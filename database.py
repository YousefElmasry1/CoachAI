import os
import re
import sqlite3  # kept only for type hints (sqlite3.Row / sqlite3.Cursor) below —
                # the actual connection no longer uses this module directly.
from contextlib import contextmanager
from datetime import date, datetime, timezone
from itertools import count
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import (
    create_engine, text, inspect,
    MetaData, Table, Column, Integer, Float, String, Text,
    DateTime, Date, Time, ForeignKey, UniqueConstraint, CheckConstraint,
    Index, func,
)
from sqlalchemy.engine import Connection, CursorResult, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from timezone_utils import local_hour_from_utc_string


# ---------------------------------------------------------------------------
# Schema — dialect-agnostic table definitions
#
# This is the SQLAlchemy equivalent of schema.sql. It's what makes a FRESH
# Postgres database creatable directly (metadata.create_all(engine)) —
# instead of running the SQLite-only schema.sql text file. An existing
# SQLite database keeps using schema.sql + the self-healing migrations
# below, exactly as before, so nothing changes for local development.
# ---------------------------------------------------------------------------

metadata = MetaData()

users = Table(
    "users", metadata,
    Column("user_id", Integer, primary_key=True, autoincrement=True),
    Column("email", String, nullable=False, unique=True),
    Column("password_hash", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("timezone", String, nullable=False, server_default="UTC"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
)
Index("idx_users_email", users.c.email)

categories = Table(
    "categories", metadata,
    Column("category_id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("name", String, nullable=False),
    Column("color", String, nullable=False, server_default="#3B82F6"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("user_id", "name"),
)
Index("idx_categories_user_id", categories.c.user_id)

plans = Table(
    "plans", metadata,
    Column("plan_id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("plan_date", Date, nullable=False),
    Column("raw_input", Text, nullable=False),
    Column("ai_summary", Text),
    Column("status", String, nullable=False, server_default="active"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("user_id", "plan_date"),
    CheckConstraint("status IN ('draft','active','completed')", name="ck_plans_status"),
)
Index("idx_plans_user_id", plans.c.user_id)
Index("idx_plans_date", plans.c.plan_date)
Index("idx_plans_user_date", plans.c.user_id, plans.c.plan_date)

tasks = Table(
    "tasks", metadata,
    Column("task_id", Integer, primary_key=True, autoincrement=True),
    Column("plan_id", Integer, ForeignKey("plans.plan_id", ondelete="CASCADE"), nullable=False),
    Column("category_id", Integer, ForeignKey("categories.category_id", ondelete="SET NULL")),
    Column("title", String, nullable=False),
    Column("description", Text),
    Column("priority", Integer, nullable=False, server_default="3"),
    Column("estimated_minutes", Integer, nullable=False, server_default="30"),
    Column("scheduled_start", Time),
    Column("scheduled_end", Time),
    Column("is_fixed_time", Integer, nullable=False, server_default="0"),
    Column("is_break", Integer, nullable=False, server_default="0"),
    Column("order_index", Integer, nullable=False, server_default="0"),
    Column("status", String, nullable=False, server_default="pending"),
    Column("failure_reason", String),
    Column("actual_minutes", Integer),
    Column("started_at", DateTime),
    Column("completed_at", DateTime),
    Column("timer_accumulated_seconds", Integer, nullable=False, server_default="0"),
    Column("timer_segment_started_at", DateTime),
    Column("pause_count", Integer, nullable=False, server_default="0"),
    Column("paused_at", DateTime),
    Column("timer_total_paused_seconds", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("google_event_id", String),
    Column("google_exported_at", DateTime),
    CheckConstraint("priority BETWEEN 1 AND 5", name="ck_tasks_priority"),
    CheckConstraint("estimated_minutes >= 0", name="ck_tasks_est_minutes"),
    CheckConstraint("status IN ('pending','in_progress','completed','failed')", name="ck_tasks_status"),
    CheckConstraint(
        "failure_reason IN ('Harder than expected','Distracted','Tired',"
        "'Unexpected event','Changed priorities','Ran out of time')",
        name="ck_tasks_failure_reason",
    ),
    CheckConstraint("actual_minutes >= 0", name="ck_tasks_actual_minutes"),
    CheckConstraint("is_fixed_time IN (0, 1)", name="ck_tasks_is_fixed_time"),
    CheckConstraint("is_break IN (0, 1)", name="ck_tasks_is_break"),
)
Index("idx_tasks_plan_id", tasks.c.plan_id)
Index("idx_tasks_category_id", tasks.c.category_id)
Index("idx_tasks_status", tasks.c.status)
Index("idx_tasks_plan_status", tasks.c.plan_id, tasks.c.status)

user_profiles = Table(
    "user_profiles", metadata,
    Column("profile_id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, unique=True),
    Column("completion_rate", Float, nullable=False, server_default="0.0"),
    Column("productivity_score", Float, nullable=False, server_default="0.0"),
    Column("best_productivity_hour", Integer),
    Column("avg_delay_minutes", Float, nullable=False, server_default="0.0"),
    Column("main_failure_reason", String),
    Column("favorite_category_id", Integer, ForeignKey("categories.category_id", ondelete="SET NULL")),
    Column("current_streak", Integer, nullable=False, server_default="0"),
    Column("longest_streak", Integer, nullable=False, server_default="0"),
    Column("total_completed", Integer, nullable=False, server_default="0"),
    Column("total_failed", Integer, nullable=False, server_default="0"),
    Column("total_tasks", Integer, nullable=False, server_default="0"),
    Column("last_updated", DateTime, nullable=False, server_default=func.current_timestamp()),
    CheckConstraint("completion_rate BETWEEN 0 AND 1", name="ck_profiles_completion_rate"),
    CheckConstraint("productivity_score BETWEEN 0 AND 100", name="ck_profiles_productivity_score"),
    CheckConstraint("best_productivity_hour BETWEEN 0 AND 23", name="ck_profiles_hour"),
    CheckConstraint("avg_delay_minutes >= 0", name="ck_profiles_avg_delay"),
    CheckConstraint("current_streak >= 0", name="ck_profiles_current_streak"),
    CheckConstraint("longest_streak >= 0", name="ck_profiles_longest_streak"),
    CheckConstraint("total_completed >= 0", name="ck_profiles_total_completed"),
    CheckConstraint("total_failed >= 0", name="ck_profiles_total_failed"),
    CheckConstraint("total_tasks >= 0", name="ck_profiles_total_tasks"),
)
Index("idx_profiles_user_id", user_profiles.c.user_id)

badges = Table(
    "badges", metadata,
    Column("badge_id", Integer, primary_key=True, autoincrement=True),
    Column("name", String, nullable=False, unique=True),
    Column("description", String, nullable=False),
    Column("icon", String),
    Column("requirement_type", String, nullable=False),
    Column("requirement_value", Integer, nullable=False),
    CheckConstraint("requirement_type IN ('streak','count','rate')", name="ck_badges_req_type"),
    CheckConstraint("requirement_value >= 0", name="ck_badges_req_value"),
)

user_badges = Table(
    "user_badges", metadata,
    Column("user_badge_id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("badge_id", Integer, ForeignKey("badges.badge_id", ondelete="CASCADE"), nullable=False),
    Column("earned_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("user_id", "badge_id"),
)
Index("idx_user_badges_user_id", user_badges.c.user_id)

google_oauth_tokens = Table(
    "google_oauth_tokens", metadata,
    Column("token_id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, unique=True),
    Column("access_token", Text, nullable=False),
    Column("refresh_token", Text, nullable=False),
    Column("token_expiry", DateTime, nullable=False),
    Column("scopes", String, nullable=False, server_default=""),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
)

google_selected_calendars = Table(
    "google_selected_calendars", metadata,
    Column("selection_id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("calendar_id", String, nullable=False),
    Column("calendar_name", String, nullable=False, server_default=""),
    Column("color", String, nullable=False, server_default="#4285F4"),
    Column("is_primary", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("user_id", "calendar_id"),
)
Index("idx_gcal_selected_user", google_selected_calendars.c.user_id)

google_calendar_events = Table(
    "google_calendar_events", metadata,
    Column("event_id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("google_event_id", String, nullable=False),
    Column("title", String, nullable=False, server_default=""),
    Column("start_time", Time, nullable=False),
    Column("end_time", Time, nullable=False),
    Column("event_date", Date, nullable=False),
    Column("calendar_id", String, nullable=False),
    Column("last_synced_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("user_id", "google_event_id", "event_date"),
)
Index("idx_gcal_events_user_date", google_calendar_events.c.user_id, google_calendar_events.c.event_date)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_NAME: str = "coach_ai.db"
SCHEMA_FILE: str = "schema.sql"

# Set DATABASE_URL to point at Postgres in production, e.g.:
#   postgresql+psycopg2://user:password@host:5432/coachai
# Left unset (the default), CoachAI keeps using a local SQLite file — no
# other code in this file, or anywhere else in the project, needs to change
# to switch between the two.
DEFAULT_SQLITE_PATH: str = os.path.abspath(DB_NAME)
DATABASE_URL: str = os.environ.get(
    "DATABASE_URL", f"sqlite:///{DEFAULT_SQLITE_PATH}"
)


# ---------------------------------------------------------------------------
# Database Class
# ---------------------------------------------------------------------------

class Database:
    """
    Database manager for CoachAI, backed by SQLAlchemy.

    Talks to SQLite by default (for local development) or Postgres when
    DATABASE_URL is set (for production). Every method below this class's
    connection-management section is UNCHANGED from the original sqlite3
    version — same names, same SQL, same ``?`` placeholders — because the
    low-level execute()/fetch_one()/fetch_all() helpers now translate
    everything through SQLAlchemy under the hood.

    Attributes:
        engine: SQLAlchemy Engine (connection pool + dialect).
        connection: Active SQLAlchemy Connection instance.
    """

    def __init__(self, db_url: Optional[str] = None) -> None:
        """
        Initialise the database manager.

        Args:
            db_url: Optional SQLAlchemy connection URL. Defaults to
                    DATABASE_URL (env var), which itself defaults to a
                    local ``coach_ai.db`` SQLite file.
        """
        self.db_url: str = db_url or DATABASE_URL
        self.engine: Optional[Engine] = None
        self.connection: Optional[Connection] = None
        self._connect()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        """Open the engine/connection and enable foreign keys (SQLite)."""
        self.engine = create_engine(self.db_url, future=True)
        self.connection = self.engine.connect()
        if self.is_sqlite:
            self.connection.execute(text("PRAGMA foreign_keys = ON"))
            self.connection.commit()
        self._maybe_create_schema()

    @property
    def is_sqlite(self) -> bool:
        """True when this Database is currently talking to SQLite."""
        return self.engine.dialect.name == "sqlite"

    def _raw_dbapi(self) -> Any:
        """
        Return the underlying DBAPI connection (sqlite3.Connection,
        psycopg2 connection, ...) for the handful of operations
        SQLAlchemy Core has no portable equivalent for (PRAGMA,
        executescript). Only ever called on the SQLite path.
        """
        return self.connection.connection

    def close(self) -> None:
        """Safely close the database connection."""
        if self.connection:
            try:
                self.connection.close()
            except SQLAlchemyError:
                pass
            finally:
                self.connection = None

    def __enter__(self) -> "Database":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit – closes connection cleanly."""
        self.close()

    # ------------------------------------------------------------------
    # Schema initialisation
    #
    # NOTE: schema.sql and every _maybe_migrate_* helper below are still
    # SQLite-specific (AUTOINCREMENT, PRAGMA, sqlite_master...), exactly
    # as they were before this conversion. They're now guarded to only
    # run on the SQLite path. Creating/migrating the Postgres schema
    # (via SQLAlchemy Table objects + Alembic) is the next follow-up
    # piece of work, not something this pass silently papers over.
    # ------------------------------------------------------------------

    def _maybe_create_schema(self) -> None:
        """
        Create the schema if the database is empty (no tables yet).

        - SQLite: keeps using schema.sql + the self-healing migrations
          below, unchanged from before — this is what your existing
          coach_ai.db already went through, so it keeps working exactly
          as-is.
        - Any other dialect (Postgres in production): a fresh database
          is created directly from the ``metadata`` Table objects above
          via ``metadata.create_all()``. Those definitions already
          include every column the SQLite migrations add over time
          (e.g. ``google_event_id``, the updated failure_reason CHECK),
          so a new Postgres database starts at the final schema and
          never needs to run the SQLite migration methods at all.
        """
        if not self.is_sqlite:
            inspector = inspect(self.engine)
            if not inspector.get_table_names():
                metadata.create_all(self.engine)
            return

        cursor = self.connection.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1")
        )
        if cursor.fetchone() is not None:
            self._maybe_migrate_started_at()
            self._maybe_migrate_failure_reason_check()
            self._maybe_create_google_tables()
            self._maybe_migrate_task_google_event_id()
            self._maybe_migrate_task_is_fixed_time()
            self._maybe_migrate_task_google_exported_at()
            self._maybe_migrate_task_is_break()
            self._maybe_migrate_task_timer_fields()
            self._maybe_migrate_task_pause_count()
            self._maybe_migrate_task_pause_duration_fields()
            return  # Schema already applied

        schema_path = Path(__file__).with_name(SCHEMA_FILE)
        if not schema_path.exists():
            raise FileNotFoundError(
                f"Schema file not found: {schema_path}"
            )

        with open(schema_path, "r", encoding="utf-8") as f:
            self._raw_dbapi().executescript(f.read())
        self.connection.commit()

        # schema.sql only has the columns baked in from day one
        # (is_fixed_time). Everything added later purely via migration
        # (google_event_id, google_exported_at, the Google Calendar
        # tables) still needs to run once here too, so a BRAND NEW
        # SQLite database ends up at the same final shape as one that
        # started earlier and migrated forward.
        self._maybe_migrate_started_at()
        self._maybe_migrate_failure_reason_check()
        self._maybe_create_google_tables()
        self._maybe_migrate_task_google_event_id()
        self._maybe_migrate_task_is_fixed_time()
        self._maybe_migrate_task_google_exported_at()
        self._maybe_migrate_task_is_break()
        self._maybe_migrate_task_timer_fields()
        self._maybe_migrate_task_pause_count()
        self._maybe_migrate_task_pause_duration_fields()

    def _maybe_migrate_started_at(self) -> None:
        """
        Add the ``started_at`` column to an existing ``tasks`` table if it
        doesn't have it yet. SQLite-only self-healing migration.
        """
        cursor = self.connection.execute(text("PRAGMA table_info(tasks)"))
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "started_at" not in existing_columns:
            self.connection.execute(
                text("ALTER TABLE tasks ADD COLUMN started_at DATETIME")
            )
            self.connection.commit()

    def _maybe_migrate_failure_reason_check(self) -> None:
        """
        Rebuild the ``tasks`` table if its ``failure_reason`` CHECK
        constraint doesn't yet include ``'Ran out of time'``. SQLite-only
        (Postgres CHECK constraints CAN be altered in place with ALTER
        TABLE ... DROP/ADD CONSTRAINT, so this rebuild dance won't be
        needed there at all).
        """
        cursor = self.connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'")
        )
        row = cursor.fetchone()
        if row is None or row[0] is None or "Ran out of time" in row[0]:
            return  # Already up to date (or table doesn't exist yet)

        raw = self._raw_dbapi()
        raw.execute("PRAGMA foreign_keys = OFF")
        try:
            raw.execute("BEGIN")
            raw.execute("ALTER TABLE tasks RENAME TO tasks_old")
            raw.execute(
                """
                CREATE TABLE tasks (
                    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id INTEGER NOT NULL,
                    category_id INTEGER,
                    title TEXT NOT NULL,
                    description TEXT,
                    priority INTEGER NOT NULL DEFAULT 3
                        CHECK (priority BETWEEN 1 AND 5),
                    estimated_minutes INTEGER NOT NULL DEFAULT 30
                        CHECK (estimated_minutes >= 0),
                    scheduled_start TIME,
                    scheduled_end TIME,
                    order_index INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'in_progress', 'completed', 'failed')),
                    failure_reason TEXT
                        CHECK (failure_reason IN (
                            'Harder than expected',
                            'Distracted',
                            'Tired',
                            'Unexpected event',
                            'Changed priorities',
                            'Ran out of time'
                        )),
                    actual_minutes INTEGER
                        CHECK (actual_minutes >= 0),
                    started_at DATETIME,
                    completed_at DATETIME,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

                    FOREIGN KEY (plan_id) REFERENCES plans(plan_id) ON DELETE CASCADE,
                    FOREIGN KEY (category_id) REFERENCES categories(category_id) ON DELETE SET NULL
                )
                """
            )
            raw.execute(
                """
                INSERT INTO tasks (
                    task_id, plan_id, category_id, title, description, priority,
                    estimated_minutes, scheduled_start, scheduled_end, order_index,
                    status, failure_reason, actual_minutes, started_at, completed_at,
                    created_at, updated_at
                )
                SELECT
                    task_id, plan_id, category_id, title, description, priority,
                    estimated_minutes, scheduled_start, scheduled_end, order_index,
                    status, failure_reason, actual_minutes, started_at, completed_at,
                    created_at, updated_at
                FROM tasks_old
                """
            )
            raw.execute("DROP TABLE tasks_old")
            raw.execute("CREATE INDEX idx_tasks_plan_id ON tasks(plan_id)")
            raw.execute("CREATE INDEX idx_tasks_category_id ON tasks(category_id)")
            raw.execute("CREATE INDEX idx_tasks_status ON tasks(status)")
            raw.execute("CREATE INDEX idx_tasks_plan_status ON tasks(plan_id, status)")
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.execute("PRAGMA foreign_keys = ON")

    def _maybe_create_google_tables(self) -> None:
        """Create Google Calendar tables if they don't exist (idempotent)."""
        self._raw_dbapi().executescript("""
            CREATE TABLE IF NOT EXISTS google_oauth_tokens (
                token_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL UNIQUE,
                access_token  TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                token_expiry  DATETIME NOT NULL,
                scopes        TEXT NOT NULL DEFAULT '',
                created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS google_selected_calendars (
                selection_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                calendar_id   TEXT NOT NULL,
                calendar_name TEXT NOT NULL DEFAULT '',
                color         TEXT NOT NULL DEFAULT '#4285F4',
                is_primary    INTEGER NOT NULL DEFAULT 0,
                created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                UNIQUE(user_id, calendar_id)
            );

            CREATE TABLE IF NOT EXISTS google_calendar_events (
                event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                google_event_id TEXT NOT NULL,
                title           TEXT NOT NULL DEFAULT '',
                start_time      TIME NOT NULL,
                end_time        TIME NOT NULL,
                event_date      DATE NOT NULL,
                calendar_id     TEXT NOT NULL,
                last_synced_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                UNIQUE(user_id, google_event_id, event_date)
            );

            CREATE INDEX IF NOT EXISTS idx_gcal_events_user_date
                ON google_calendar_events(user_id, event_date);
            CREATE INDEX IF NOT EXISTS idx_gcal_selected_user
                ON google_selected_calendars(user_id);
        """)
        self.connection.commit()

    def _maybe_migrate_task_google_event_id(self) -> None:
        """Add google_event_id column to tasks if missing."""
        cursor = self.connection.execute(text("PRAGMA table_info(tasks)"))
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "google_event_id" not in existing_columns:
            self.connection.execute(
                text("ALTER TABLE tasks ADD COLUMN google_event_id TEXT")
            )
            self.connection.commit()

    def _maybe_migrate_task_is_fixed_time(self) -> None:
        """
        Add the ``is_fixed_time`` column to an existing ``tasks`` table if
        it doesn't have it yet. This is what lets the app tell a task the
        AI/user pinned to a real clock time (e.g. a fixed 2:00 PM meeting)
        apart from a task the Scheduler is free to move — without it,
        every task looked the same once scheduled_start was set, whether
        it was fixed on purpose or just previously auto-scheduled.

        Existing rows default to 0 (flexible), since there is no reliable
        way to infer which already-scheduled tasks were originally meant
        to be fixed.
        """
        cursor = self.connection.execute(text("PRAGMA table_info(tasks)"))
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "is_fixed_time" not in existing_columns:
            self.connection.execute(
                text(
                    "ALTER TABLE tasks ADD COLUMN is_fixed_time "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            )
            self.connection.commit()

    def _maybe_migrate_task_google_exported_at(self) -> None:
        """
        Add the ``google_exported_at`` column to an existing ``tasks``
        table if it doesn't have it yet. This records when a task was
        last pushed to Google Calendar, so the app can tell a stale
        export (task rescheduled after it was exported) apart from one
        that's already in sync — without it, there was no way to warn
        the user that Google Calendar still holds an old time.
        """
        cursor = self.connection.execute(text("PRAGMA table_info(tasks)"))
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "google_exported_at" not in existing_columns:
            self.connection.execute(
                text("ALTER TABLE tasks ADD COLUMN google_exported_at DATETIME")
            )
            self.connection.commit()

    def _maybe_migrate_task_is_break(self) -> None:
        """
        Add the ``is_break`` column to an existing ``tasks`` table if it
        doesn't have it yet. This is what turns a break from a purely
        in-memory scheduling hint (gone the moment the page reloads)
        into a real, persisted task row — one the Scheduler already
        treats as an immovable fixed-time block (via is_fixed_time),
        and that the UI can show in the task list, start a timer on,
        and mark completed, exactly like any other task.

        Existing rows default to 0 (not a break), since there is no
        reliable way to infer which already-scheduled tasks were
        originally meant to represent a break.
        """
        cursor = self.connection.execute(text("PRAGMA table_info(tasks)"))
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "is_break" not in existing_columns:
            self.connection.execute(
                text(
                    "ALTER TABLE tasks ADD COLUMN is_break "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            )
            self.connection.commit()

    def _maybe_migrate_task_timer_fields(self) -> None:
        """
        Add ``timer_accumulated_seconds`` and ``timer_segment_started_at``
        to an existing ``tasks`` table if missing. Together these let a
        task's timer be paused and resumed without losing accuracy:

        - timer_accumulated_seconds: total active seconds banked from
          every PREVIOUS run segment (i.e. excludes any time currently
          paused, and excludes the segment still in progress).
        - timer_segment_started_at: when the CURRENT active segment
          began. NULL means the timer is currently paused (or the task
          was never started) — there's deliberately no separate
          "paused" status; a task stays 'in_progress' the whole time,
          and pause/resume just toggles whether a segment is running.

        Existing rows default to 0 / NULL, which is exactly correct for
        a task that predates this feature (no time banked, no segment
        running) — it behaves as if freshly started once someone
        presses Start.
        """
        cursor = self.connection.execute(text("PRAGMA table_info(tasks)"))
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "timer_accumulated_seconds" not in existing_columns:
            self.connection.execute(
                text(
                    "ALTER TABLE tasks ADD COLUMN timer_accumulated_seconds "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            )
            self.connection.commit()
        if "timer_segment_started_at" not in existing_columns:
            self.connection.execute(
                text("ALTER TABLE tasks ADD COLUMN timer_segment_started_at DATETIME")
            )
            self.connection.commit()

    def _maybe_migrate_task_pause_count(self) -> None:
        """
        Add the ``pause_count`` column to an existing ``tasks`` table if
        missing. Incremented once per pause (see
        Database.increment_task_pause_count / services.pause_task_timer)
        and read by get_pause_matrix() to build the Category × Time-of-
        day focus matrix — a per-task counter, not derived from the
        timer segment fields, since those only track total banked time,
        not how many times a task was interrupted.
        """
        cursor = self.connection.execute(text("PRAGMA table_info(tasks)"))
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "pause_count" not in existing_columns:
            self.connection.execute(
                text("ALTER TABLE tasks ADD COLUMN pause_count INTEGER NOT NULL DEFAULT 0")
            )
            self.connection.commit()

    def _maybe_migrate_task_pause_duration_fields(self) -> None:
        """
        Add ``paused_at`` and ``timer_total_paused_seconds`` to an
        existing ``tasks`` table if missing. pause_count alone only says
        HOW MANY TIMES a task was interrupted — these two add HOW LONG
        it stayed paused each time, which matters a lot for analysis:
        five short 30-second pauses (a bit of fidgeting) reads very
        differently from one 40-minute pause (a real interruption or
        context switch), even though pause_count is 5 either way.

        - paused_at: when the CURRENT pause began. NULL whenever the
          timer isn't paused right now (running, not yet started, or
          already finished).
        - timer_total_paused_seconds: cumulative total of every FINISHED
          pause's duration for this task (the currently-open pause, if
          any, is added on top of this at read time — see
          services.get_task_paused_seconds — not stored until it ends).
        """
        cursor = self.connection.execute(text("PRAGMA table_info(tasks)"))
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "paused_at" not in existing_columns:
            self.connection.execute(
                text("ALTER TABLE tasks ADD COLUMN paused_at DATETIME")
            )
            self.connection.commit()
        if "timer_total_paused_seconds" not in existing_columns:
            self.connection.execute(
                text(
                    "ALTER TABLE tasks ADD COLUMN timer_total_paused_seconds "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            )
            self.connection.commit()

    # ------------------------------------------------------------------
    # Low-level query helpers
    #
    # These are the ONLY methods that changed behind the scenes. Every
    # domain method below (create_user, get_tasks_by_plan, ...) still
    # calls self.execute(sql, params) / self.fetch_one(...) / etc. with
    # the exact same "?"-style SQL as before — nothing below this point
    # needed to change at all.
    # ------------------------------------------------------------------

    @staticmethod
    def _to_named(sql: str, parameters: tuple[Any, ...]) -> tuple[Any, dict[str, Any]]:
        """
        Translate sqlite3-style positional "?" placeholders (and a plain
        tuple of values) into SQLAlchemy's dialect-agnostic named-param
        style, so the exact same SQL string works against SQLite or
        Postgres without every call site needing to change.
        """
        if not parameters:
            return text(sql), {}

        counter = count(1)
        param_names: list[str] = []

        def _replace(_match: "re.Match[str]") -> str:
            name = f"p{next(counter)}"
            param_names.append(name)
            return f":{name}"

        named_sql = re.sub(r"\?", _replace, sql)
        param_dict = {name: value for name, value in zip(param_names, parameters)}
        return text(named_sql), param_dict

    def execute(
        self,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ) -> CursorResult:
        """
        Execute a single SQL statement with parameters.

        Args:
            sql: Parameterised SQL statement (sqlite3-style "?" placeholders).
            parameters: Values to bind (never use string formatting).

        Returns:
            SQLAlchemy CursorResult for further inspection (supports
            .fetchone() and .fetchall()).

        Raises:
            SQLAlchemyError: On execution failure.
        """
        stmt, params = self._to_named(sql, parameters)
        try:
            return self.connection.execute(stmt, params)
        except SQLAlchemyError as exc:
            self.connection.rollback()
            raise exc

    def fetch_one(
        self,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ) -> Optional[dict]:
        """
        Execute a query and return the first row as a dict, or ``None``.

        Args:
            sql: Parameterised SELECT statement.
            parameters: Values to bind.

        Returns:
            First matching row as a dict (supports both row["col"] and
            row.get("col")), or ``None`` if no results.
        """
        cursor = self.execute(sql, parameters)
        row = cursor.fetchone()
        return dict(row._mapping) if row is not None else None

    def fetch_all(
        self,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ) -> list[dict]:
        """
        Execute a query and return all rows as dicts.

        Args:
            sql: Parameterised SELECT statement.
            parameters: Values to bind.

        Returns:
            List of matching rows as dicts (empty list if none).
        """
        cursor = self.execute(sql, parameters)
        return [dict(row._mapping) for row in cursor.fetchall()]

    def commit(self) -> None:
        """Commit the current transaction."""
        self.connection.commit()

    def _insert_and_get_id(
        self,
        sql: str,
        parameters: tuple[Any, ...],
        pk_column: str,
    ) -> Optional[int]:
        """
        Execute an INSERT (or upsert) and return the affected row's
        primary key, portably across SQLite and Postgres.

        SQLite (and MySQL) support ``cursor.lastrowid`` directly, but
        Postgres does not reliably support it at all — the correct,
        dialect-agnostic way to get an inserted id back is
        ``INSERT ... RETURNING <pk_column>``, which both SQLite (3.35+)
        and Postgres understand. Using this everywhere means every
        ``create_*``/``add_*`` method below returns the right id on
        either database with no per-call-site branching.

        Args:
            sql: The INSERT statement, WITHOUT a trailing semicolon or
                RETURNING clause (both are added here).
            parameters: Values to bind, sqlite3-style ``?`` order.
            pk_column: Name of the primary-key column to return.

        Returns:
            The primary key of the inserted/updated row, or ``None`` if
            the statement affected no row (e.g. an ON CONFLICT DO
            NOTHING that matched nothing).
        """
        stmt = sql.rstrip().rstrip(";") + f" RETURNING {pk_column}"
        cursor = self.execute(stmt, parameters)
        row = cursor.fetchone()
        return row[0] if row is not None else None

    # ------------------------------------------------------------------
    # Transaction context manager
    # ------------------------------------------------------------------

    @contextmanager
    def transaction(self):
        """
        Context manager for explicit transactions.

        Usage::
            with db.transaction():
                db.execute("INSERT ...", (value,))
                db.execute("UPDATE ...", (value,))
            # Committed on success, rolled back on exception.
        """
        try:
            yield
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    # =====================================================================
    # USERS
    # =====================================================================

    def create_user(
        self,
        email: str,
        password_hash: str,
        display_name: str,
        timezone: str = "UTC",
    ) -> int:
        """
        Register a new user and initialise an empty profile.

        Args:
            email: Unique email address.
            password_hash: Pre-hashed password string.
            display_name: Human-readable name.
            timezone: IANA timezone name (default ``UTC``).

        Returns:
            The newly created ``user_id``.

        Raises:
            sqlite3.IntegrityError: If the email already exists.
        """
        with self.transaction():
            user_id = self._insert_and_get_id(
                """
                INSERT INTO users (email, password_hash, display_name, timezone)
                VALUES (?, ?, ?, ?)
                """,
                (email, password_hash, display_name, timezone),
                pk_column="user_id",
            )

            # Initialise empty profile so AI always has a row to read
            self.execute(
                "INSERT INTO user_profiles (user_id) VALUES (?)",
                (user_id,),
            )

        return user_id

    def get_user(self, user_id: int) -> Optional[sqlite3.Row]:
        """
        Retrieve a user by ID.

        Args:
            user_id: Primary key of the user.

        Returns:
            User row, or ``None`` if not found.
        """
        return self.fetch_one(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,),
        )

    def get_user_by_email(self, email: str) -> Optional[sqlite3.Row]:
        """
        Retrieve a user by email address.

        Args:
            email: Email to look up.

        Returns:
            User row, or ``None`` if not found.
        """
        return self.fetch_one(
            "SELECT * FROM users WHERE email = ?",
            (email,),
        )

    def update_user_timezone(self, user_id: int, timezone: str) -> None:
        """
        Update a user's stored IANA timezone name.

        Intended to be called by the client (mobile app, or a web
        settings page) whenever it has a real device/browser timezone
        to report — e.g. once at login, or whenever the app detects the
        device timezone changed (user travelled). Not validated against
        the IANA database here; invalid values safely fall back to UTC
        wherever they're consumed (see timezone_utils.safe_zoneinfo).

        Args:
            user_id: Whose timezone to update.
            timezone: IANA timezone name (e.g. "Africa/Cairo",
                "America/New_York").
        """
        self.execute(
            "UPDATE users SET timezone = ? WHERE user_id = ?",
            (timezone, user_id),
        )
        self.commit()

    # =====================================================================
    # PLANS
    # =====================================================================

    def create_plan(
        self,
        user_id: int,
        plan_date: date,
        raw_input: str,
        ai_summary: Optional[str] = None,
        status: str = "active",
    ) -> int:
        """
        Create a new daily plan for a user.

        Args:
            user_id: Owner of the plan.
            plan_date: The calendar date this plan represents.
            raw_input: Original natural-language text from the user.
            ai_summary: Optional LLM-generated summary.
            status: Plan lifecycle state (``draft``, ``active``, ``completed``).

        Returns:
            The newly created ``plan_id``.

        Raises:
            sqlite3.IntegrityError: If a plan already exists for this user+date.
        """
        plan_id = self._insert_and_get_id(
            """
            INSERT INTO plans (user_id, plan_date, raw_input, ai_summary, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, plan_date.isoformat(), raw_input, ai_summary, status),
            pk_column="plan_id",
        )
        self.commit()
        return plan_id

    def get_today_plan(self, user_id: int) -> Optional[sqlite3.Row]:
        """
        Fetch the active plan for today (system local date).

        Args:
            user_id: Owner of the plan.

        Returns:
            Plan row, or ``None`` if no plan exists for today.
        """
        today = date.today().isoformat()
        return self.fetch_one(
            """
            SELECT * FROM plans
            WHERE user_id = ? AND plan_date = ?
            ORDER BY plan_id DESC
            LIMIT 1
            """,
            (user_id, today),
        )

    def get_plan_by_date(
        self,
        user_id: int,
        plan_date: date,
    ) -> Optional[sqlite3.Row]:
        """
        Fetch a user's plan for a specific date.

        Args:
            user_id: Owner of the plan.
            plan_date: Calendar date to query.

        Returns:
            Plan row, or ``None`` if not found.
        """
        return self.fetch_one(
            "SELECT * FROM plans WHERE user_id = ? AND plan_date = ?",
            (user_id, plan_date.isoformat()),
        )

    def get_plan_by_id(self, plan_id: int) -> Optional[sqlite3.Row]:
        """
        Fetch a plan by its primary key.

        Args:
            plan_id: Plan primary key.

        Returns:
            Plan row, or ``None`` if not found.
        """
        return self.fetch_one(
            "SELECT * FROM plans WHERE plan_id = ?",
            (plan_id,),
        )

    def get_recent_plans(
        self,
        user_id: int,
        since_date: date,
        exclude_plan_id: Optional[int] = None,
    ) -> list[sqlite3.Row]:
        """
        Retrieve a user's plans within a rolling time window, newest first.

        Args:
            user_id: Owner of the plans.
            since_date: Only plans on or after this date are returned.
            exclude_plan_id: Optional plan id to omit (e.g. the plan
                currently being analyzed, to keep "today" out of its own
                history).

        Returns:
            List of plan rows ordered by plan_date descending.
        """
        query = "SELECT * FROM plans WHERE user_id = ? AND plan_date >= ?"
        params: list = [user_id, since_date.isoformat()]

        if exclude_plan_id is not None:
            query += " AND plan_id != ?"
            params.append(exclude_plan_id)

        query += " ORDER BY plan_date DESC, plan_id DESC"
        return self.fetch_all(query, tuple(params))

    def update_plan_status(self, plan_id: int, status: str) -> None:
        """
        Update the status of a plan.

        Args:
            plan_id: Target plan.
            status: New status value.
        """
        self.execute(
            "UPDATE plans SET status = ? WHERE plan_id = ?",
            (status, plan_id),
        )
        self.commit()

    # =====================================================================
    # TASKS
    # =====================================================================

    def add_task(
        self,
        plan_id: int,
        title: str,
        category_id: Optional[int] = None,
        description: Optional[str] = None,
        priority: int = 3,
        estimated_minutes: int = 30,
        scheduled_start: Optional[str] = None,
        scheduled_end: Optional[str] = None,
        order_index: int = 0,
        is_fixed_time: bool = False,
        is_break: bool = False,
    ) -> int:
        """
        Add a task to an existing plan.

        Args:
            plan_id: Parent plan.
            title: Task name (AI-extracted).
            category_id: Optional category classification.
            description: Optional extra context.
            priority: 1 (highest) to 5 (lowest).
            estimated_minutes: LLM duration estimate.
            scheduled_start: ``HH:MM`` suggested start time.
            scheduled_end: ``HH:MM`` suggested end time.
            order_index: Display order within the plan.
            is_fixed_time: True if the user/AI pinned this task to a real
                clock time (e.g. "meeting at 2pm"). Fixed-time tasks are
                never moved by the Scheduler, regardless of what work-day
                start time is chosen when re-running it. False (the
                default) means the Scheduler is free to place/move this
                task around other commitments.
            is_break: True if this row represents a user-defined break
                rather than a real task. Breaks are always fixed-time
                (the scheduler carves out exactly the window the user
                asked for) and are rendered/controlled differently in
                the UI (start/countdown instead of complete/fail).

        Returns:
            The newly created ``task_id``.
        """
        task_id = self._insert_and_get_id(
            """
            INSERT INTO tasks (
                plan_id, category_id, title, description,
                priority, estimated_minutes, scheduled_start,
                scheduled_end, order_index, is_fixed_time, is_break
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                category_id,
                title,
                description,
                priority,
                estimated_minutes,
                scheduled_start,
                scheduled_end,
                order_index,
                1 if is_fixed_time else 0,
                1 if is_break else 0,
            ),
            pk_column="task_id",
        )
        self.commit()
        return task_id

    def update_task(
        self,
        task_id: int,
        **kwargs: Any,
    ) -> None:
        """
        Update one or more columns on a task.

        Only columns present in ``kwargs`` are modified. Safe against SQL
        injection because column names are whitelisted.

        Args:
            task_id: Target task.
            **kwargs: Column names and new values.
                      Allowed keys:
                      ``title``, ``description``, ``priority``,
                      ``estimated_minutes``, ``scheduled_start``,
                      ``scheduled_end``, ``order_index``, ``category_id``,
                      ``plan_id`` (used to move a task to a different
                      day's plan, e.g. deferring it to tomorrow).
        """
        allowed = {
            "title",
            "description",
            "priority",
            "estimated_minutes",
            "scheduled_start",
            "scheduled_end",
            "order_index",
            "category_id",
            "plan_id",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return

        set_clause = ", ".join(f"{col} = ?" for col in updates)
        values = list(updates.values()) + [task_id]

        self.execute(
            f"UPDATE tasks SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
            tuple(values),
        )
        self.commit()

    def delete_task(self, task_id: int) -> None:
        """
        Remove a task permanently.

        Args:
            task_id: Task to delete.
        """
        self.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
        self.commit()

    def get_tasks_by_plan(self, plan_id: int) -> list[sqlite3.Row]:
        """
        Retrieve all tasks belonging to a plan, ordered by display index.

        Args:
            plan_id: Parent plan.

        Returns:
            List of task rows.
        """
        return self.fetch_all(
            """
            SELECT * FROM tasks
            WHERE plan_id = ?
            ORDER BY order_index ASC, task_id ASC
            """,
            (plan_id,),
        )

    def get_task(self, task_id: int) -> Optional[sqlite3.Row]:
        """
        Fetch a single task by ID.

        Args:
            task_id: Task primary key.

        Returns:
            Task row, or ``None`` if not found.
        """
        return self.fetch_one(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        )

    def set_task_timer_state(
        self,
        task_id: int,
        accumulated_seconds: int,
        segment_started_at: Optional[str],
        paused_at: Optional[str],
        total_paused_seconds: int,
    ) -> None:
        """
        Directly set a task's full pause/resume timer state. Low-level —
        callers (services.start_task_timer/pause_task_timer/
        resume_task_timer/finish_task_with_timer) are responsible for
        computing the right values; this just persists them all in one
        statement (they always change together, so there's no reason to
        risk a partial update between two separate calls).

        Args:
            task_id: Target task.
            accumulated_seconds: Total active seconds banked from every
                completed run segment (excludes the current one).
            segment_started_at: ``YYYY-MM-DD HH:MM:SS`` UTC timestamp
                marking the start of the currently-running active
                segment, or None if paused/not started/finished.
            paused_at: ``YYYY-MM-DD HH:MM:SS`` UTC timestamp marking the
                start of the CURRENT pause, or None if not paused.
            total_paused_seconds: Cumulative duration of every FINISHED
                pause (the currently-open one, if any, is on top of
                this at read time, not included here yet).
        """
        self.execute(
            """
            UPDATE tasks
            SET timer_accumulated_seconds = ?,
                timer_segment_started_at = ?,
                paused_at = ?,
                timer_total_paused_seconds = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE task_id = ?
            """,
            (accumulated_seconds, segment_started_at, paused_at, total_paused_seconds, task_id),
        )
        self.commit()

    def increment_task_pause_count(self, task_id: int) -> None:
        """Bump a task's pause_count by 1 (called once per Pause click)."""
        self.execute(
            "UPDATE tasks SET pause_count = pause_count + 1, "
            "updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
            (task_id,),
        )
        self.commit()

    def get_pause_matrix(
        self,
        user_id: int,
        since_date: str,
        user_timezone: str = "UTC",
    ) -> list[dict]:
        """
        Build the Category x Time-of-day focus matrix: for every task
        this user started within the window, bucket it by the category
        it belongs to and the LOCAL hour-of-day it was FIRST started
        (started_at, converted from UTC to the user's timezone), and
        sum pause_count within each bucket.

        This is the raw data behind "when/on what do you lose focus
        most" — e.g. a user who consistently pauses 'Study' tasks
        started in the Evening bucket, but rarely pauses 'Study' tasks
        started in the Morning, has a clear, actionable pattern.
        Deliberately returned as flat (category, time_bucket) rows
        rather than a pre-pivoted grid, so any caller (a UI table, the
        AI Coach's recommendation prompt, a future analytics chart) can
        reshape it however it needs without re-querying.

        Only tasks with started_at set are included (a task that was
        never started has no time-of-day to bucket it by). Uncategorised
        tasks are grouped under 'Uncategorised'.

        Args:
            user_id: Whose tasks to analyze.
            since_date: ISO date string — only plans on/after this date.
            user_timezone: IANA timezone name (e.g. "Africa/Cairo") used
                to convert the stored UTC started_at into the hour the
                user actually experienced. Defaults to "UTC" for users
                who haven't had a real timezone captured yet.

        Returns:
            List of dicts: {category, time_bucket, task_count,
            paused_task_count, total_pauses, avg_pauses_per_task}.
            time_bucket is one of 'Morning' (5-12), 'Afternoon' (12-17),
            'Evening' (17-21), 'Night' (21-5), all in the user's local
            time.
        """
        rows = self.fetch_all(
            """
            SELECT
                COALESCE(c.name, 'Uncategorised') AS category,
                t.started_at AS started_at,
                t.pause_count AS pause_count,
                t.timer_total_paused_seconds AS timer_total_paused_seconds
            FROM tasks t
            JOIN plans p ON t.plan_id = p.plan_id
            LEFT JOIN categories c ON t.category_id = c.category_id
            WHERE p.user_id = ?
              AND p.plan_date >= ?
              AND t.started_at IS NOT NULL
              AND t.is_break = 0
            """,
            (user_id, since_date),
        )

        def _bucket(started_at_raw) -> str:
            hour = local_hour_from_utc_string(started_at_raw, user_timezone)
            if hour is None:
                return "Unknown"
            if 5 <= hour < 12:
                return "Morning"
            if 12 <= hour < 17:
                return "Afternoon"
            if 17 <= hour < 21:
                return "Evening"
            return "Night"

        cells: dict[tuple[str, str], dict[str, int]] = {}
        for row in rows:
            key = (row["category"], _bucket(row["started_at"]))
            cell = cells.setdefault(
                key, {
                    "task_count": 0, "paused_task_count": 0, "total_pauses": 0,
                    "total_paused_seconds": 0,
                }
            )
            cell["task_count"] += 1
            pause_count = int(row["pause_count"] or 0)
            cell["total_pauses"] += pause_count
            cell["total_paused_seconds"] += int(row["timer_total_paused_seconds"] or 0)
            if pause_count > 0:
                cell["paused_task_count"] += 1

        matrix: list[dict] = []
        for (category, time_bucket), cell in cells.items():
            matrix.append({
                "category": category,
                "time_bucket": time_bucket,
                "task_count": cell["task_count"],
                "paused_task_count": cell["paused_task_count"],
                "total_pauses": cell["total_pauses"],
                "avg_pauses_per_task": (
                    cell["total_pauses"] / cell["task_count"] if cell["task_count"] else 0.0
                ),
                "total_paused_seconds": cell["total_paused_seconds"],
                "avg_pause_duration_seconds": (
                    cell["total_paused_seconds"] / cell["total_pauses"]
                    if cell["total_pauses"] else 0.0
                ),
            })
        matrix.sort(key=lambda r: (-r["total_pauses"], r["category"], r["time_bucket"]))
        return matrix

    def update_task_status(
        self,
        task_id: int,
        status: str,
        failure_reason: Optional[str] = None,
        actual_minutes: Optional[int] = None,
    ) -> None:
        """
        Update a task's status.

        Timing behaviour:
            - status == 'in_progress': stamps ``started_at`` with now,
              starting the timer (only if not already started, so
              re-clicking Start doesn't reset an in-flight timer).
            - status in ('completed', 'failed'): stamps ``completed_at``
              with now. If ``actual_minutes`` isn't explicitly passed in,
              it is auto-computed as the elapsed minutes since
              ``started_at``. If the task was never started (went
              straight from pending to completed/failed), it falls back
              to the task's own ``estimated_minutes``.

        Args:
            task_id: Target task.
            status: New status ('in_progress', 'completed', or 'failed').
            failure_reason: Required when status is 'failed'.
            actual_minutes: Optional explicit override for actual
                duration. Leave unset to let the timer compute it.
        """
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        now_utc_str = now_utc.strftime("%Y-%m-%d %H:%M:%S")

        if status == "in_progress":
            self.execute(
                """
                UPDATE tasks
                SET status = ?,
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE task_id = ?
                """,
                (status, now_utc_str, now_utc_str, task_id),
            )
            self.commit()
            return

        completed_at = now_utc_str if status in ("completed", "failed") else None

        if status in ("completed", "failed") and actual_minutes is None:
            task = self.get_task(task_id)
            started_at_raw = task["started_at"] if task is not None else None
            if started_at_raw:
                try:
                    started_dt = datetime.fromisoformat(str(started_at_raw))
                    elapsed = (now_utc - started_dt).total_seconds() / 60.0
                    actual_minutes = max(1, round(elapsed))
                except (ValueError, TypeError):
                    actual_minutes = task["estimated_minutes"] if task is not None else None
            elif task is not None:
                # Never started (went straight to completed/failed) — no
                # timer data exists, so fall back to the estimate.
                actual_minutes = task["estimated_minutes"]

        self.execute(
            """
            UPDATE tasks
            SET status = ?,
                failure_reason = ?,
                actual_minutes = ?,
                completed_at = ?,
                updated_at = ?
            WHERE task_id = ?
            """,
            (status, failure_reason, actual_minutes, completed_at, now_utc_str, task_id),
        )
        self.commit()

    def close_out_stale_tasks(self, user_id: int) -> int:
        """
        Auto-close any task left ``pending``/``in_progress`` on a
        past-dated plan for this user, so tasks never sit unresolved
        indefinitely and always get counted in analytics.

        - A task that was never started (``pending``) is marked
          ``failed`` with ``actual_minutes = 0`` — there's no real
          timer data to report since it was never touched.
        - A task that was started but never finished (``in_progress``)
          is marked ``failed`` via ``update_task_status``, which reuses
          the normal timer logic to compute real elapsed minutes from
          ``started_at``.

        Both get ``failure_reason = 'Ran out of time'`` — a signal the
        AI Coach can use to notice overcommitment patterns (e.g. "you
        keep planning more than you can realistically finish").

        Args:
            user_id: The plans' owner.

        Returns:
            The number of tasks that were closed out.
        """
        today = date.today().isoformat()
        stale = self.fetch_all(
            """
            SELECT tasks.task_id, tasks.status
            FROM tasks
            JOIN plans ON plans.plan_id = tasks.plan_id
            WHERE plans.user_id = ?
              AND plans.plan_date < ?
              AND tasks.status IN ('pending', 'in_progress')
            """,
            (user_id, today),
        )

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        now_utc_str = now_utc.strftime("%Y-%m-%d %H:%M:%S")

        for row in stale:
            if row["status"] == "pending":
                self.execute(
                    """
                    UPDATE tasks
                    SET status = 'failed',
                        failure_reason = 'Ran out of time',
                        actual_minutes = 0,
                        completed_at = ?,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (now_utc_str, now_utc_str, row["task_id"]),
                )
            else:  # in_progress — let the timer logic compute real elapsed time
                self.update_task_status(
                    row["task_id"], status="failed", failure_reason="Ran out of time"
                )

        self.commit()
        return len(stale)

    def get_recent_tasks_for_user(
        self,
        user_id: int,
        since_date: date,
        exclude_plan_id: Optional[int] = None,
    ) -> list[sqlite3.Row]:
        """
        Retrieve a user's tasks within a rolling time window, newest first.

        Joins to plans (to scope by user_id, expose plan_date, and apply
        the time window) and to categories (to expose a readable category
        name). Used by the Recommendation layer to build historical
        context — Recommendation only reads this data, it never computes
        statistics from it.

        Args:
            user_id: Owner of the tasks, via their plans.
            since_date: Only tasks belonging to plans on or after this
                date are returned.
            exclude_plan_id: Optional plan id to omit.

        Returns:
            List of task rows (plus plan_date and category_name) ordered
            by plan_date descending, then order_index ascending.
        """
        query = """
            SELECT
                t.*,
                p.plan_date AS plan_date,
                c.name AS category_name
            FROM tasks t
            JOIN plans p ON t.plan_id = p.plan_id
            LEFT JOIN categories c ON t.category_id = c.category_id
            WHERE p.user_id = ? AND p.plan_date >= ?
        """
        params: list = [user_id, since_date.isoformat()]

        if exclude_plan_id is not None:
            query += " AND p.plan_id != ?"
            params.append(exclude_plan_id)

        query += " ORDER BY p.plan_date DESC, t.order_index ASC"
        return self.fetch_all(query, tuple(params))

    def get_failure_reason_counts(
        self,
        user_id: int,
        since_date: date,
        exclude_plan_id: Optional[int] = None,
    ) -> list[sqlite3.Row]:
        """
        Compute how many times each failure_reason occurred for a user's
        failed tasks within a rolling time window.

        This is an aggregate statistic derived directly from stored
        data — the same category of work as main_failure_reason already
        cached in user_profiles. It belongs at the data layer:
        Recommendation consumes this distribution, it does not compute
        it.

        Args:
            user_id: Owner of the tasks, via their plans.
            since_date: Only tasks belonging to plans on or after this
                date are counted.
            exclude_plan_id: Optional plan id to omit.

        Returns:
            Rows of (failure_reason, occurrence_count), ordered by count
            descending. Tasks with no recorded failure_reason are
            excluded.
        """
        query = """
            SELECT
                t.failure_reason AS failure_reason,
                COUNT(*) AS occurrence_count
            FROM tasks t
            JOIN plans p ON t.plan_id = p.plan_id
            WHERE p.user_id = ?
              AND p.plan_date >= ?
              AND t.failure_reason IS NOT NULL
        """
        params: list = [user_id, since_date.isoformat()]

        if exclude_plan_id is not None:
            query += " AND p.plan_id != ?"
            params.append(exclude_plan_id)

        query += " GROUP BY t.failure_reason ORDER BY occurrence_count DESC"
        return self.fetch_all(query, tuple(params))

    # =====================================================================
    # USER PROFILES
    # =====================================================================

    def get_profile(self, user_id: int) -> Optional[sqlite3.Row]:
        """
        Retrieve the pre-computed analytics profile for a user.

        Args:
            user_id: Target user.

        Returns:
            Profile row, or ``None`` if the user does not exist.
        """
        return self.fetch_one(
            "SELECT * FROM user_profiles WHERE user_id = ?",
            (user_id,),
        )

    def update_profile(
        self,
        user_id: int,
        **kwargs: Any,
    ) -> None:
        """
        Update analytics fields in the user's profile.

        Called by the Python behaviour-analysis script after processing
        historical task data.

        Args:
            user_id: Target user.
            **kwargs: Allowed keys —
                      ``completion_rate``, ``productivity_score``,
                      ``best_productivity_hour``, ``avg_delay_minutes``,
                      ``main_failure_reason``, ``favorite_category_id``,
                      ``current_streak``, ``longest_streak``,
                      ``total_completed``, ``total_failed``, ``total_tasks``.
        """
        allowed = {
            "completion_rate",
            "productivity_score",
            "best_productivity_hour",
            "avg_delay_minutes",
            "main_failure_reason",
            "favorite_category_id",
            "current_streak",
            "longest_streak",
            "total_completed",
            "total_failed",
            "total_tasks",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return

        set_clause = ", ".join(f"{col} = ?" for col in updates)
        values = list(updates.values()) + [user_id]

        self.execute(
            f"""
            UPDATE user_profiles
            SET {set_clause}, last_updated = CURRENT_TIMESTAMP
            WHERE user_id = ?
            """,
            tuple(values),
        )
        self.commit()

    # =====================================================================
    # BADGES
    # =====================================================================

    def award_badge(self, user_id: int, badge_id: int) -> Optional[int]:
        """
        Award a badge to a user if they do not already have it.

        Args:
            user_id: Recipient.
            badge_id: Badge to award.

        Returns:
            The ``user_badge_id`` if newly awarded, or ``None`` if the user
            already possesses this badge.

        Raises:
            sqlite3.IntegrityError: If the badge_id does not exist.
        """
        try:
            new_id = self._insert_and_get_id(
                """
                INSERT INTO user_badges (user_id, badge_id)
                VALUES (?, ?)
                """,
                (user_id, badge_id),
                pk_column="user_badge_id",
            )
            self.commit()
            return new_id
        except IntegrityError:
            # UNIQUE(user_id, badge_id) violation – already earned
            self.connection.rollback()
            return None

    def get_user_badges(self, user_id: int) -> list[sqlite3.Row]:
        """
        Retrieve all badges earned by a user.

        Args:
            user_id: Target user.

        Returns:
            List of joined user_badge + badge rows.
        """
        return self.fetch_all(
            """
            SELECT
                ub.user_badge_id,
                ub.earned_at,
                b.badge_id,
                b.name,
                b.description,
                b.icon,
                b.requirement_type,
                b.requirement_value
            FROM user_badges ub
            JOIN badges b ON ub.badge_id = b.badge_id
            WHERE ub.user_id = ?
            ORDER BY ub.earned_at DESC
            """,
            (user_id,),
        )

    def get_all_badges(self) -> list[sqlite3.Row]:
        """
        Retrieve all badge definitions.

        Returns:
            List of badge rows.
        """
        return self.fetch_all("SELECT * FROM badges ORDER BY badge_id")

    def create_badge(
        self,
        name: str,
        description: str,
        requirement_type: str,
        requirement_value: int,
        icon: Optional[str] = None,
    ) -> int:
        """
        Insert a new badge definition (typically used during seeding).

        Args:
            name: Unique badge name.
            description: How the badge is earned.
            requirement_type: ``streak``, ``count``, or ``rate``.
            requirement_value: Numeric threshold.
            icon: Optional icon path or URL.

        Returns:
            The newly created ``badge_id``.
        """
        badge_id = self._insert_and_get_id(
            """
            INSERT INTO badges (name, description, icon, requirement_type, requirement_value)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, description, icon, requirement_type, requirement_value),
            pk_column="badge_id",
        )
        self.commit()
        return badge_id

    # =====================================================================
    # CATEGORIES (bonus helpers)
    # =====================================================================

    def create_category(
        self,
        user_id: int,
        name: str,
        color: str = "#3B82F6",
    ) -> int:
        """
        Create a custom category for a user.

        Args:
            user_id: Owner.
            name: Category name (unique per user).
            color: Hex colour code.

        Returns:
            The newly created ``category_id``.

        Raises:
            sqlite3.IntegrityError: If the user already has this category name.
        """
        category_id = self._insert_and_get_id(
            "INSERT INTO categories (user_id, name, color) VALUES (?, ?, ?)",
            (user_id, name, color),
            pk_column="category_id",
        )
        self.commit()
        return category_id

    def get_categories(self, user_id: int) -> list[sqlite3.Row]:
        """
        List all categories belonging to a user.

        Args:
            user_id: Target user.

        Returns:
            List of category rows.
        """
        return self.fetch_all(
            "SELECT * FROM categories WHERE user_id = ? ORDER BY name",
            (user_id,),
        )

    # ------------------------------------------------------------------
    # Google OAuth Tokens
    # ------------------------------------------------------------------

    def save_google_tokens(
        self,
        user_id: int,
        access_token: str,
        refresh_token: str,
        token_expiry: str,
        scopes: str = "",
    ) -> int:
        """Insert or replace Google OAuth tokens for a user."""
        token_id = self._insert_and_get_id(
            """
            INSERT INTO google_oauth_tokens
                (user_id, access_token, refresh_token, token_expiry, scopes)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                access_token = excluded.access_token,
                refresh_token = excluded.refresh_token,
                token_expiry = excluded.token_expiry,
                scopes = excluded.scopes,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, access_token, refresh_token, token_expiry, scopes),
            pk_column="token_id",
        )
        self.commit()
        return token_id

    def get_google_tokens(self, user_id: int) -> Optional[sqlite3.Row]:
        """Fetch stored Google OAuth tokens for a user."""
        return self.fetch_one(
            "SELECT * FROM google_oauth_tokens WHERE user_id = ?",
            (user_id,),
        )

    def update_google_tokens(
        self,
        user_id: int,
        access_token: str,
        token_expiry: str,
    ) -> None:
        """Update access token and expiry after a refresh."""
        self.execute(
            """
            UPDATE google_oauth_tokens
            SET access_token = ?, token_expiry = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
            """,
            (access_token, token_expiry, user_id),
        )
        self.commit()

    def delete_google_tokens(self, user_id: int) -> None:
        """Remove Google OAuth tokens (disconnect)."""
        self.execute(
            "DELETE FROM google_oauth_tokens WHERE user_id = ?",
            (user_id,),
        )
        self.commit()

    # ------------------------------------------------------------------
    # Google Selected Calendars
    # ------------------------------------------------------------------

    def save_selected_calendars(
        self,
        user_id: int,
        calendars: list[dict],
    ) -> None:
        """
        Replace all selected calendars for a user.

        Args:
            calendars: List of dicts with keys:
                calendar_id, calendar_name, color, is_primary
        """
        with self.transaction():
            self.execute(
                "DELETE FROM google_selected_calendars WHERE user_id = ?",
                (user_id,),
            )
            for cal in calendars:
                self.execute(
                    """
                    INSERT INTO google_selected_calendars
                        (user_id, calendar_id, calendar_name, color, is_primary)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        cal["calendar_id"],
                        cal.get("calendar_name", ""),
                        cal.get("color", "#4285F4"),
                        1 if cal.get("is_primary", False) else 0,
                    ),
                )

    def get_selected_calendars(self, user_id: int) -> list[sqlite3.Row]:
        """Fetch the user's selected Google Calendars."""
        return self.fetch_all(
            "SELECT * FROM google_selected_calendars WHERE user_id = ? ORDER BY is_primary DESC, calendar_name",
            (user_id,),
        )

    def get_selected_calendar_ids(self, user_id: int) -> list[str]:
        """Return just the calendar_id strings for the user's selections."""
        rows = self.fetch_all(
            "SELECT calendar_id FROM google_selected_calendars WHERE user_id = ?",
            (user_id,),
        )
        return [row["calendar_id"] for row in rows]

    def delete_selected_calendars(self, user_id: int) -> None:
        """Remove all selected calendars for a user."""
        self.execute(
            "DELETE FROM google_selected_calendars WHERE user_id = ?",
            (user_id,),
        )
        self.commit()

    # ------------------------------------------------------------------
    # Google Calendar Events
    # ------------------------------------------------------------------

    def upsert_google_calendar_event(
        self,
        user_id: int,
        google_event_id: str,
        title: str,
        start_time: str,
        end_time: str,
        event_date: str,
        calendar_id: str,
    ) -> int:
        """Insert or update a synced Google Calendar event."""
        event_id = self._insert_and_get_id(
            """
            INSERT INTO google_calendar_events
                (user_id, google_event_id, title, start_time, end_time,
                 event_date, calendar_id, last_synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, google_event_id, event_date) DO UPDATE SET
                title = excluded.title,
                start_time = excluded.start_time,
                end_time = excluded.end_time,
                calendar_id = excluded.calendar_id,
                last_synced_at = CURRENT_TIMESTAMP
            """,
            (user_id, google_event_id, title, start_time, end_time,
             event_date, calendar_id),
            pk_column="event_id",
        )
        self.commit()
        return event_id

    def mark_task_exported(self, task_id: int) -> None:
        """
        Stamp a task as freshly exported to Google Calendar (its
        scheduled_start/scheduled_end at this exact moment are now
        reflected there). Used for the "update an existing event" export
        path, where google_event_id doesn't change but the export
        timestamp still needs to move forward — this is what lets the
        app detect a schedule change made AFTER the last export and warn
        the user, instead of silently leaving Google Calendar stale.

        Args:
            task_id: The task that was just (re-)exported.
        """
        self.execute(
            "UPDATE tasks SET google_exported_at = CURRENT_TIMESTAMP WHERE task_id = ?",
            (task_id,),
        )
        self.commit()

    def get_stale_google_exports(self, plan_id: int) -> list[dict]:
        """
        Find tasks in a plan that were exported to Google Calendar at
        some point, but whose scheduled_start/scheduled_end has changed
        SINCE that export (e.g. the Scheduler was re-run afterwards) —
        meaning Google Calendar currently shows an out-of-date time.

        Tasks never exported at all (google_event_id IS NULL) are not
        "stale" — there's nothing in Google Calendar to be behind yet.

        Args:
            plan_id: The plan to check.

        Returns:
            List of task rows whose Google Calendar event no longer
            matches the task's current scheduled time.
        """
        return self.fetch_all(
            """
            SELECT * FROM tasks
            WHERE plan_id = ?
              AND google_event_id IS NOT NULL
              AND (
                    google_exported_at IS NULL
                    OR updated_at > google_exported_at
                  )
            ORDER BY order_index ASC, task_id ASC
            """,
            (plan_id,),
        )

    def get_google_calendar_events(
        self,
        user_id: int,
        event_date: str,
    ) -> list[sqlite3.Row]:
        """Fetch synced Google Calendar events for a given date."""
        return self.fetch_all(
            """
            SELECT * FROM google_calendar_events
            WHERE user_id = ? AND event_date = ?
            ORDER BY start_time ASC
            """,
            (user_id, event_date),
        )

    def delete_google_events_not_in(
        self,
        user_id: int,
        event_date: str,
        calendar_id: str,
        keep_ids: list[str],
    ) -> None:
        """Remove events deleted from Google for a specific calendar+date."""
        if not keep_ids:
            self.execute(
                """
                DELETE FROM google_calendar_events
                WHERE user_id = ? AND event_date = ? AND calendar_id = ?
                """,
                (user_id, event_date, calendar_id),
            )
        else:
            placeholders = ", ".join("?" for _ in keep_ids)
            self.execute(
                f"""
                DELETE FROM google_calendar_events
                WHERE user_id = ? AND event_date = ? AND calendar_id = ?
                  AND google_event_id NOT IN ({placeholders})
                """,
                (user_id, event_date, calendar_id, *keep_ids),
            )
        self.commit()

    def delete_all_google_calendar_events(self, user_id: int) -> None:
        """Remove all synced events for a user (disconnect cleanup)."""
        self.execute(
            "DELETE FROM google_calendar_events WHERE user_id = ?",
            (user_id,),
        )
        self.commit()

    def delete_google_events_by_calendar(
        self,
        user_id: int,
        calendar_id: str,
    ) -> None:
        """Remove all events from a specific calendar (deselection)."""
        self.execute(
            "DELETE FROM google_calendar_events WHERE user_id = ? AND calendar_id = ?",
            (user_id, calendar_id),
        )
        self.commit()

    # ------------------------------------------------------------------
    # Task Export Tracking
    # ------------------------------------------------------------------

    def update_task_google_event_id(
        self,
        task_id: int,
        google_event_id: Optional[str],
    ) -> None:
        """Set the Google Calendar event ID for a newly-exported task,
        and stamp it as freshly exported (see mark_task_exported)."""
        self.execute(
            """
            UPDATE tasks
            SET google_event_id = ?, google_exported_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE task_id = ?
            """,
            (google_event_id, task_id),
        )
        self.commit()

    def get_tasks_with_google_event_id(
        self,
        plan_id: int,
    ) -> list[sqlite3.Row]:
        """Fetch tasks that have been exported to Google Calendar."""
        return self.fetch_all(
            """
            SELECT * FROM tasks
            WHERE plan_id = ? AND google_event_id IS NOT NULL
            ORDER BY order_index ASC
            """,
            (plan_id,),
        )