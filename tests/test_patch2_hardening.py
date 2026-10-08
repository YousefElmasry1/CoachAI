import tempfile
import unittest
from datetime import time
from pathlib import Path
from unittest.mock import patch

import services
from database import Database, StaleScheduleError


class Patch2HardeningTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "hardening.db"
        self.db = Database(f"sqlite:///{path.as_posix()}")
        self.db.execute("INSERT INTO users (user_id,email,display_name) VALUES ('u','u@x.com','U')")
        self.db.execute("INSERT INTO plans (user_id,plan_date,raw_input) VALUES ('u','2026-09-30','x')")
        self.db.connection.commit()
        self.plan = self.db.fetch_one("SELECT plan_id FROM plans")["plan_id"]
        self.task_a = self.db.add_task(self.plan, "u", "A", estimated_minutes=60)
        self.task_b = self.db.add_task(self.plan, "u", "B", estimated_minutes=60)

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def revision(self):
        return int(self.db.get_plan_by_id(self.plan, "u")["schedule_revision"])

    @staticmethod
    def one(start, end):
        return [{"segment_index": 0, "scheduled_start": start, "scheduled_end": end}]

    def complete_schedule(self):
        return {
            self.task_a: self.one("09:00:00", "10:00:00"),
            self.task_b: self.one("10:00:00", "11:00:00"),
        }

    def test_failure_mid_replacement_rolls_back_revision_cache_segments_and_operation(self):
        before_revision = self.revision()
        original_execute = self.db.execute
        inserts = 0

        def failing_execute(sql, parameters=()):
            nonlocal inserts
            if "INSERT INTO task_segments" in sql:
                inserts += 1
                if inserts == 2:
                    raise RuntimeError("injected persistence failure")
            return original_execute(sql, parameters)

        self.db.execute = failing_execute
        with self.assertRaises(RuntimeError):
            self.db.replace_plan_segments(
                self.plan, "u", self.complete_schedule(), before_revision,
            )
        self.db.execute = original_execute
        self.assertEqual(before_revision, self.revision())
        self.assertEqual([], self.db.get_plan_segments(self.plan, "u"))
        self.assertEqual(0, self.db.fetch_one("SELECT COUNT(*) AS n FROM schedule_operations")["n"])
        tasks = self.db.get_tasks_by_plan(self.plan, "u")
        self.assertTrue(all(task["scheduled_start"] is None for task in tasks))

    def test_two_writers_same_revision_second_is_stale_and_writes_nothing(self):
        shared_revision = self.revision()
        first = self.db.replace_plan_segments(
            self.plan, "u", self.complete_schedule(), shared_revision,
        )
        state_after_first = self.db._schedule_snapshot(self.plan, "u")
        with self.assertRaises(StaleScheduleError):
            self.db.replace_plan_segments(
                self.plan, "u",
                {
                    self.task_a: self.one("11:00:00", "12:00:00"),
                    self.task_b: self.one("12:00:00", "13:00:00"),
                },
                shared_revision,
            )
        self.assertEqual(state_after_first, self.db._schedule_snapshot(self.plan, "u"))
        operations = self.db.fetch_all("SELECT operation_id FROM schedule_operations")
        self.assertEqual([first], [row["operation_id"] for row in operations])

    def test_failure_mid_undo_restores_post_operation_state_and_status(self):
        initial = self.complete_schedule()
        self.db.replace_plan_segments(self.plan, "u", initial, self.revision())
        operation = self.db.replace_plan_segments(
            self.plan, "u",
            {
                self.task_a: self.one("11:00:00", "12:00:00"),
                self.task_b: self.one("12:00:00", "13:00:00"),
            },
            self.revision(),
        )
        before_undo = self.db._schedule_snapshot(self.plan, "u")
        revision = self.revision()
        original_execute = self.db.execute

        def failing_execute(sql, parameters=()):
            if "INSERT INTO task_segments" in sql:
                raise RuntimeError("injected undo failure")
            return original_execute(sql, parameters)

        self.db.execute = failing_execute
        with self.assertRaises(RuntimeError):
            self.db.undo_schedule_operation(operation, "u")
        self.db.execute = original_execute
        self.assertEqual(before_undo, self.db._schedule_snapshot(self.plan, "u"))
        self.assertEqual(revision, self.revision())
        self.assertEqual("applied", self.db.get_schedule_operation(operation, "u")["status"])

    def test_invalid_payloads_are_rejected_before_writes(self):
        invalid_payloads = [
            {self.task_a: [
                {"segment_index": i, "scheduled_start": f"0{9+i}:00:00", "scheduled_end": f"0{9+i}:15:00"}
                for i in range(4)
            ], self.task_b: []},
            {self.task_a: self.one("09:00:00", "09:10:00"), self.task_b: []},
            {self.task_a: [
                {"segment_index": 0, "scheduled_start": "09:00:00", "scheduled_end": "09:30:00"},
                {"segment_index": 1, "scheduled_start": "09:15:00", "scheduled_end": "09:45:00"},
            ], self.task_b: []},
            {self.task_a: self.one("23:30:00", "00:30:00"), self.task_b: []},
            {self.task_a: self.one("09:00:00", "10:00:00"), self.task_b: self.one("09:30:00", "10:30:00")},
        ]
        baseline_revision = self.revision()
        for payload in invalid_payloads:
            with self.assertRaises(ValueError):
                self.db.replace_plan_segments(self.plan, "u", payload, baseline_revision)
            self.assertEqual(baseline_revision, self.revision())
            self.assertEqual([], self.db.get_plan_segments(self.plan, "u"))

    def test_sqlite_trigger_rejects_invalid_clock_text(self):
        with self.assertRaises(Exception):
            self.db.execute(
                "INSERT INTO task_segments (task_id,segment_index,scheduled_start,scheduled_end) VALUES (?,?,?,?)",
                (self.task_a, 0, "24:00:00", "25:00:00"),
            )
        self.db.connection.rollback()

    def test_grouped_read_ignores_divergent_cache(self):
        self.db.replace_plan_segments(self.plan, "u", self.complete_schedule(), self.revision())
        self.db.execute(
            "UPDATE tasks SET scheduled_start='01:00',scheduled_end='02:00' WHERE task_id=?",
            (self.task_a,),
        )
        self.db.connection.commit()
        task = next(task for task in self.db.get_tasks_with_segments(self.plan, "u") if task["task_id"] == self.task_a)
        self.assertEqual("09:00:00", str(task["scheduled_start"]))
        self.db.execute("DELETE FROM task_segments WHERE task_id=?", (self.task_a,))
        self.db.connection.commit()
        task = next(task for task in self.db.get_tasks_with_segments(self.plan, "u") if task["task_id"] == self.task_a)
        self.assertIsNone(task["scheduled_start"])

    def test_flexible_duration_edit_invalidates_segments_and_cache(self):
        self.db.replace_plan_segments(self.plan, "u", self.complete_schedule(), self.revision())
        old_revision = self.revision()
        self.db.update_task(self.task_a, "u", estimated_minutes=90)
        self.assertEqual([], self.db.get_task_segments(self.task_a, "u"))
        self.assertIsNone(self.db.get_task(self.task_a, "u")["scheduled_start"])
        self.assertEqual(old_revision + 1, self.revision())

    def test_fixed_duration_edit_recomputes_one_window_and_marks_calendar_stale(self):
        self.db.update_task(self.task_a, "u", is_fixed_time=True)
        self.db.set_single_task_segment(self.task_a, "u", "09:00", "10:00")
        segment_id = self.db.get_task_segments(self.task_a, "u")[0]["segment_id"]
        self.db.update_segment_calendar_state(
            segment_id, "u", google_event_id="owned",
            calendar_provenance="coachai_created", calendar_sync_status="synced",
        )
        self.db.update_task(self.task_a, "u", estimated_minutes=90)
        segments = self.db.get_task_segments(self.task_a, "u")
        self.assertEqual(1, len(segments))
        self.assertEqual("09:00:00", str(segments[0]["scheduled_start"]))
        self.assertEqual("10:30:00", str(segments[0]["scheduled_end"]))
        self.assertEqual("owned", segments[0]["google_event_id"])
        self.assertEqual("pending_update", segments[0]["calendar_sync_status"])

    def test_status_change_preserves_logical_schedule(self):
        self.db.replace_plan_segments(self.plan, "u", self.complete_schedule(), self.revision())
        before = [dict(row) for row in self.db.get_task_segments(self.task_a, "u")]
        self.db.update_task_status(self.task_a, "u", "completed", actual_minutes=55)
        after = [dict(row) for row in self.db.get_task_segments(self.task_a, "u")]
        self.assertEqual(before, after)

    def test_break_creation_rejects_midnight_rollover_without_writes(self):
        before = len(self.db.get_tasks_by_plan(self.plan, "u"))
        with patch.object(services, "get_database", return_value=self.db):
            result = services.add_break_to_plan(
                self.plan, time(23, 50), duration_minutes=30, user_id="u",
            )
        self.assertIsNone(result)
        self.assertEqual(before, len(self.db.get_tasks_by_plan(self.plan, "u")))

    def test_compatibility_cache_cannot_be_written_directly(self):
        with self.assertRaises(ValueError):
            self.db.update_task(self.task_a, "u", scheduled_start="09:00")

    def test_nested_failure_cannot_be_caught_and_committed_by_outer_scope(self):
        original_title = self.db.get_task(self.task_a, "u")["title"]
        with self.db.transaction():
            self.db.execute("UPDATE tasks SET title='outer' WHERE task_id=?", (self.task_a,))
            try:
                with self.db.transaction():
                    self.db.execute("UPDATE tasks SET title='inner' WHERE task_id=?", (self.task_a,))
                    raise RuntimeError("inner failure")
            except RuntimeError:
                pass
        self.assertEqual(original_title, self.db.get_task(self.task_a, "u")["title"])


if __name__ == "__main__":
    unittest.main()
