import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import Database


ROOT = Path(__file__).resolve().parents[1]


class SmartSchedulingSchemaTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "test.db"

    def tearDown(self):
        self.tempdir.cleanup()

    def open_database(self):
        return Database(f"sqlite:///{self.db_path.as_posix()}")

    def test_fresh_sqlite_has_segment_and_operation_schema(self):
        with self.open_database() as db:
            tables = {row["name"] for row in db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            self.assertIn("task_segments", tables)
            self.assertIn("schedule_operations", tables)
            columns = {row["name"] for row in db.fetch_all("PRAGMA table_info(plans)")}
            self.assertIn("schedule_revision", columns)

    def test_backfill_only_copies_valid_same_day_intervals_and_is_idempotent(self):
        with self.open_database() as db:
            db.execute(
                "INSERT INTO users (user_id,email,display_name) VALUES (?,?,?)",
                ("u1", "u@example.com", "User"),
            )
            db.execute(
                "INSERT INTO plans (user_id,plan_date,raw_input) VALUES (?,?,?)",
                ("u1", "2026-09-29", "test"),
            )
            plan_id = db.fetch_one("SELECT plan_id FROM plans")["plan_id"]
            cases = [
                ("valid", "09:00", "10:00"),
                ("null", None, None),
                ("partial", "11:00", None),
                ("overnight", "12:30", "03:30"),
                ("equal", "14:00", "14:00"),
            ]
            for index, (title, start, end) in enumerate(cases):
                db.execute(
                    """INSERT INTO tasks
                       (plan_id,title,estimated_minutes,scheduled_start,scheduled_end,order_index)
                       VALUES (?,?,?,?,?,?)""",
                    (plan_id, title, 60, start, end, index),
                )
            db.connection.commit()
            db._maybe_create_smart_scheduling_schema()
            db._maybe_create_smart_scheduling_schema()
            rows = db.fetch_all(
                """SELECT t.title,s.segment_index,s.scheduled_start,s.scheduled_end
                   FROM task_segments s JOIN tasks t ON t.task_id=s.task_id"""
            )
            self.assertEqual(1, len(rows))
            self.assertEqual("valid", rows[0]["title"])
            self.assertEqual(0, rows[0]["segment_index"])

    def test_unique_segment_index_and_fk_cascade(self):
        with self.open_database() as db:
            db.execute(
                "INSERT INTO users (user_id,email,display_name) VALUES (?,?,?)",
                ("u1", "u@example.com", "User"),
            )
            db.execute(
                "INSERT INTO plans (user_id,plan_date,raw_input) VALUES (?,?,?)",
                ("u1", "2026-09-29", "test"),
            )
            plan_id = db.fetch_one("SELECT plan_id FROM plans")["plan_id"]
            db.execute(
                "INSERT INTO tasks (plan_id,title,estimated_minutes) VALUES (?,?,?)",
                (plan_id, "task", 60),
            )
            task_id = db.fetch_one("SELECT task_id FROM tasks")["task_id"]
            db.execute(
                "INSERT INTO task_segments (task_id,segment_index,scheduled_start,scheduled_end) VALUES (?,?,?,?)",
                (task_id, 0, "09:00", "10:00"),
            )
            db.connection.commit()
            with self.assertRaises(Exception):
                db.execute(
                    "INSERT INTO task_segments (task_id,segment_index,scheduled_start,scheduled_end) VALUES (?,?,?,?)",
                    (task_id, 0, "10:00", "11:00"),
                )
            db.connection.rollback()
            db.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            db.connection.commit()
            self.assertEqual(0, db.fetch_one("SELECT COUNT(*) AS n FROM task_segments")["n"])

    def test_real_database_copy_preserves_invalid_legacy_row(self):
        source = ROOT / "coach_ai.db"
        if not source.exists():
            self.skipTest("local database is unavailable")
        shutil.copy2(source, self.db_path)
        with self.open_database() as db:
            invalid = db.fetch_one(
                "SELECT task_id,scheduled_start,scheduled_end FROM tasks WHERE task_id = ?",
                (105,),
            )
            if invalid is None:
                self.skipTest("known invalid legacy row is not present")
            self.assertEqual("12:30", str(invalid["scheduled_start"])[:5])
            self.assertEqual("03:30", str(invalid["scheduled_end"])[:5])
            count = db.fetch_one(
                "SELECT COUNT(*) FROM task_segments WHERE task_id = ?", (105,)
            )["COUNT(*)"]
            self.assertEqual(0, count)


if __name__ == "__main__":
    unittest.main()
