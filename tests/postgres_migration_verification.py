"""Opt-in PostgreSQL 16 migration adoption and recovery verification.

The script refuses every target except the explicitly named loopback database
``coachai_migration_upgrade``. It is intentionally outside unittest discovery
because it resets that disposable database's public schema.
"""

from __future__ import annotations

import json
import os

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from migrations.schema_20261008_0001 import metadata as baseline_metadata


BASE_REVISION = "20261008_0001"
HEAD_REVISION = "20261009_0002"
RAW_URL = os.environ.get("COACHAI_MIGRATION_POSTGRES_URL", "")


def _checked_url() -> str:
    if not RAW_URL:
        raise RuntimeError("COACHAI_MIGRATION_POSTGRES_URL is required.")
    parsed = make_url(RAW_URL)
    if parsed.drivername != "postgresql+psycopg2":
        raise RuntimeError("Migration verification requires postgresql+psycopg2.")
    if (
        parsed.host not in {"127.0.0.1", "localhost"}
        or parsed.database != "coachai_migration_upgrade"
    ):
        raise RuntimeError(
            "Refusing to reset anything except "
            "localhost/coachai_migration_upgrade."
        )
    return RAW_URL


def _config(connection) -> Config:
    config = Config("alembic.ini")
    config.attributes["connection"] = connection
    return config


def _reset(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


def _revision(connection) -> str:
    return str(
        connection.execute(text("SELECT version_num FROM alembic_version"))
        .scalar_one()
    )


def _verify_revision_upgrade_and_recovery(engine) -> None:
    _reset(engine)
    with engine.begin() as connection:
        config = _config(connection)
        command.upgrade(config, BASE_REVISION)
        connection.execute(
            text(
                "INSERT INTO users "
                "(user_id,email,display_name,timezone,password_hash) "
                "VALUES ('chain-user','chain@example.test','Chain','UTC','!')"
            )
        )
        command.upgrade(config, "head")
        assert _revision(connection) == HEAD_REVISION
        assert connection.execute(
            text("SELECT display_name FROM users WHERE user_id='chain-user'")
        ).scalar_one() == "Chain"
        assert "rate_limit_buckets" in inspect(connection).get_table_names()

        # Revision 0002 owns only disposable counters, so its downgrade is a
        # supported recovery drill and must preserve all user data.
        command.downgrade(config, BASE_REVISION)
        assert _revision(connection) == BASE_REVISION
        assert "rate_limit_buckets" not in inspect(connection).get_table_names()
        assert connection.execute(
            text("SELECT COUNT(*) FROM users WHERE user_id='chain-user'")
        ).scalar_one() == 1
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        assert _revision(connection) == HEAD_REVISION
        assert "rate_limit_buckets" in inspect(connection).get_table_names()


def _create_pre_alembic_fixture(engine) -> None:
    """Create the real typed core tables, then remove known newer columns."""
    with engine.begin() as connection:
        for table_name in ("users", "categories", "plans", "tasks"):
            baseline_metadata.tables[table_name].create(connection)
        connection.execute(
            text(
                "INSERT INTO users "
                "(user_id,email,display_name,timezone,password_hash) "
                "VALUES ('legacy-user','legacy@example.test','Legacy','UTC','!')"
            )
        )
        category_id = connection.execute(
            text(
                "INSERT INTO categories (user_id,name,color) "
                "VALUES ('legacy-user','Legacy Category','#123456') "
                "RETURNING category_id"
            )
        ).scalar_one()
        plan_id = connection.execute(
            text(
                "INSERT INTO plans (user_id,plan_date,raw_input) "
                "VALUES ('legacy-user','2026-10-08','legacy input') "
                "RETURNING plan_id"
            )
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO tasks "
                "(plan_id,category_id,title,estimated_minutes,version) "
                "VALUES (:plan_id,:category_id,'Preserve me',45,0)"
            ),
            {"plan_id": plan_id, "category_id": category_id},
        )
        connection.execute(text("ALTER TABLE tasks DROP COLUMN version CASCADE"))
        connection.execute(text("ALTER TABLE users DROP COLUMN password_hash CASCADE"))
        connection.execute(text("ALTER TABLE users DROP COLUMN is_disabled CASCADE"))
        connection.execute(text("ALTER TABLE users DROP COLUMN updated_at CASCADE"))


def _verify_pre_alembic_adoption(engine) -> None:
    _reset(engine)
    _create_pre_alembic_fixture(engine)
    with engine.begin() as connection:
        config = _config(connection)
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        assert _revision(connection) == HEAD_REVISION
        inspector = inspect(connection)
        user_columns = {item["name"] for item in inspector.get_columns("users")}
        task_columns = {item["name"] for item in inspector.get_columns("tasks")}
        assert {"password_hash", "is_disabled", "updated_at"} <= user_columns
        assert "version" in task_columns
        assert connection.execute(
            text("SELECT display_name FROM users WHERE user_id='legacy-user'")
        ).scalar_one() == "Legacy"
        row = connection.execute(
            text("SELECT title,version FROM tasks WHERE title='Preserve me'")
        ).one()
        assert row.title == "Preserve me" and int(row.version) == 0
        assert set(baseline_metadata.tables) <= set(inspector.get_table_names())
        assert "rate_limit_buckets" in inspector.get_table_names()
        user_checks = {
            item["name"] for item in inspector.get_check_constraints("users")
        }
        task_checks = {
            item["name"] for item in inspector.get_check_constraints("tasks")
        }
        assert "ck_users_disabled" in user_checks
        assert "ck_tasks_version" in task_checks


def main() -> None:
    engine = create_engine(_checked_url(), future=True)
    try:
        with engine.connect() as connection:
            server_version = connection.execute(
                text("SHOW server_version")
            ).scalar_one()
        assert str(server_version) == "16.15"
        _verify_revision_upgrade_and_recovery(engine)
        _verify_pre_alembic_adoption(engine)
        print(json.dumps({
            "postgresql_server": server_version,
            "revision_upgrade": True,
            "reversible_counter_rollback": True,
            "idempotent_upgrade": True,
            "pre_alembic_rows_preserved": True,
            "head": HEAD_REVISION,
        }, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
