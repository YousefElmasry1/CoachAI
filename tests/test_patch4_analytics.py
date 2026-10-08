import tempfile
import unittest
from datetime import date, time
from pathlib import Path

from analytics import (
    AnalyticsEngine,
    BurnoutCalculator,
    TaskSnapshot,
    build_intermediate_stats,
)
from database import Database
from scheduler_service import SchedulerService
from timezone_utils import user_today


def snapshot(
    task_id,
    *,
    category="Study",
    status="pending",
    estimated=30,
    actual=None,
    segments=(),
    is_break=False,
    is_fixed=False,
    plan_date="2026-09-30",
):
    first = segments[0][0] if segments else None
    last = segments[-1][1] if segments else None
    return TaskSnapshot(
        task_id=task_id,
        plan_date=plan_date,
        title=f"Task {task_id}",
        category_name=category,
        priority=3,
        estimated_minutes=estimated,
        actual_minutes=actual,
        scheduled_start=first,
        scheduled_end=last,
        status=status,
        failure_reason="Distracted" if status == "failed" else None,
        completed_at=None,
        created_at="2026-09-30 08:00:00",
        weekday=2,
        hour=int(first[:2]) if first else None,
        segments=tuple(segments),
        is_break=is_break,
        is_fixed_time=is_fixed,
    )


class Patch4AnalyticsPureTests(unittest.TestCase):
    def test_logical_counts_and_segment_metrics_have_separate_sources(self):
        rows = [
            snapshot(1, status="completed", estimated=60, actual=60,
                     segments=(("10:00:00", "10:30:00"), ("14:00:00", "14:30:00"))),
            snapshot(2, category="Break", estimated=30, status="completed", is_break=True, is_fixed=True,
                     segments=(("10:30:00", "11:00:00"),)),
            snapshot(3, category="Work", status="failed", actual=30,
                     segments=(("11:00:00", "11:30:00"),)),
            snapshot(4, status="pending", segments=(("14:30:00", "15:00:00"),)),
            snapshot(5, category="Work", status="completed", estimated=60, actual=60, is_fixed=True,
                     segments=(("15:00:00", "16:00:00"),)),
            snapshot(6, status="pending", estimated=45),
        ]
        stats = build_intermediate_stats(rows, today=date(2026, 9, 30))
        burnout = BurnoutCalculator().compute(stats)
        self.assertEqual(5, stats.total_tasks)
        self.assertEqual(2, stats.total_completed)
        self.assertEqual(1, stats.total_failed)
        self.assertEqual(6, sum(len(row.segments) for row in stats.schedule_snapshots))
        self.assertEqual(210.0, burnout.schedule_density)
        self.assertEqual(150.0, burnout.recovery_time)
        self.assertEqual(3.0, burnout.context_switching_score)
        self.assertAlmostEqual(1 / 5, burnout.time_fragmentation)

    def test_overlapping_and_touching_intervals_merge_once(self):
        rows = [
            snapshot(1, segments=(("09:00:00", "10:00:00"),), is_fixed=True),
            snapshot(2, category="Work", segments=(("09:30:00", "10:30:00"),), is_fixed=True),
            snapshot(3, segments=(("10:30:00", "11:00:00"),)),
            snapshot(4, category="Break", is_break=True, is_fixed=True,
                     segments=(("12:00:00", "12:30:00"),)),
        ]
        stats = build_intermediate_stats(rows, today=date(2026, 9, 30))
        burnout = BurnoutCalculator().compute(stats)
        self.assertEqual(150.0, burnout.schedule_density)
        self.assertEqual(60.0, burnout.recovery_time)

    def test_empty_unscheduled_and_break_only_data_are_not_fabricated(self):
        empty = BurnoutCalculator().compute(build_intermediate_stats([], today=date(2026, 9, 30)))
        self.assertEqual(0.0, empty.schedule_density)
        self.assertEqual(0.0, empty.recovery_time)

        only_break = [snapshot(
            1, category="Break", is_break=True, is_fixed=True,
            segments=(("12:00:00", "12:30:00"),),
        )]
        stats = build_intermediate_stats(only_break, today=date(2026, 9, 30))
        burnout = BurnoutCalculator().compute(stats)
        self.assertEqual(0, stats.total_tasks)
        self.assertEqual(30.0, burnout.schedule_density)
        self.assertEqual(0.0, burnout.context_switching_score)

        unscheduled = build_intermediate_stats([snapshot(2)], today=date(2026, 9, 30))
        self.assertEqual(1, unscheduled.total_tasks)
        self.assertEqual(0.0, BurnoutCalculator().compute(unscheduled).schedule_density)


class Patch4AnalyticsDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "analytics.db"
        self.db = Database(f"sqlite:///{path.as_posix()}")
        for user in ("a", "b"):
            self.db.execute(
                "INSERT INTO users (user_id,email,display_name,timezone) VALUES (?,?,?,'UTC')",
                (user, f"{user}@example.test", user.upper()),
            )
            plan_date = user_today(self.db, user).isoformat()
            self.db.execute(
                "INSERT INTO plans (user_id,plan_date,raw_input) VALUES (?,?,?)",
                (user, plan_date, "analytics"),
            )
        self.db.connection.commit()
        self.plan_a = self.db.fetch_one("SELECT plan_id FROM plans WHERE user_id='a'")["plan_id"]
        self.plan_b = self.db.fetch_one("SELECT plan_id FROM plans WHERE user_id='b'")["plan_id"]

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_loader_and_profile_are_tenant_scoped_and_do_not_duplicate_split_tasks(self):
        task_a = self.db.add_task(self.plan_a, "a", "A", estimated_minutes=60)
        self.db.replace_plan_segments(self.plan_a, "a", {task_a: [
            {"segment_index": 0, "scheduled_start": "10:00:00", "scheduled_end": "10:30:00"},
            {"segment_index": 1, "scheduled_start": "14:00:00", "scheduled_end": "14:30:00"},
        ]}, self.db.get_plan_by_id(self.plan_a, "a")["schedule_revision"])
        self.db.add_task(self.plan_b, "b", "B", estimated_minutes=30)

        profile_a = AnalyticsEngine(self.db).build_profile("a", window_days=1)
        profile_b = AnalyticsEngine(self.db).build_profile("b", window_days=1)
        self.assertEqual(1, profile_a.total_tasks)
        self.assertEqual(60.0, profile_a.burnout.schedule_density)
        self.assertEqual(1, profile_b.total_tasks)
        self.assertEqual(0.0, profile_b.burnout.schedule_density)

    def test_manual_move_and_undo_flow_through_analytics_source_of_truth(self):
        task = self.db.add_task(self.plan_a, "a", "Split", estimated_minutes=60)
        self.db.replace_plan_segments(self.plan_a, "a", {task: [
            {"segment_index": 0, "scheduled_start": "09:00:00", "scheduled_end": "09:30:00"},
            {"segment_index": 1, "scheduled_start": "10:00:00", "scheduled_end": "10:30:00"},
        ]}, self.db.get_plan_by_id(self.plan_a, "a")["schedule_revision"])
        engine = AnalyticsEngine(self.db)
        before = engine.build_profile("a", window_days=1).burnout
        self.assertEqual(30.0, before.recovery_time)

        service = SchedulerService(self.db)
        result = service.move_flexible_task(
            task, "a", time(12), self.db.get_plan_by_id(self.plan_a, "a")["schedule_revision"],
        )
        moved = engine.build_profile("a", window_days=1).burnout
        self.assertEqual(60.0, moved.schedule_density)
        self.assertEqual(0.0, moved.recovery_time)

        self.db.undo_schedule_operation(result.operation_id, "a")
        restored = engine.build_profile("a", window_days=1).burnout
        self.assertEqual(before.schedule_density, restored.schedule_density)
        self.assertEqual(before.recovery_time, restored.recovery_time)


if __name__ == "__main__":
    unittest.main()
