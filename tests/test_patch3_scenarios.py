import tempfile
import unittest
from datetime import date, time
from pathlib import Path

from database import Database, StaleScheduleError, metadata
from scheduler import SchedulingPreferences
from scheduler_service import SchedulerService
from smart_scheduler import SmartScheduler


def flexible(task_id, minutes, priority=3):
    return {
        "task_id": task_id,
        "title": f"Task {task_id}",
        "category": "Work",
        "estimated_minutes": minutes,
        "priority": priority,
    }


class Patch3AllocationScenarios(unittest.TestCase):
    day = date(2026, 9, 30)

    def allocate(self, tasks, blocked=(), start=time(9)):
        preferences = SchedulingPreferences(start, blocked_slots=list(blocked))
        return SmartScheduler(preferences).allocate(tasks, self.day)

    def test_scenarios_a_fixed_break_and_flexible(self):
        tasks = [
            {**flexible(1, 60), "is_fixed_time": True, "fixed_start": "09:00", "fixed_end": "10:00"},
            {**flexible(2, 30), "is_fixed_time": True, "is_break": True, "fixed_start": "10:00", "fixed_end": "10:30"},
            flexible(3, 60),
        ]
        result = self.allocate(tasks)
        by_id = {item.task_id: item for item in result.placed_tasks}
        self.assertEqual((time(9), time(10)), (by_id[1].scheduled_start, by_id[1].scheduled_end))
        self.assertEqual((time(10), time(10, 30)), (by_id[2].scheduled_start, by_id[2].scheduled_end))
        self.assertEqual(time(10, 30), by_id[3].scheduled_start)

    def test_scenarios_b_c_two_and_three_segments(self):
        two = self.allocate([flexible(1, 60)], [(time(9, 30), time(10)), (time(10, 30), time(23, 59, 59))])
        three = self.allocate([flexible(2, 150)], [(time(10), time(11)), (time(12), time(22, 30))])
        self.assertEqual(2, len(two.placed_tasks[0].segments))
        self.assertEqual(3, len(three.placed_tasks[0].segments))
        self.assertEqual(60, sum(segment.duration_minutes for segment in two.placed_tasks[0].segments))
        self.assertEqual(150, sum(segment.duration_minutes for segment in three.placed_tasks[0].segments))

    def test_scenarios_d_e_capacity_and_segment_cap(self):
        no_capacity = self.allocate([flexible(1, 120)], [(time(10), time(23, 59, 59))])
        too_fragmented = self.allocate(
            [flexible(2, 60)],
            [(time(9, 15), time(9, 30)), (time(9, 45), time(10)),
             (time(10, 15), time(10, 30)), (time(10, 45), time(23, 59, 59))],
        )
        self.assertEqual("capacity_shortfall", no_capacity.unplaced_tasks[0].reason)
        self.assertEqual("fragmentation_segment_cap", too_fragmented.unplaced_tasks[0].reason)
        self.assertEqual([], no_capacity.placed_tasks)
        self.assertEqual([], too_fragmented.placed_tasks)

    def test_scenarios_f_g_whole_window_preferred_but_valid_split_allowed(self):
        whole = self.allocate([flexible(1, 90), flexible(2, 30, priority=1)], [(time(10), time(11))])
        by_id = {item.task_id: item for item in whole.placed_tasks}
        self.assertEqual(1, len(by_id[1].segments))
        self.assertEqual(time(11), by_id[1].scheduled_start)
        split = self.allocate([flexible(3, 60)], [(time(9, 30), time(10)), (time(10, 30), time(23, 59, 59))])
        self.assertEqual(2, len(split.placed_tasks[0].segments))

    def test_scenario_h_midnight_is_never_crossed(self):
        result = self.allocate([flexible(1, 60)], start=time(23, 30))
        self.assertEqual([], result.placed_tasks)
        self.assertEqual("capacity_shortfall", result.unplaced_tasks[0].reason)


class Patch3PersistenceScenarios(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "patch3.db"
        self.db = Database(f"sqlite:///{path.as_posix()}")
        self.db.execute("INSERT INTO users (user_id,email,display_name) VALUES ('u','u@example.test','U')")
        self.db.execute("INSERT INTO plans (user_id,plan_date,raw_input) VALUES ('u','2026-09-30','patch3')")
        self.db.connection.commit()
        self.plan = self.db.fetch_one("SELECT plan_id FROM plans")["plan_id"]

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def revision(self):
        return int(self.db.get_plan_by_id(self.plan, "u")["schedule_revision"])

    @staticmethod
    def segments(*windows, provenance="legacy_unknown"):
        return [
            {
                "segment_index": index,
                "scheduled_start": start,
                "scheduled_end": end,
                "calendar_provenance": provenance,
            }
            for index, (start, end) in enumerate(windows)
        ]

    def add(self, title="Flexible", minutes=60, **kwargs):
        return self.db.add_task(self.plan, "u", title, estimated_minutes=minutes, **kwargs)

    def test_scenarios_i_j_imported_blocking_and_coachai_segments(self):
        imported = self.add(
            "Imported", 60, is_fixed_time=True, scheduled_start="10:00", scheduled_end="11:00",
            calendar_provenance="imported", google_event_id="external-event",
        )
        work = self.add("Flexible", 90)
        service = SchedulerService(self.db)
        service.schedule_plan(self.plan, "u", SchedulingPreferences(time(9)))
        imported_segment = self.db.get_task_segments(imported, "u")[0]
        work_segments = self.db.get_task_segments(work, "u")
        self.assertEqual("imported", imported_segment["calendar_provenance"])
        self.assertEqual("external-event", imported_segment["google_event_id"])
        self.assertTrue(all(not (str(row["scheduled_start"]) < "11:00:00" and str(row["scheduled_end"]) > "10:00:00") for row in work_segments))
        segment_id = work_segments[0]["segment_id"]
        self.db.update_segment_calendar_state(
            segment_id, "u", google_event_id="coachai-event",
            calendar_provenance="coachai_created", calendar_sync_status="synced",
        )
        self.assertEqual("coachai_created", self.db.get_task_segments(work, "u")[0]["calendar_provenance"])

    def test_scenarios_k_l_undo_and_stale_writer(self):
        task = self.add()
        base = self.revision()
        first = self.db.replace_plan_segments(
            self.plan, "u", {task: self.segments(("09:00:00", "10:00:00"))}, base,
        )
        with self.assertRaises(StaleScheduleError):
            self.db.replace_plan_segments(
                self.plan, "u", {task: self.segments(("11:00:00", "12:00:00"))}, base,
            )
        self.db.undo_schedule_operation(first, "u")
        self.assertEqual([], self.db.get_task_segments(task, "u"))
        self.assertEqual("undone", self.db.get_schedule_operation(first, "u")["status"])
        self.assertEqual(base + 2, self.revision())

    def test_scenarios_m_n_manual_move_and_duration_change(self):
        task = self.add()
        self.db.replace_plan_segments(
            self.plan, "u", {task: self.segments(("09:00:00", "09:30:00"), ("10:00:00", "10:30:00"))},
            self.revision(),
        )
        self.db.set_single_task_segment(task, "u", "13:00", "14:00")
        row = self.db.get_task(task, "u")
        self.assertEqual("13:00:00", str(row["scheduled_start"]))
        self.assertEqual(1, len(self.db.get_task_segments(task, "u")))
        before = self.revision()
        self.db.update_task(task, "u", estimated_minutes=90)
        self.assertEqual([], self.db.get_task_segments(task, "u"))
        self.assertIsNone(self.db.get_task(task, "u")["scheduled_start"])
        self.assertEqual(before + 1, self.revision())

    def test_scenarios_o_p_task_and_break_deletion_cascade(self):
        task = self.add()
        break_id = self.add(
            "Break", 30, is_fixed_time=True, is_break=True,
            scheduled_start="10:00", scheduled_end="10:30",
        )
        self.db.replace_plan_segments(
            self.plan, "u", {
                task: self.segments(("09:00:00", "09:30:00"), ("10:30:00", "11:00:00")),
                break_id: self.segments(("10:00:00", "10:30:00")),
            }, self.revision(),
        )
        self.db.delete_task(task, "u")
        self.db.delete_task(break_id, "u")
        self.assertEqual(0, self.db.fetch_one("SELECT COUNT(*) AS n FROM task_segments")["n"])
        self.assertEqual([], self.db.get_tasks_by_plan(self.plan, "u"))


class PostgreSQLStaticSchemaTests(unittest.TestCase):
    def test_all_metadata_ddl_compiles_for_postgresql(self):
        from sqlalchemy.schema import CreateIndex, CreateTable
        from sqlalchemy.dialects import postgresql

        dialect = postgresql.dialect()
        for table in metadata.sorted_tables:
            self.assertTrue(str(CreateTable(table).compile(dialect=dialect)).strip())
            for index in table.indexes:
                self.assertTrue(str(CreateIndex(index).compile(dialect=dialect)).strip())

        required = {"task_segments", "schedule_operations"}
        self.assertTrue(required.issubset(metadata.tables))
        self.assertIn("schedule_revision", metadata.tables["plans"].c)
        segment_columns = metadata.tables["task_segments"].c
        for name in ("google_event_id", "google_calendar_id", "calendar_provenance", "calendar_sync_status"):
            self.assertIn(name, segment_columns)


if __name__ == "__main__":
    unittest.main()
