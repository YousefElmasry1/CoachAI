import sqlite3
import os
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_NAME: str = "coach_ai.db"
SCHEMA_FILE: str = "schema.sql"


# ---------------------------------------------------------------------------
# Database Class
# ---------------------------------------------------------------------------

class Database:
    """
    SQLite database manager for CoachAI.

    Automatically creates the database file and executes the schema on first
    use. Provides low-level query helpers and high-level domain methods.

    Attributes:
        db_path: Absolute path to the SQLite database file.
        connection: Active sqlite3.Connection instance.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        """
        Initialise the database manager.

        Args:
            db_path: Optional custom path for the database file.
                     Defaults to ``coach_ai.db`` in the current directory.
        """
        self.db_path: str = db_path or os.path.abspath(DB_NAME)
        self.connection: Optional[sqlite3.Connection] = None
        self._connect()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        """Open a connection to SQLite and enable foreign keys."""
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._maybe_create_schema()

    def close(self) -> None:
        """Safely close the database connection."""
        if self.connection:
            try:
                self.connection.close()
            except sqlite3.Error:
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
    # ------------------------------------------------------------------

    def _maybe_create_schema(self) -> None:
        """
        Execute ``schema.sql`` if the database is empty (no tables yet).

        Looks for ``schema.sql`` in the same directory as this module.
        """
        cursor = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' LIMIT 1"
        )
        if cursor.fetchone() is not None:
            self._maybe_migrate_started_at()
            self._maybe_migrate_failure_reason_check()
            return  # Schema already applied

        schema_path = Path(__file__).with_name(SCHEMA_FILE)
        if not schema_path.exists():
            raise FileNotFoundError(
                f"Schema file not found: {schema_path}"
            )

        with open(schema_path, "r", encoding="utf-8") as f:
            self.connection.executescript(f.read())
        self.connection.commit()

    def _maybe_migrate_started_at(self) -> None:
        """
        Add the ``started_at`` column to an existing ``tasks`` table if it
        doesn't have it yet.

        This makes the Start-Task timer feature self-healing for databases
        created before this column existed, instead of requiring the user
        to run a manual ``ALTER TABLE`` migration.
        """
        cursor = self.connection.execute("PRAGMA table_info(tasks)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "started_at" not in existing_columns:
            self.connection.execute("ALTER TABLE tasks ADD COLUMN started_at DATETIME")
            self.connection.commit()

    def _maybe_migrate_failure_reason_check(self) -> None:
        """
        Rebuild the ``tasks`` table if its ``failure_reason`` CHECK
        constraint doesn't yet include ``'Ran out of time'``.

        SQLite bakes CHECK constraints into a table's original CREATE
        TABLE statement — unlike a plain column, they can't be altered
        in place. This performs SQLite's standard rebuild pattern
        (rename -> recreate with the new constraint -> copy rows over ->
        drop the old table) inside a single transaction, so it's safe to
        run on every startup and a no-op once already migrated.
        """
        cursor = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'"
        )
        row = cursor.fetchone()
        if row is None or row[0] is None or "Ran out of time" in row[0]:
            return  # Already up to date (or table doesn't exist yet)

        self.connection.execute("PRAGMA foreign_keys = OFF")
        try:
            with self.connection:
                self.connection.execute("ALTER TABLE tasks RENAME TO tasks_old")
                self.connection.execute(
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
                self.connection.execute(
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
                self.connection.execute("DROP TABLE tasks_old")
                self.connection.execute("CREATE INDEX idx_tasks_plan_id ON tasks(plan_id)")
                self.connection.execute("CREATE INDEX idx_tasks_category_id ON tasks(category_id)")
                self.connection.execute("CREATE INDEX idx_tasks_status ON tasks(status)")
                self.connection.execute("CREATE INDEX idx_tasks_plan_status ON tasks(plan_id, status)")
        finally:
            self.connection.execute("PRAGMA foreign_keys = ON")

    # ------------------------------------------------------------------
    # Low-level query helpers
    # ------------------------------------------------------------------

    def execute(
        self,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ) -> sqlite3.Cursor:
        """
        Execute a single SQL statement with parameters.

        Args:
            sql: Parameterised SQL statement.
            parameters: Values to bind (never use string formatting).

        Returns:
            sqlite3.Cursor for further inspection.

        Raises:
            sqlite3.Error: On execution failure.
        """
        try:
            return self.connection.execute(sql, parameters)
        except sqlite3.Error as exc:
            self.connection.rollback()
            raise exc

    def fetch_one(
        self,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ) -> Optional[sqlite3.Row]:
        """
        Execute a query and return the first row, or ``None``.

        Args:
            sql: Parameterised SELECT statement.
            parameters: Values to bind.

        Returns:
            First matching row, or ``None`` if no results.
        """
        cursor = self.execute(sql, parameters)
        return cursor.fetchone()

    def fetch_all(
        self,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ) -> list[sqlite3.Row]:
        """
        Execute a query and return all rows.

        Args:
            sql: Parameterised SELECT statement.
            parameters: Values to bind.

        Returns:
            List of matching rows (empty list if none).
        """
        cursor = self.execute(sql, parameters)
        return cursor.fetchall()

    def commit(self) -> None:
        """Commit the current transaction."""
        self.connection.commit()

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
            cursor = self.execute(
                """
                INSERT INTO users (email, password_hash, display_name, timezone)
                VALUES (?, ?, ?, ?)
                """,
                (email, password_hash, display_name, timezone),
            )
            user_id = cursor.lastrowid

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
        cursor = self.execute(
            """
            INSERT INTO plans (user_id, plan_date, raw_input, ai_summary, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, plan_date.isoformat(), raw_input, ai_summary, status),
        )
        self.commit()
        return cursor.lastrowid

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

        Returns:
            The newly created ``task_id``.
        """
        cursor = self.execute(
            """
            INSERT INTO tasks (
                plan_id, category_id, title, description,
                priority, estimated_minutes, scheduled_start,
                scheduled_end, order_index
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        self.commit()
        return cursor.lastrowid

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
                      ``scheduled_end``, ``order_index``, ``category_id``.
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
            cursor = self.execute(
                """
                INSERT INTO user_badges (user_id, badge_id)
                VALUES (?, ?)
                """,
                (user_id, badge_id),
            )
            self.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            # UNIQUE(user_id, badge_id) violation – already earned
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
        cursor = self.execute(
            """
            INSERT INTO badges (name, description, icon, requirement_type, requirement_value)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, description, icon, requirement_type, requirement_value),
        )
        self.commit()
        return cursor.lastrowid

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
        cursor = self.execute(
            "INSERT INTO categories (user_id, name, color) VALUES (?, ?, ?)",
            (user_id, name, color),
        )
        self.commit()
        return cursor.lastrowid

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