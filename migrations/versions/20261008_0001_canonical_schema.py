"""Establish the canonical CoachAI schema without replacing existing data.

Revision ID: 20261008_0001
Revises: None
Create Date: 2026-10-08

The revision supports both a fresh database and the schema that predates the
mobile-core work.  Existing tables are never dropped or renamed.  Known new
columns are added conditionally, missing tables/indexes are created, and an
unexpected partially-created legacy schema fails closed instead of being
silently stamped as current.
"""

from __future__ import annotations

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa

from migrations.schema_20261008_0001 import metadata as canonical_metadata


revision: str = "20261008_0001"
down_revision: Optional[str] = None
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _existing_tables() -> set[str]:
    return set(_inspector().get_table_names())


def _existing_columns(table_name: str) -> set[str]:
    return {column["name"] for column in _inspector().get_columns(table_name)}


def _add_known_upgrade_columns() -> None:
    """Add columns introduced after the pre-Alembic PostgreSQL schema."""

    tables = _existing_tables()
    dialect = op.get_bind().dialect.name

    if "users" in tables:
        columns = _existing_columns("users")
        if "password_hash" not in columns:
            op.add_column("users", sa.Column("password_hash", sa.Text(), nullable=True))
        if "is_disabled" not in columns:
            op.add_column(
                "users",
                sa.Column(
                    "is_disabled",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("0"),
                ),
            )
        if "updated_at" not in columns:
            if dialect == "sqlite":
                # SQLite cannot ALTER ADD a non-constant CURRENT_TIMESTAMP
                # default. SQLite is only a compatibility/test backend; the
                # canonical PostgreSQL path below retains the exact default.
                op.add_column(
                    "users", sa.Column("updated_at", sa.DateTime(), nullable=True)
                )
                op.execute(
                    sa.text(
                        "UPDATE users SET updated_at = CURRENT_TIMESTAMP "
                        "WHERE updated_at IS NULL"
                    )
                )
            else:
                op.add_column(
                    "users",
                    sa.Column(
                        "updated_at",
                        sa.DateTime(),
                        nullable=False,
                        server_default=sa.text("CURRENT_TIMESTAMP"),
                    ),
                )

    if "tasks" in tables and "version" not in _existing_columns("tasks"):
        op.add_column(
            "tasks",
            sa.Column(
                "version",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )


def _create_missing_tables() -> None:
    """Create each absent table in dependency order.

    This intentionally uses version-frozen ``Table.create`` calls inside the
    Alembic revision. It does not call ``MetaData.create_all`` and therefore
    does not turn mutable application metadata into a migration mechanism.
    """

    bind = op.get_bind()
    existing = _existing_tables()
    for table in canonical_metadata.sorted_tables:
        if table.name not in existing:
            table.create(bind, checkfirst=True)
            existing.add(table.name)


def _create_missing_indexes() -> None:
    bind = op.get_bind()
    inspector = _inspector()
    tables = set(inspector.get_table_names())
    for table in canonical_metadata.sorted_tables:
        if table.name not in tables:
            continue
        existing = {item["name"] for item in inspector.get_indexes(table.name)}
        for index in sorted(table.indexes, key=lambda item: item.name or ""):
            if index.name and index.name not in existing:
                index.create(bind, checkfirst=True)


def _create_mobile_column_checks() -> None:
    """Add the two named checks introduced with mobile-core versioning."""

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # Fresh SQLite databases get these checks from the frozen metadata.
        # Rebuilding populated compatibility databases merely to add checks is
        # deliberately outside this canonical PostgreSQL migration.
        return

    inspector = _inspector()
    tables = set(inspector.get_table_names())
    expected = {
        "users": ("ck_users_disabled", "is_disabled IN (0, 1)"),
        "tasks": ("ck_tasks_version", "version >= 0"),
    }
    for table_name, (constraint_name, condition) in expected.items():
        if table_name not in tables:
            continue
        names = {
            constraint.get("name")
            for constraint in inspector.get_check_constraints(table_name)
        }
        if constraint_name not in names:
            op.create_check_constraint(constraint_name, table_name, condition)


def _assert_column_parity() -> None:
    """Fail closed if a legacy database is missing an unknown core column."""

    tables = _existing_tables()
    missing_tables = set(canonical_metadata.tables) - tables
    if missing_tables:
        raise RuntimeError(
            "Canonical schema migration did not create tables: "
            + ", ".join(sorted(missing_tables))
        )

    mismatches: list[str] = []
    for table_name, table in canonical_metadata.tables.items():
        missing_columns = set(table.columns.keys()) - _existing_columns(table_name)
        if missing_columns:
            mismatches.append(
                f"{table_name} missing {', '.join(sorted(missing_columns))}"
            )
    if mismatches:
        raise RuntimeError(
            "Refusing to stamp a structurally incomplete database: "
            + "; ".join(mismatches)
        )


def upgrade() -> None:
    _add_known_upgrade_columns()
    _create_missing_tables()
    _create_missing_indexes()
    _create_mobile_column_checks()
    _assert_column_parity()


def downgrade() -> None:
    raise RuntimeError(
        "Revision 20261008_0001 is a non-destructive adoption baseline and "
        "cannot be downgraded safely: Alembic cannot distinguish pre-existing "
        "tables from tables created on a fresh database. Restore the verified "
        "pre-migration backup instead."
    )
