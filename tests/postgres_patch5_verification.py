"""Opt-in Patch 5 runtime verification against a disposable PostgreSQL 16 DB.

Run explicitly with COACHAI_PATCH51_POSTGRES_URL.  This module is deliberately
not named ``test_*.py`` so the normal SQLite regression suite never connects to
or resets an external database.
"""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime, time
from unittest.mock import patch

import psycopg2
import sqlalchemy
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from analytics import AnalyticsLoader
from database import Database, StaleScheduleError, metadata
from future_planner import FuturePlanningService
from scheduler import SchedulingPreferences
from tests import test_patch5_future_planning as sqlite_patch5


POSTGRES_URL = os.environ.get("COACHAI_PATCH51_POSTGRES_URL", "")


def _checked_url() -> str:
    if not POSTGRES_URL:
        raise RuntimeError("COACHAI_PATCH51_POSTGRES_URL is required.")
    parsed = make_url(POSTGRES_URL)
    if parsed.drivername != "postgresql+psycopg2":
        raise RuntimeError("Patch 5.1 verification requires PostgreSQL via psycopg2.")
    if parsed.host not in {"127.0.0.1", "localhost"} or parsed.database != "coachai_patch51":
        raise RuntimeError("Refusing to reset anything except localhost/coachai_patch51.")
    return POSTGRES_URL


def _reset_disposable_schema() -> None:
    engine = create_engine(_checked_url(), future=True)
    try:
        metadata.drop_all(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()


def setUpModule() -> None:
    url = _checked_url()
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            version = connection.execute(text("SHOW server_version")).scalar_one()
        print(
            f"PostgreSQL={version}; SQLAlchemy={sqlalchemy.__version__}; "
            f"psycopg2={psycopg2.__version__}"
        )
    finally:
        engine.dispose()


class PostgreSQLPatch5RuntimeTests(sqlite_patch5.Patch5FuturePlanningTests):
    """Run the complete Patch 5 behavioral contract on PostgreSQL."""

    def setUp(self):
        _reset_disposable_schema()
        self.db = Database(_checked_url())
        for user in ("a", "b"):
            self.db.execute(
                "INSERT INTO users (user_id,email,display_name,timezone) VALUES (?,?,?,?)",
                (user, f"{user}@test.local", user.upper(), "UTC"),
            )
        self.db.connection.commit()
        self.days = [date(2026, 10, day) for day in (5, 6, 7)]
        self.today_patcher = patch("future_planner.user_today", return_value=self.days[0])
        self.today_patcher.start()
        self.addCleanup(self.today_patcher.stop)
        self.plans = {
            day: self.db.create_plan("a", day, "postgres future") for day in self.days
        }
        self.other_plan = self.db.create_plan("b", self.days[0], "other tenant")
        self.service = FuturePlanningService(self.db)
        self.pref = SchedulingPreferences(work_day_start=time(8))

    def tearDown(self):
        self.db.close()

    def test_progress_state_race_rejects_apply_without_partial_writes(self):
        task_id = self.task("Progress race", 60)
        preview = self.preview()
        revision = int(
            self.db.get_plan_by_id(self.plans[self.days[0]], "a")["schedule_revision"]
        )
        self.db.execute(
            "UPDATE tasks SET status='in_progress',started_at=? WHERE task_id=?",
            (datetime(2026, 10, 5, 8), task_id),
        )
        self.db.connection.commit()

        with self.assertRaises(StaleScheduleError):
            self.service.apply(preview)

        self.assertEqual([], self.db.get_task_segments(task_id, "a"))
        self.assertEqual(revision, int(
            self.db.get_plan_by_id(self.plans[self.days[0]], "a")["schedule_revision"]
        ))
        self.assertEqual(
            0, self.db.fetch_one("SELECT COUNT(*) n FROM schedule_operations")["n"]
        )

    def test_deleted_task_race_rejects_apply_without_operation(self):
        task_id = self.task("Deleted race", 60)
        preview = self.preview()
        self.db.delete_task(task_id, "a")

        with self.assertRaises(StaleScheduleError):
            self.service.apply(preview)

        self.assertEqual(
            0, self.db.fetch_one("SELECT COUNT(*) n FROM schedule_operations")["n"]
        )

    def test_all_protected_occupancy_classes_remain_anchors(self):
        day = self.days[0]
        plan = self.plans[day]

        def anchored(title, start, end, **kwargs):
            return self.db.add_task(
                plan, "a", title, estimated_minutes=int(
                    (datetime.combine(day, end) - datetime.combine(day, start)).total_seconds() // 60
                ), scheduled_start=start.isoformat(), scheduled_end=end.isoformat(), **kwargs
            )

        fixed = anchored("Fixed", time(8), time(9), is_fixed_time=True)
        break_task = anchored("Break", time(9), time(9, 30), is_break=True)
        started = anchored("Started", time(9, 30), time(10, 30))
        completed = anchored("Completed", time(10, 30), time(11, 30))
        failed = anchored("Failed", time(11, 30), time(12, 30))
        self.db.execute(
            "UPDATE tasks SET status='in_progress',started_at=? WHERE task_id=?",
            (datetime(2026, 10, 5, 9, 30), started),
        )
        self.db.execute("UPDATE tasks SET status='completed' WHERE task_id=?", (completed,))
        self.db.execute("UPDATE tasks SET status='failed' WHERE task_id=?", (failed,))
        self.db.connection.commit()
        self.db.upsert_google_calendar_event(
            "a", "external-anchor", "External", "12:30", "13:00",
            day.isoformat(), "primary",
        )
        target = self.task("After anchors", 60)
        before = {
            task_id: [dict(row) for row in self.db.get_task_segments(task_id, "a")]
            for task_id in (fixed, break_task, started, completed, failed)
        }

        outcome = next(
            item for item in self.preview(day, day).outcomes if item.task_id == target
        )

        self.assertGreaterEqual(outcome.segments[0].scheduled_start, time(13))
        for task_id, expected in before.items():
            self.assertEqual(expected, [dict(row) for row in self.db.get_task_segments(task_id, "a")])

    def test_postgres_analytics_reads_one_logical_task_with_dated_segments(self):
        task_id = self.task("Analytics multi-day", 600)
        self.service.apply(self.preview())

        snapshots = AnalyticsLoader(self.db).load_snapshots("a", window_days=30)
        matching = [snapshot for snapshot in snapshots if snapshot.task_id == task_id]

        self.assertEqual(1, len(matching))
        self.assertEqual(3, len(matching[0].dated_segments))
        minutes = sum(
            int((datetime.combine(date.min, time.fromisoformat(end))
                 - datetime.combine(date.min, time.fromisoformat(start))).total_seconds() // 60)
            for _, start, end in matching[0].dated_segments
        )
        self.assertEqual(600, minutes)

    def test_postgres_segment_constraints_uniqueness_and_cascade(self):
        task_id = self.task("Constraints", 60)
        self.db.execute(
            """INSERT INTO task_segments
               (task_id,segment_index,scheduled_date,scheduled_start,scheduled_end)
               VALUES (?,?,?,?,?)""",
            (task_id, 0, self.days[0], time(8), time(9)),
        )
        self.db.connection.commit()

        with self.assertRaises(SQLAlchemyError):
            self.db.execute(
                """INSERT INTO task_segments
                   (task_id,segment_index,scheduled_date,scheduled_start,scheduled_end)
                   VALUES (?,?,?,?,?)""",
                (task_id, 0, self.days[0], time(10), time(11)),
            )
        with self.assertRaises(SQLAlchemyError):
            self.db.execute(
                """INSERT INTO task_segments
                   (task_id,segment_index,scheduled_date,scheduled_start,scheduled_end)
                   VALUES (?,?,?,?,?)""",
                (task_id, 1, self.days[0], time(11), time(10)),
            )

        self.assertEqual(1, len(self.db.get_task_segments(task_id, "a")))
        self.db.delete_task(task_id, "a")
        self.assertEqual(
            0, self.db.fetch_one(
                "SELECT COUNT(*) n FROM task_segments WHERE task_id=?", (task_id,)
            )["n"],
        )


class PostgreSQLPatch5SchemaTests(unittest.TestCase):
    def setUp(self):
        _reset_disposable_schema()
        self.db = Database(_checked_url())

    def tearDown(self):
        self.db.close()

    def test_patch5_schema_types_indexes_constraints_and_foreign_keys(self):
        inspector = inspect(self.db.engine)
        task_columns = {column["name"]: column for column in inspector.get_columns("tasks")}
        segment_columns = {
            column["name"]: column for column in inspector.get_columns("task_segments")
        }
        plan_columns = {column["name"] for column in inspector.get_columns("plans")}
        operation_columns = {
            column["name"] for column in inspector.get_columns("schedule_operations")
        }
        segment_indexes = {index["name"] for index in inspector.get_indexes("task_segments")}
        segment_checks = {
            constraint["name"] for constraint in inspector.get_check_constraints("task_segments")
        }
        segment_foreign_keys = inspector.get_foreign_keys("task_segments")

        self.assertIn("earliest_start", task_columns)
        self.assertIn("deadline", task_columns)
        self.assertIn("TIMESTAMP", str(task_columns["earliest_start"]["type"]).upper())
        self.assertIn("scheduled_date", segment_columns)
        self.assertEqual("DATE", str(segment_columns["scheduled_date"]["type"]).upper())
        self.assertIn("idx_task_segments_date", segment_indexes)
        self.assertIn("schedule_revision", plan_columns)
        self.assertTrue({"before_state", "after_state", "calendar_reconciliation_error"}
                        <= operation_columns)
        self.assertTrue({"ck_task_segments_index", "ck_task_segments_same_day",
                         "ck_task_segments_provenance"} <= segment_checks)
        self.assertTrue(any(
            foreign_key["referred_table"] == "tasks"
            and foreign_key["options"].get("ondelete") == "CASCADE"
            for foreign_key in segment_foreign_keys
        ))


if __name__ == "__main__":
    unittest.main(verbosity=2)
