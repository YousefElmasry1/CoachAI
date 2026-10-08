import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch

from database import Database, StaleScheduleError
from future_planner import FuturePlanningService
from planning_request import (
    InterpretedPlanningIntent,
    InterpretedTask,
    PlanningClarificationRequired,
    PlanningInputMethod,
    PlanningRequestInterpreter,
    PlanningScope,
    PlanningTaskRequest,
    StructuredPlanningRequest,
)


class Patch52FuturePlanningUXTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(f"sqlite:///{(Path(self.tmp.name) / 'patch52.db').as_posix()}")
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
        self.plans = {day: self.db.create_plan("a", day, "future") for day in self.days}
        self.other_plan = self.db.create_plan("b", self.days[0], "other")
        self.service = FuturePlanningService(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def task(self, title="Task", minutes=60, *, day=None, priority=3, user="a"):
        plan_id = self.other_plan if user == "b" else self.plans[day or self.days[0]]
        return self.db.add_task(
            plan_id, user, title, estimated_minutes=minutes, priority=priority,
        )

    def request(self, task_ids, *, scope=PlanningScope.DATE_RANGE,
                method=PlanningInputMethod.MANUAL, start=None, end=None, overrides=None):
        start = start or self.days[0]
        end = end or (start if scope == PlanningScope.SPECIFIC_DAY else self.days[-1])
        overrides = overrides or {}
        tasks = []
        for task_id in task_ids:
            row = self.db.get_task(task_id, "a")
            tasks.append(PlanningTaskRequest(
                task_id=task_id,
                title=row["title"],
                **overrides.get(task_id, {}),
            ))
        return StructuredPlanningRequest(
            scope=scope, input_method=method,
            planning_start=start, planning_end=end,
            work_day_start=time(8), tasks=tasks,
        )

    def test_specific_day_is_exactly_one_day_and_not_rejected_as_a_range(self):
        task_id = self.task()
        request = self.request(
            [task_id], scope=PlanningScope.SPECIFIC_DAY,
            start=self.days[0], end=self.days[0],
        )
        preview = self.service.preview_request("a", request)
        self.assertEqual(preview.planning_start, preview.planning_end)
        self.assertTrue(all(
            segment["scheduled_date"] == self.days[0].isoformat()
            for segments in preview.segments_by_task.values() for segment in segments
        ))
        with self.assertRaisesRegex(ValueError, "same start and end"):
            self.request(
                [task_id], scope=PlanningScope.SPECIFIC_DAY,
                start=self.days[0], end=self.days[1],
            )

    def test_date_range_twelve_weeks_is_allowed_and_longer_is_rejected(self):
        start = self.days[0]
        valid = StructuredPlanningRequest(
            scope=PlanningScope.DATE_RANGE, input_method=PlanningInputMethod.MANUAL,
            planning_start=start, planning_end=start + timedelta(weeks=12),
            work_day_start=time(8), tasks=[],
        )
        self.assertEqual(start + timedelta(weeks=12), valid.planning_end)
        with self.assertRaisesRegex(ValueError, "at most 12 weeks"):
            StructuredPlanningRequest(
                scope=PlanningScope.DATE_RANGE, input_method=PlanningInputMethod.MANUAL,
                planning_start=start, planning_end=start + timedelta(weeks=12, days=1),
                work_day_start=time(8), tasks=[],
            )

    def test_interpreter_maps_only_an_exact_existing_task(self):
        task_id = self.task("Machine Learning")
        candidates = self.db.get_future_planning_tasks("a", self.days[0], self.days[-1])
        intent = InterpretedPlanningIntent(tasks=[InterpretedTask(
            task_title="Machine Learning", remaining_minutes=120,
            duration_source="user_explicit", priority=1,
            deadline=datetime(2026, 10, 7, 12),
        )])
        request = PlanningRequestInterpreter.build_request(
            intent, candidates=candidates,
            scope=PlanningScope.DATE_RANGE, input_method=PlanningInputMethod.WRITE,
            planning_start=self.days[0], planning_end=self.days[-1], work_day_start=time(8),
        )
        self.assertEqual(task_id, request.tasks[0].task_id)
        self.assertEqual(120, request.tasks[0].remaining_minutes)
        self.assertEqual(PlanningInputMethod.WRITE, request.input_method)

    def test_interpreter_requires_duration_before_creating_a_draft(self):
        self.task("Known")
        candidates = self.db.get_future_planning_tasks("a", self.days[0], self.days[-1])
        intent = InterpretedPlanningIntent(tasks=[InterpretedTask(task_title="Invented")])
        with self.assertRaisesRegex(ValueError, "valid duration estimate"):
            PlanningRequestInterpreter.build_request(
                intent, candidates=candidates,
                scope=PlanningScope.DATE_RANGE, input_method=PlanningInputMethod.WRITE,
                planning_start=self.days[0], planning_end=self.days[-1], work_day_start=time(8),
            )

    def test_all_input_methods_feed_the_same_deterministic_planner(self):
        task_id = self.task("Shared", 120)
        results = []
        for method in PlanningInputMethod:
            request = self.request([task_id], method=method)
            results.append(self.service.preview_request("a", request).segments_by_task)
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])

    def test_preview_is_read_only_with_manual_constraint_changes(self):
        task_id = self.task("Read only", 60, priority=3)
        request = self.request([task_id], overrides={task_id: {
            "remaining_minutes": 120, "priority": 1,
            "deadline": datetime(2026, 10, 7, 12),
        }})
        preview = self.service.preview_request("a", request)
        stored = self.db.get_task(task_id, "a")
        self.assertEqual(60, stored["estimated_minutes"])
        self.assertEqual(3, stored["priority"])
        self.assertIsNone(stored["deadline"])
        self.assertEqual([], self.db.get_task_segments(task_id, "a"))
        self.assertEqual(120, preview.task_updates[task_id]["estimated_minutes"])

    def test_apply_and_undo_restore_schedule_and_constraints_exactly(self):
        task_id = self.task("Atomic", 60, priority=3)
        request = self.request([task_id], overrides={task_id: {
            "remaining_minutes": 120,
            "priority": 1,
            "earliest_start": datetime(2026, 10, 6, 9),
            "deadline": datetime(2026, 10, 7, 12),
        }})
        preview = self.service.preview_request("a", request)
        operation_id = self.service.apply(preview)
        applied = self.db.get_task(task_id, "a")
        self.assertEqual(120, applied["estimated_minutes"])
        self.assertEqual(1, applied["priority"])
        self.assertTrue(self.db.get_task_segments(task_id, "a"))
        self.db.undo_schedule_operation(operation_id, "a")
        restored = self.db.get_task(task_id, "a")
        self.assertEqual(60, restored["estimated_minutes"])
        self.assertEqual(3, restored["priority"])
        self.assertIsNone(restored["earliest_start"])
        self.assertIsNone(restored["deadline"])
        self.assertEqual([], self.db.get_task_segments(task_id, "a"))

    def test_stale_apply_writes_neither_constraints_nor_segments(self):
        task_id = self.task("Stale", 60, priority=3)
        preview = self.service.preview_request(
            "a", self.request([task_id], overrides={task_id: {"priority": 1}}),
        )
        self.db.execute(
            "UPDATE plans SET schedule_revision=schedule_revision+1 WHERE plan_id=?",
            (self.plans[self.days[0]],),
        )
        self.db.connection.commit()
        with self.assertRaises(StaleScheduleError):
            self.service.apply(preview)
        self.assertEqual(3, self.db.get_task(task_id, "a")["priority"])
        self.assertEqual([], self.db.get_task_segments(task_id, "a"))

    def test_unselected_task_is_a_stable_occupancy_anchor(self):
        anchor = self.task("Anchor", 60)
        selected = self.task("Selected", 60)
        self.db.execute(
            """INSERT INTO task_segments
               (task_id,segment_index,scheduled_date,scheduled_start,scheduled_end)
               VALUES (?,?,?,?,?)""",
            (anchor, 0, self.days[0].isoformat(), "08:00", "09:00"),
        )
        self.db.connection.commit()
        preview = self.service.preview_request(
            "a", self.request(
                [selected], scope=PlanningScope.SPECIFIC_DAY,
                start=self.days[0], end=self.days[0],
            ),
        )
        self.assertNotIn(anchor, preview.segments_by_task)
        self.assertGreaterEqual(
            preview.outcomes[0].segments[0].scheduled_start, time(9),
        )

    def test_existing_stable_task_remains_stable(self):
        task_id = self.task("Stable", 60)
        first = self.service.preview_request("a", self.request([task_id]))
        self.service.apply(first)
        second = self.service.preview_request("a", self.request([task_id]))
        self.assertEqual("already_valid", second.outcomes[0].reason)
        self.assertEqual([], second.changes)

    def test_empty_selection_has_clear_no_work_domain_result(self):
        self.task("Available")
        preview = self.service.preview_request("a", self.request([]))
        self.assertEqual([], preview.outcomes)
        self.assertEqual({}, preview.segments_by_task)

    def test_tenant_task_cannot_enter_an_owned_request(self):
        other = self.task("Other", user="b")
        request = StructuredPlanningRequest(
            scope=PlanningScope.SPECIFIC_DAY, input_method=PlanningInputMethod.MANUAL,
            planning_start=self.days[0], planning_end=self.days[0], work_day_start=time(8),
            tasks=[PlanningTaskRequest(task_id=other, title="Other")],
        )
        with self.assertRaisesRegex(ValueError, "not owned"):
            self.service.preview_request("a", request)


if __name__ == "__main__":
    unittest.main()
