"""Focused, database-local verification for the Alembic foundation."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from migrations.schema_20261008_0001 import metadata as revision_metadata


# Schema comparison must not read local credentials. The database module only
# needs dotenv during runtime configuration, not while exporting metadata.
with patch("dotenv.load_dotenv", return_value=False):
    from database import metadata as application_metadata


ROOT = Path(__file__).resolve().parents[1]
BASE_REVISION = "20261008_0001"
REVISION = "20261009_0002"


def _alembic_config(connection=None) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def _column_signature(column) -> tuple:
    foreign_keys = tuple(
        sorted(
            (
                foreign_key.target_fullname,
                foreign_key.ondelete,
                foreign_key.onupdate,
            )
            for foreign_key in column.foreign_keys
        )
    )
    default = None
    if column.server_default is not None:
        default = str(column.server_default.arg).strip()
    return (
        column.name,
        column.type.__class__.__name__,
        bool(column.nullable),
        bool(column.primary_key),
        default,
        foreign_keys,
    )


def _constraint_signature(table) -> tuple:
    constraints = []
    for constraint in table.constraints:
        kind = constraint.__class__.__name__
        columns = tuple(column.name for column in constraint.columns)
        expression = None
        if kind == "CheckConstraint":
            expression = str(constraint.sqltext)
        constraints.append((kind, constraint.name, columns, expression))
    return tuple(sorted(constraints, key=repr))


def _index_signature(table) -> tuple:
    return tuple(
        sorted(
            (
                index.name,
                bool(index.unique),
                tuple(column.name for column in index.columns),
            )
            for index in table.indexes
        )
    )


def _metadata_signature(metadata) -> dict[str, tuple]:
    return {
        table_name: (
            tuple(_column_signature(column) for column in table.columns),
            _constraint_signature(table),
            _index_signature(table),
        )
        for table_name, table in sorted(metadata.tables.items())
    }


class MigrationFoundationTests(unittest.TestCase):
    def test_migration_logging_preserves_application_loggers(self) -> None:
        source = (ROOT / "migrations" / "env.py").read_text(encoding="utf-8")
        self.assertIn("disable_existing_loggers=False", source)

    def test_new_schema_is_owned_by_follow_up_revision(self):
        live = _metadata_signature(application_metadata)
        baseline = _metadata_signature(revision_metadata)
        self.assertEqual(set(live) - set(baseline), {"rate_limit_buckets"})
        for table_name, signature in baseline.items():
            self.assertEqual(live[table_name], signature)

    def test_revision_chain_has_one_expected_head(self):
        script = ScriptDirectory.from_config(_alembic_config())
        self.assertEqual(script.get_heads(), [REVISION])
        revision = script.get_revision(REVISION)
        self.assertEqual(revision.down_revision, BASE_REVISION)
        self.assertIsNone(script.get_revision(BASE_REVISION).down_revision)

    def test_fresh_upgrade_creates_canonical_schema_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "fresh.sqlite3"
            engine = create_engine(f"sqlite:///{path}")
            with engine.connect() as connection:
                config = _alembic_config(connection)
                command.upgrade(config, "head")
                # Running the same target again must not re-execute mutations.
                command.upgrade(config, "head")

                inspector = inspect(connection)
                expected_tables = set(application_metadata.tables)
                self.assertTrue(expected_tables.issubset(inspector.get_table_names()))
                for table_name, table in application_metadata.tables.items():
                    actual_columns = {
                        column["name"] for column in inspector.get_columns(table_name)
                    }
                    self.assertEqual(actual_columns, set(table.columns.keys()))

                current_revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                self.assertEqual(current_revision, REVISION)
            engine.dispose()

    def test_legacy_upgrade_adds_known_columns_and_preserves_rows(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "legacy.sqlite3"
            engine = create_engine(f"sqlite:///{path}")
            with engine.begin() as connection:
                user_columns = [
                    column.name
                    for column in revision_metadata.tables["users"].columns
                    if column.name
                    not in {"password_hash", "is_disabled", "updated_at"}
                ]
                task_columns = [
                    column.name
                    for column in revision_metadata.tables["tasks"].columns
                    if column.name != "version"
                ]
                connection.execute(
                    text(
                        "CREATE TABLE users ("
                        + ", ".join(f'\"{name}\" TEXT' for name in user_columns)
                        + ")"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO users (user_id, email, display_name, timezone) "
                        "VALUES ('legacy-user', 'legacy@example.test', 'Legacy', 'UTC')"
                    )
                )
                connection.execute(
                    text(
                        "CREATE TABLE tasks ("
                        + ", ".join(f'\"{name}\" TEXT' for name in task_columns)
                        + ")"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO tasks (task_id, plan_id, title) "
                        "VALUES ('7', '3', 'Preserve me')"
                    )
                )

            with engine.connect() as connection:
                command.upgrade(_alembic_config(connection), "head")
                inspector = inspect(connection)
                user_column_names = {
                    column["name"] for column in inspector.get_columns("users")
                }
                task_column_names = {
                    column["name"] for column in inspector.get_columns("tasks")
                }
                self.assertTrue(
                    {"password_hash", "is_disabled", "updated_at"}.issubset(
                        user_column_names
                    )
                )
                self.assertIn("version", task_column_names)
                self.assertEqual(
                    connection.execute(
                        text("SELECT display_name FROM users WHERE user_id='legacy-user'")
                    ).scalar_one(),
                    "Legacy",
                )
                self.assertEqual(
                    connection.execute(
                        text("SELECT title FROM tasks WHERE task_id='7'")
                    ).scalar_one(),
                    "Preserve me",
                )
                self.assertEqual(
                    int(
                        connection.execute(
                            text("SELECT version FROM tasks WHERE task_id='7'")
                        ).scalar_one()
                    ),
                    0,
                )
            engine.dispose()

    def test_migration_environment_requires_an_explicit_database(self):
        config_text = (ROOT / "alembic.ini").read_text(encoding="utf-8")
        self.assertIn("required-via-DATABASE_URL", config_text)
        self.assertNotIn("sqlite:///", config_text)

    def test_revision_never_uses_metadata_create_all(self):
        forbidden = "metadata" + ".create_all("
        baseline_source = (
            ROOT / "migrations" / "versions" / "20261008_0001_canonical_schema.py"
        ).read_text(encoding="utf-8")
        rate_limit_source = (
            ROOT / "migrations" / "versions" / "20261009_0002_shared_rate_limits.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn(forbidden, baseline_source)
        self.assertNotIn(forbidden, rate_limit_source)
        self.assertIn("cannot be downgraded safely", baseline_source)
        self.assertIn("op.drop_table", rate_limit_source)


if __name__ == "__main__":
    unittest.main()
