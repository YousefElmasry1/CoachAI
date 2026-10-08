import json
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch

import services
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


class FakeCalendar:
    def __init__(self):
        self.created = []
        self.deleted = []

    def create_event(self, **kwargs):
        self.created.append(kwargs)
        return kwargs["event_id"]

    def update_event(self, **kwargs):
        raise AssertionError("A new draft segment must create, not update, an event.")

    def delete_event(self, calendar_id, google_event_id):
        self.deleted.append((calendar_id, google_event_id))


class Patch521FuturePlanningDraftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "patch521.db"
        self.db = Database(f"sqlite:///{path.as_posix()}")
        for user in ("a", "b"):
            self.db.execute(
                "INSERT INTO users (user_id,email,display_name,timezone) VALUES (?,?,?,?)",
                (user, f"{user}@test.local", user.upper(), "UTC"),
            )
        self.db.connection.commit()
        self.start = date(2026, 10, 5)
        self.today_patcher = patch("future_planner.user_today", return_value=self.start)
        self.today_patcher.start()
        self.addCleanup(self.today_patcher.stop)
        self.service = FuturePlanningService(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def counts(self, user="a"):
        return {
            "plans": int(self.db.fetch_one(
                "SELECT COUNT(*) AS n FROM plans WHERE user_id=?", (user,),
            )["n"]),
            "tasks": int(self.db.fetch_one(
                """SELECT COUNT(*) AS n FROM tasks t JOIN plans p ON p.plan_id=t.plan_id
                   WHERE p.user_id=?""", (user,),
            )["n"]),
            "segments": int(self.db.fetch_one(
                """SELECT COUNT(*) AS n FROM task_segments s JOIN tasks t ON t.task_id=s.task_id
                   JOIN plans p ON p.plan_id=t.plan_id WHERE p.user_id=?""", (user,),
            )["n"]),
            "operations": int(self.db.fetch_one(
                "SELECT COUNT(*) AS n FROM schedule_operations WHERE user_id=?", (user,),
            )["n"]),
            "calendar": int(self.db.fetch_one(
                "SELECT COUNT(*) AS n FROM google_calendar_events WHERE user_id=?", (user,),
            )["n"]),
        }

    def request(self, tasks, *, end=None, method=PlanningInputMethod.MANUAL):
        end = end or self.start
        return StructuredPlanningRequest(
            scope=(PlanningScope.SPECIFIC_DAY if end == self.start else PlanningScope.DATE_RANGE),
            input_method=method,
            planning_start=self.start,
            planning_end=end,
            work_day_start=time(8),
            tasks=tasks,
        )

    @staticmethod
    def draft(title="Machine Learning", minutes=120, **kwargs):
        return PlanningTaskRequest(
            temporary_id=f"draft:{title.casefold().replace(' ', '-')}",
            title=title,
            remaining_minutes=minutes,
            priority=kwargs.pop("priority", 3),
            **kwargs,
        )

    def add_existing(self, title="Existing", minutes=60):
        plan = self.db.create_plan("a", self.start, "existing")
        return self.db.add_task(plan, "a", title, estimated_minutes=minutes, priority=3)

    def created_task_ids(self, operation_id):
        operation = self.db.get_schedule_operation(operation_id, "a")
        return [int(value) for value in json.loads(operation["after_state"])["created_task_ids"]]

    def test_zero_existing_specific_day_preview_apply_and_undo(self):
        before = self.counts()
        preview = self.service.preview_request("a", self.request([self.draft()]))
        self.assertEqual(before, self.counts())
        self.assertEqual(1, len(preview.draft_tasks))
        self.assertEqual(1, len(preview.outcomes))
        self.assertTrue(preview.outcomes[0].is_draft)
        self.assertIsNone(preview.outcomes[0].task_id)
        self.assertTrue(preview.outcomes[0].placed)
        self.assertTrue(all(
            segment["scheduled_date"] == self.start.isoformat()
            for segment in preview.draft_segments[preview.outcomes[0].candidate_id]
        ))

        operation_id = self.service.apply(preview)
        created_ids = self.created_task_ids(operation_id)
        self.assertEqual(1, len(created_ids))
        self.assertEqual("Machine Learning", self.db.get_task(created_ids[0], "a")["title"])
        self.assertTrue(self.db.get_task_segments(created_ids[0], "a"))
        with patch.object(services, "get_database", return_value=self.db):
            self.assertTrue(services.undo_smart_schedule(operation_id, "a"))
        self.assertIsNone(self.db.get_task(created_ids[0], "a"))
        after = self.counts()
        self.assertEqual(before["tasks"], after["tasks"])
        self.assertEqual(before["segments"], after["segments"])
        self.assertEqual(before["calendar"], after["calendar"])
        self.assertEqual("undone", self.db.get_schedule_operation(operation_id, "a")["status"])

    def test_zero_existing_date_range_accepts_multiple_drafts(self):
        end = self.start + timedelta(days=6)
        request = self.request([
            self.draft("Deep Learning", 180, priority=2),
            self.draft("Database", 90, priority=3),
        ], end=end)
        before = self.counts()
        preview = self.service.preview_request("a", request)
        self.assertEqual(before, self.counts())
        self.assertEqual(2, len(preview.draft_tasks))
        self.assertTrue(all(
            self.start <= date.fromisoformat(segment["scheduled_date"]) <= end
            for segments in preview.draft_segments.values() for segment in segments
        ))
        operation_id = self.service.apply(preview)
        self.assertEqual(2, len(self.created_task_ids(operation_id)))
        self.db.undo_schedule_operation(operation_id, "a")
        after = self.counts()
        self.assertEqual(before["tasks"], after["tasks"])
        self.assertEqual(before["segments"], after["segments"])
        self.assertEqual(before["calendar"], after["calendar"])
        self.assertEqual("undone", self.db.get_schedule_operation(operation_id, "a")["status"])

    def test_write_and_voice_unknown_tasks_become_drafts(self):
        for method in (PlanningInputMethod.WRITE, PlanningInputMethod.VOICE):
            intent = InterpretedPlanningIntent(tasks=[InterpretedTask(
                task_title=f"New {method.value}", remaining_minutes=75,
                duration_source="estimated",
            )])
            request = PlanningRequestInterpreter.build_request(
                intent, candidates=[], scope=PlanningScope.SPECIFIC_DAY,
                input_method=method, planning_start=self.start,
                planning_end=self.start, work_day_start=time(8),
            )
            self.assertTrue(request.tasks[0].is_draft)
            self.assertEqual(75, request.tasks[0].remaining_minutes)
            self.assertEqual(method, request.input_method)

    def test_write_partial_durations_use_estimates_without_clarification(self):
        intent = InterpretedPlanningIntent(
            clarification_needed=True,
            clarification_question="How much time for Math and Games?",
            tasks=[
                InterpretedTask(
                    task_title="Study Math", remaining_minutes=60,
                    duration_source="estimated",
                ),
                InterpretedTask(
                    task_title="Study OOP", remaining_minutes=120,
                    duration_source="user_explicit",
                ),
                InterpretedTask(
                    task_title="Play Games", remaining_minutes=45,
                    duration_source="estimated",
                ),
            ],
        )

        class FakeChain:
            def invoke(self, _payload):
                return intent

        class FakeParser:
            @staticmethod
            def get_format_instructions():
                return "structured JSON"

        interpreter = PlanningRequestInterpreter()
        interpreter._chain = FakeChain()
        interpreter._parser = FakeParser()
        request = interpreter.interpret(
            "study math and study oop 2h and play games",
            candidates=[], scope=PlanningScope.SPECIFIC_DAY,
            input_method=PlanningInputMethod.WRITE,
            planning_start=self.start, planning_end=self.start,
            work_day_start=time(8), local_date=self.start,
        )
        self.assertEqual(
            [("Study Math", 60, "estimated"),
             ("Study OOP", 120, "user_explicit"),
             ("Play Games", 45, "estimated")],
            [(task.title, task.remaining_minutes, task.duration_source)
             for task in request.tasks],
        )
        self.assertTrue(all(task.earliest_start is None for task in request.tasks))
        self.assertTrue(all(task.deadline is None for task in request.tasks))

    def test_existing_task_duration_changes_only_when_user_explicit(self):
        existing = {
            "task_id": 7, "title": "Study OOP", "estimated_minutes": 180,
            "actual_minutes": 30, "priority": 3,
            "earliest_start": None, "deadline": None,
        }
        common = dict(
            candidates=[existing], scope=PlanningScope.SPECIFIC_DAY,
            input_method=PlanningInputMethod.WRITE,
            planning_start=self.start, planning_end=self.start,
            work_day_start=time(8),
        )
        estimated = PlanningRequestInterpreter.build_request(
            InterpretedPlanningIntent(tasks=[InterpretedTask(
                task_title="Study OOP", remaining_minutes=90,
                duration_source="estimated",
            )]), **common,
        )
        explicit = PlanningRequestInterpreter.build_request(
            InterpretedPlanningIntent(tasks=[InterpretedTask(
                task_title="Study OOP", remaining_minutes=120,
                duration_source="user_explicit",
            )]), **common,
        )
        self.assertIsNone(estimated.tasks[0].remaining_minutes)
        self.assertIsNone(estimated.tasks[0].duration_source)
        self.assertEqual(120, explicit.tasks[0].remaining_minutes)
        self.assertEqual("user_explicit", explicit.tasks[0].duration_source)

    def test_arabic_draft_estimate_and_malformed_duration_fail_safely(self):
        request = PlanningRequestInterpreter.build_request(
            InterpretedPlanningIntent(tasks=[InterpretedTask(
                task_title="مذاكرة الرياضيات", remaining_minutes=75,
                duration_source="estimated",
            )]),
            candidates=[], scope=PlanningScope.SPECIFIC_DAY,
            input_method=PlanningInputMethod.WRITE,
            planning_start=self.start, planning_end=self.start,
            work_day_start=time(8),
        )
        self.assertEqual("مذاكرة الرياضيات", request.tasks[0].title)
        self.assertEqual(75, request.tasks[0].remaining_minutes)
        with self.assertRaisesRegex(ValueError, "provided together"):
            InterpretedTask(task_title="Malformed", remaining_minutes=60)

    def test_unknown_without_model_estimate_fails_safely_and_ambiguous_existing_clarifies(self):
        with self.assertRaisesRegex(ValueError, "valid duration estimate"):
            PlanningRequestInterpreter.build_request(
                InterpretedPlanningIntent(tasks=[InterpretedTask(task_title="New")]),
                candidates=[], scope=PlanningScope.SPECIFIC_DAY,
                input_method=PlanningInputMethod.WRITE, planning_start=self.start,
                planning_end=self.start, work_day_start=time(8),
            )
        duplicate = {
            "task_id": 1, "title": "Same", "estimated_minutes": 60,
            "actual_minutes": 0, "priority": 3, "earliest_start": None, "deadline": None,
        }
        with self.assertRaisesRegex(PlanningClarificationRequired, "ambiguous"):
            PlanningRequestInterpreter.build_request(
                InterpretedPlanningIntent(tasks=[InterpretedTask(task_title="Same")]),
                candidates=[duplicate, {**duplicate, "task_id": 2}],
                scope=PlanningScope.SPECIFIC_DAY,
                input_method=PlanningInputMethod.WRITE, planning_start=self.start,
                planning_end=self.start, work_day_start=time(8),
            )

    def test_existing_and_draft_share_one_preview_apply_and_exact_undo(self):
        existing_id = self.add_existing("Database", 60)
        request = self.request([
            PlanningTaskRequest(
                task_id=existing_id, title="Database", remaining_minutes=90,
                priority=1, earliest_start=datetime(2026, 10, 5, 10),
                deadline=datetime(2026, 10, 5, 18),
            ),
            self.draft("Machine Learning", 120, priority=2),
        ])
        original = dict(self.db.get_task(existing_id, "a"))
        preview = self.service.preview_request("a", request)
        self.assertIn(existing_id, preview.segments_by_task)
        self.assertEqual(1, len(preview.draft_segments))
        operation_id = self.service.apply(preview)
        created_id = self.created_task_ids(operation_id)[0]
        self.assertEqual(90, self.db.get_task(existing_id, "a")["estimated_minutes"])
        self.db.undo_schedule_operation(operation_id, "a")
        restored = self.db.get_task(existing_id, "a")
        for field in ("estimated_minutes", "priority", "earliest_start", "deadline",
                      "scheduled_start", "scheduled_end"):
            self.assertEqual(original[field], restored[field])
        self.assertEqual([], self.db.get_task_segments(existing_id, "a"))
        self.assertIsNone(self.db.get_task(created_id, "a"))

    def test_preview_is_pure_across_all_persistence_surfaces(self):
        existing_id = self.add_existing()
        before = self.counts()
        revision = self.db.get_plan_by_date("a", self.start)["schedule_revision"]
        request = self.request([
            PlanningTaskRequest(task_id=existing_id, title="Existing", priority=1),
            self.draft("Draft", 60),
        ])
        self.service.preview_request("a", request)
        self.assertEqual(before, self.counts())
        self.assertEqual(revision, self.db.get_plan_by_date("a", self.start)["schedule_revision"])
        self.assertEqual(3, self.db.get_task(existing_id, "a")["priority"])

    def test_apply_failure_rolls_back_draft_existing_updates_and_revision(self):
        existing_id = self.add_existing()
        plan = self.db.get_plan_by_date("a", self.start)
        before = self.counts()
        preview = self.service.preview_request("a", self.request([
            PlanningTaskRequest(task_id=existing_id, title="Existing", priority=1),
            self.draft("Draft", 60),
        ]))
        original_execute = self.db.execute

        def fail_operation_insert(sql, params=()):
            if "INSERT INTO schedule_operations" in sql:
                raise RuntimeError("injected operation failure")
            return original_execute(sql, params)

        with patch.object(self.db, "execute", side_effect=fail_operation_insert):
            with self.assertRaisesRegex(RuntimeError, "injected operation failure"):
                self.service.apply(preview)
        self.assertEqual(before, self.counts())
        self.assertEqual(3, self.db.get_task(existing_id, "a")["priority"])
        self.assertEqual(plan["schedule_revision"], self.db.get_plan_by_date("a", self.start)["schedule_revision"])
        self.assertEqual([], self.db.get_task_segments(existing_id, "a"))

    def test_stale_revision_rejects_apply_without_materializing_draft(self):
        existing_id = self.add_existing()
        preview = self.service.preview_request("a", self.request([
            PlanningTaskRequest(task_id=existing_id, title="Existing"),
            self.draft("Draft", 60),
        ]))
        plan_id = int(self.db.get_task(existing_id, "a")["plan_id"])
        self.db.execute(
            "UPDATE plans SET schedule_revision=schedule_revision+1 WHERE plan_id=?", (plan_id,),
        )
        self.db.connection.commit()
        before = self.counts()
        with self.assertRaises(StaleScheduleError):
            self.service.apply(preview)
        self.assertEqual(before, self.counts())

    def test_progress_race_rejects_apply_without_materializing_draft(self):
        existing_id = self.add_existing()
        preview = self.service.preview_request("a", self.request([
            PlanningTaskRequest(task_id=existing_id, title="Existing"),
            self.draft("Draft", 60),
        ]))
        self.db.execute("UPDATE tasks SET actual_minutes=15 WHERE task_id=?", (existing_id,))
        self.db.connection.commit()
        before = self.counts()
        with self.assertRaises(StaleScheduleError):
            self.service.apply(preview)
        self.assertEqual(before, self.counts())

    def test_draft_constraints_segment_rules_and_specific_day_boundary(self):
        preview = self.service.preview_request("a", self.request([
            self.draft(
                "Constrained", 180, earliest_start=datetime(2026, 10, 5, 10),
                deadline=datetime(2026, 10, 5, 15),
            ),
        ]))
        segments = next(iter(preview.draft_segments.values()))
        self.assertLessEqual(len(segments), 3)
        self.assertEqual(180, sum(
            int((datetime.combine(self.start, time.fromisoformat(s["scheduled_end"])) -
                 datetime.combine(self.start, time.fromisoformat(s["scheduled_start"]))).total_seconds() // 60)
            for s in segments
        ))
        for segment in segments:
            self.assertEqual(self.start.isoformat(), segment["scheduled_date"])
            self.assertGreaterEqual(time.fromisoformat(segment["scheduled_start"]), time(10))
            self.assertLessEqual(time.fromisoformat(segment["scheduled_end"]), time(15))
            self.assertGreaterEqual(
                int((datetime.combine(self.start, time.fromisoformat(segment["scheduled_end"])) -
                     datetime.combine(self.start, time.fromisoformat(segment["scheduled_start"]))).total_seconds() // 60),
                15,
            )

    def test_feasibility_precedes_priority_for_drafts(self):
        preview = self.service.preview_request("a", self.request([
            self.draft("Critical flexible", 60, priority=1),
            self.draft("Optional deadline", 60, priority=5,
                       deadline=datetime(2026, 10, 5, 9)),
        ]))
        by_title = {outcome.title: outcome for outcome in preview.outcomes}
        optional = by_title["Optional deadline"].segments[0]
        critical = by_title["Critical flexible"].segments[0]
        self.assertEqual(time(8), optional.scheduled_start)
        self.assertGreaterEqual(critical.scheduled_start, optional.scheduled_end)

    def test_draft_is_tenant_scoped(self):
        other_plan = self.db.create_plan("b", self.start, "other")
        self.db.add_task(other_plan, "b", "Other tenant", estimated_minutes=60)
        before_b = self.counts("b")
        operation_id = self.service.apply(
            self.service.preview_request("a", self.request([self.draft("Owned by A", 60)]))
        )
        self.assertEqual(before_b, self.counts("b"))
        created_id = self.created_task_ids(operation_id)[0]
        self.assertIsNone(self.db.get_task(created_id, "b"))

    def test_calendar_has_no_preview_side_effect_and_exports_after_apply(self):
        preview = self.service.preview_request("a", self.request([self.draft("Calendar Draft", 60)]))
        self.assertEqual(0, self.counts()["calendar"])
        self.assertEqual([], self.db.fetch_all("SELECT * FROM task_segments"))
        operation_id = self.service.apply(preview)
        created_id = self.created_task_ids(operation_id)[0]
        fake = FakeCalendar()
        with patch.object(services, "get_database", return_value=self.db), patch.object(
            services, "get_user_timezone", return_value="UTC",
        ):
            self.assertTrue(services.export_task_to_google_calendar(created_id, "a", client=fake))
        segments = self.db.get_task_segments(created_id, "a")
        self.assertEqual(len(segments), len(fake.created))
        self.assertTrue(all(row["calendar_provenance"] == "coachai_created" for row in segments))
        with patch.object(services, "get_database", return_value=self.db), patch.object(
            services, "_get_google_client", return_value=fake,
        ), patch.object(services, "get_user_timezone", return_value="UTC"):
            self.assertTrue(services.undo_smart_schedule(operation_id, "a"))
        self.assertEqual(len(segments), len(fake.deleted))
        self.assertIsNone(self.db.get_task(created_id, "a"))
        operation = self.db.get_schedule_operation(operation_id, "a")
        self.assertEqual("undone", operation["status"])
        self.assertIsNone(operation["calendar_reconciliation_error"])

    def test_twelve_week_limit_remains_enforced_for_drafts(self):
        valid_end = self.start + timedelta(weeks=12)
        preview = self.service.preview_request(
            "a", self.request([self.draft("Twelve weeks", 60)], end=valid_end),
        )
        self.assertEqual(valid_end, preview.planning_end)
        with self.assertRaisesRegex(ValueError, "at most 12 weeks"):
            StructuredPlanningRequest(
                scope=PlanningScope.DATE_RANGE,
                input_method=PlanningInputMethod.MANUAL,
                planning_start=self.start,
                planning_end=valid_end + timedelta(days=1),
                work_day_start=time(8),
                tasks=[self.draft("Too long", 60)],
            )


if __name__ == "__main__":
    unittest.main()
