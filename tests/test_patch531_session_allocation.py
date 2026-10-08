import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch

from config import MAX_SEGMENTS_PER_TASK, MIN_SEGMENT_MINUTES, WORK_DAY_END
from database import Database
from future_planner import FuturePlanningService
from scheduler import SchedulingPreferences
from scheduler_service import SchedulerService
from session_suitability import (
    best_session_lengths,
    build_session_profile,
)
from smart_scheduler import SmartScheduler


class SessionSuitabilityUnitTests(unittest.TestCase):
    def lengths(self, minutes, title, urgency=0.0):
        profile = build_session_profile(minutes, title=title, urgency=urgency)
        return profile, best_session_lengths(minutes, profile)

    def test_short_tasks_remain_contiguous(self):
        for minutes in (30, 60, 90):
            with self.subTest(minutes=minutes):
                _, lengths = self.lengths(minutes, "Study mathematics")
                self.assertEqual((minutes,), lengths)

    def test_high_focus_duration_scales_into_human_sessions(self):
        _, three_hours = self.lengths(180, "Study algorithms")
        _, five_hours = self.lengths(300, "Study mathematics")
        _, eight_hours = self.lengths(480, "Machine learning research")
        self.assertEqual((90, 90), three_hours)
        self.assertEqual((150, 150), five_hours)
        self.assertEqual((160, 160, 160), eight_hours)

    def test_semantics_and_non_english_cues_change_suitability(self):
        study_profile, study = self.lengths(300, "مذاكرة رياضيات")
        leisure_profile, leisure = self.lengths(300, "Gaming")
        self.assertEqual("high_focus", study_profile.nature)
        self.assertEqual("leisure", leisure_profile.nature)
        self.assertEqual(2, len(study))
        self.assertEqual(1, len(leisure))
        self.assertLess(study_profile.preferred_minutes, leisure_profile.preferred_minutes)

    def test_explicit_deadline_pressure_can_justify_longer_session(self):
        relaxed_profile, relaxed = self.lengths(300, "Study Math")
        urgent_profile, urgent = self.lengths(300, "Study Math", urgency=1.0)
        self.assertGreater(urgent_profile.preferred_minutes, relaxed_profile.preferred_minutes)
        self.assertEqual(2, len(relaxed))
        self.assertEqual(1, len(urgent))


class SmartSchedulerSessionTests(unittest.TestCase):
    day = date(2026, 10, 5)

    @staticmethod
    def allocate(tasks, blocked=None):
        return SmartScheduler(SchedulingPreferences(
            time(8), blocked_slots=blocked or [],
        )).allocate(tasks, SmartSchedulerSessionTests.day)

    def test_five_hour_study_is_not_one_five_hour_segment(self):
        result = self.allocate([{
            "task_id": 1, "title": "Study Math", "category": "Study",
            "estimated_minutes": 300, "priority": 2,
        }], blocked=[(time(13), WORK_DAY_END)])
        task = result.placed_tasks[0]
        self.assertEqual(2, len(task.segments))
        self.assertEqual(300, sum(segment.duration_minutes for segment in task.segments))
        self.assertEqual(
            [(time(8), time(10, 30)), (time(10, 30), time(13))],
            [(segment.scheduled_start, segment.scheduled_end) for segment in task.segments],
        )

    def test_real_break_becomes_natural_session_boundary(self):
        result = self.allocate([
            {
                "task_id": 1, "title": "Study Math", "category": "Study",
                "estimated_minutes": 300, "priority": 2,
            },
            {
                "task_id": 2, "title": "Break", "category": "",
                "estimated_minutes": 15, "priority": 1, "is_fixed_time": True,
                "fixed_start": time(10), "fixed_end": time(10, 15), "is_break": True,
            },
        ], blocked=[(time(13, 15), WORK_DAY_END)])
        task = next(item for item in result.placed_tasks if item.task_id == 1)
        self.assertEqual(
            [(time(8), time(10)), (time(10, 15), time(13, 15))],
            [(segment.scheduled_start, segment.scheduled_end) for segment in task.segments],
        )

    def test_long_task_can_yield_spare_capacity_to_flexible_peer(self):
        result = self.allocate([
            {"task_id": 1, "title": "Study Math", "category": "Study",
             "estimated_minutes": 300, "priority": 1},
            {"task_id": 2, "title": "Email tutor", "category": "Admin",
             "estimated_minutes": 60, "priority": 2},
        ], blocked=[(time(14), WORK_DAY_END)])
        long_task = next(item for item in result.placed_tasks if item.task_id == 1)
        peer = next(item for item in result.placed_tasks if item.task_id == 2)
        self.assertEqual(2, len(long_task.segments))
        self.assertEqual((time(10, 30), time(11, 30)),
                         (peer.scheduled_start, peer.scheduled_end))
        intervals = sorted(
            (segment.scheduled_start, segment.scheduled_end, item.task_id)
            for item in result.placed_tasks for segment in item.segments
            if not item.is_fixed_time
        )
        self.assertTrue(all(left[1] <= right[0] for left, right in zip(intervals, intervals[1:])))

    def test_constraints_and_segment_invariants_remain_hard(self):
        result = self.allocate([{
            "task_id": 1, "title": "Study algorithms", "category": "Study",
            "estimated_minutes": 180, "priority": 1,
            "earliest_start": datetime(2026, 10, 5, 9),
            "deadline": datetime(2026, 10, 5, 12, 30),
        }], blocked=[(time(10, 30), time(10, 45)), (time(12, 15), WORK_DAY_END)])
        task = result.placed_tasks[0]
        self.assertLessEqual(len(task.segments), MAX_SEGMENTS_PER_TASK)
        self.assertEqual(180, sum(segment.duration_minutes for segment in task.segments))
        self.assertTrue(all(segment.duration_minutes >= MIN_SEGMENT_MINUTES
                            for segment in task.segments))
        self.assertTrue(all(segment.scheduled_start >= time(9) for segment in task.segments))
        self.assertTrue(all(segment.scheduled_end <= time(12, 30) for segment in task.segments))
        self.assertTrue(all(not (segment.scheduled_start < time(10, 45)
                                and segment.scheduled_end > time(10, 30))
                            for segment in task.segments))


class Patch531IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(f"sqlite:///{(Path(self.tmp.name) / 'patch531.db').as_posix()}")
        self.db.execute(
            "INSERT INTO users (user_id,email,display_name,timezone) VALUES (?,?,?,?)",
            ("a", "a@test.local", "A", "UTC"),
        )
        self.db.connection.commit()
        self.day = date(2026, 10, 5)
        self.today_patcher = patch("future_planner.user_today", return_value=self.day)
        self.today_patcher.start()
        self.addCleanup(self.today_patcher.stop)
        self.plan = self.db.create_plan("a", self.day, "patch 5.3.1")
        self.future = FuturePlanningService(self.db)
        self.scheduler = SchedulerService(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def task(self, title, minutes, *, start=None, deadline=None):
        end = None
        if start is not None:
            end = (datetime.combine(date.min, start) + timedelta(minutes=minutes)).time()
        return self.db.add_task(
            self.plan, "a", title, estimated_minutes=minutes, priority=2,
            scheduled_start=start.isoformat() if start else None,
            scheduled_end=end.isoformat() if end else None,
            deadline=deadline,
        )

    def test_future_uses_persisted_category_metadata(self):
        category_id = self.db.create_category("a", "Study", "#336699")
        task_id = self.db.add_task(
            self.plan, "a", "Project 7", category_id=category_id,
            estimated_minutes=300, priority=2,
        )
        self.block_after()
        outcome = next(item for item in self.preview().outcomes if item.task_id == task_id)
        self.assertEqual(2, len(outcome.segments))

    def block_after(self, value=time(13)):
        self.db.upsert_google_calendar_event(
            "a", f"block-{value.isoformat()}", "Protected", value.isoformat(),
            WORK_DAY_END.isoformat(), self.day.isoformat(), "primary",
        )

    def preview(self):
        return self.future.preview(
            "a", self.day, self.day, SchedulingPreferences(time(8)),
        )

    def test_future_preview_apply_and_undo_use_intelligent_sessions_exactly(self):
        task_id = self.task("Study Math", 300)
        self.block_after()
        before = self.db._schedule_snapshot(self.plan, "a")
        preview = self.preview()
        outcome = next(item for item in preview.outcomes if item.task_id == task_id)
        self.assertEqual(2, len(outcome.segments))
        self.assertEqual([], self.db.get_task_segments(task_id, "a"))
        operation = self.future.apply(preview)
        stored = self.db.get_task_segments(task_id, "a")
        self.assertEqual(preview.segments_by_task[task_id], [{
            "segment_index": int(row["segment_index"]),
            "scheduled_date": str(row["effective_date"]),
            "scheduled_start": str(row["scheduled_start"]),
            "scheduled_end": str(row["scheduled_end"]),
        } for row in stored])
        self.db.undo_schedule_operation(operation, "a")
        self.assertEqual(before, self.db._schedule_snapshot(self.plan, "a"))

    def test_future_semantics_and_deadline_pressure_change_result(self):
        leisure = self.task("Gaming", 300)
        self.block_after()
        outcome = next(item for item in self.preview().outcomes if item.task_id == leisure)
        self.assertEqual(1, len(outcome.segments))

        self.db.close()
        self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(f"sqlite:///{(Path(self.tmp.name) / 'urgent.db').as_posix()}")
        self.db.execute(
            "INSERT INTO users (user_id,email,display_name,timezone) VALUES (?,?,?,?)",
            ("a", "a@test.local", "A", "UTC"),
        )
        self.db.connection.commit()
        self.plan = self.db.create_plan("a", self.day, "urgent")
        self.future = FuturePlanningService(self.db)
        urgent = self.task("Study Math", 300, deadline=datetime(2026, 10, 5, 13))
        outcome = next(item for item in self.preview().outcomes if item.task_id == urgent)
        self.assertEqual(1, len(outcome.segments))

    def test_future_multiple_windows_choose_quality_distribution(self):
        task_id = self.task("Study Math", 300)
        for index, (start, end) in enumerate((
            (time(10), time(11)), (time(13), time(14)), (time(16), WORK_DAY_END),
        )):
            self.db.upsert_google_calendar_event(
                "a", f"window-block-{index}", "Protected", start.isoformat(),
                end.isoformat(), self.day.isoformat(), "primary",
            )
        outcome = next(item for item in self.preview().outcomes if item.task_id == task_id)
        self.assertEqual([100, 100, 100], [segment.minutes for segment in outcome.segments])
        self.assertEqual(3, len(outcome.segments))

    def test_workload_balance_breaks_ties_without_overriding_session_quality(self):
        windows = []
        for offset in range(3):
            start = datetime.combine(self.day + timedelta(days=offset), time(8))
            windows.append((start, start + timedelta(minutes=300)))
        candidate = self.future._best_placement(
            windows, 300,
            {self.day: 0, self.day + timedelta(days=1): 240,
             self.day + timedelta(days=2): 0},
            (), task={"title": "Study Math", "category_name": "Study"},
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(2, len(candidate))
        self.assertEqual(2, len({segment.scheduled_date for segment in candidate}))
        self.assertNotIn(self.day + timedelta(days=1),
                         {segment.scheduled_date for segment in candidate})

    def test_manual_move_peer_repair_uses_same_session_model(self):
        moved = self.task("Move me", 60, start=time(8))
        long_peer = self.task("Study Math", 300, start=time(9))
        revision = int(self.db.get_plan_by_id(self.plan, "a")["schedule_revision"])
        self.scheduler.move_flexible_task(
            moved, "a", time(15), revision,
            blocked_slots=[(time(14), time(15)), (time(16), WORK_DAY_END)],
        )
        peer_segments = self.db.get_task_segments(long_peer, "a")
        self.assertEqual(2, len(peer_segments))
        self.assertEqual(300, sum(
            int((datetime.combine(date.min, time.fromisoformat(str(row["scheduled_end"]))) -
                 datetime.combine(date.min, time.fromisoformat(str(row["scheduled_start"]))))
                .total_seconds() // 60)
            for row in peer_segments
        ))
