import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path

from analytics import AnalyticsEngine
from database import Database, OwnershipError, StaleScheduleError
from scheduler_service import SchedulerService
from timezone_utils import user_today


class Patch42ManualMoveRepairTests(unittest.TestCase):
    """Controlled current-day Manual Move and local-repair scenarios."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "patch42.db"
        self.db = Database(f"sqlite:///{path.as_posix()}")
        for user_id in ("a", "b"):
            self.db.execute(
                "INSERT INTO users (user_id,email,display_name) VALUES (?,?,?)",
                (user_id, f"{user_id}@example.test", user_id.upper()),
            )
            self.db.execute(
                "INSERT INTO plans (user_id,plan_date,raw_input) VALUES (?,?,?)",
                (user_id, user_today(self.db, user_id).isoformat(), "patch 4.2"),
            )
        self.db.connection.commit()
        self.plan = int(self.db.fetch_one(
            "SELECT plan_id FROM plans WHERE user_id='a'"
        )["plan_id"])
        self.other_plan = int(self.db.fetch_one(
            "SELECT plan_id FROM plans WHERE user_id='b'"
        )["plan_id"])
        self.service = SchedulerService(self.db)

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    @staticmethod
    def minutes(start, end):
        return (int(end[:2]) * 60 + int(end[3:5])) - (
            int(start[:2]) * 60 + int(start[3:5])
        )

    def revision(self, plan_id=None, user_id="a"):
        return int(self.db.get_plan_by_id(plan_id or self.plan, user_id)["schedule_revision"])

    def add_task(
        self,
        title,
        windows,
        *,
        minutes=None,
        priority=3,
        fixed=False,
        is_break=False,
        status="pending",
        started=False,
        running=False,
        provenance="legacy_unknown",
        event_id=None,
        earliest=None,
        deadline=None,
        user_id="a",
        plan_id=None,
    ):
        target_plan = plan_id or self.plan
        duration = minutes if minutes is not None else sum(
            self.minutes(start, end) for start, end in windows
        )
        task_id = self.db.add_task(
            target_plan,
            user_id,
            title,
            estimated_minutes=duration,
            priority=priority,
            is_fixed_time=fixed or is_break,
            is_break=is_break,
            scheduled_start=windows[0][0] if len(windows) == 1 else None,
            scheduled_end=windows[0][1] if len(windows) == 1 else None,
            calendar_provenance=provenance,
            google_event_id=event_id,
            google_calendar_id="primary" if event_id else None,
            earliest_start=earliest,
            deadline=deadline,
        )
        if len(windows) > 1:
            for index, (start, end) in enumerate(windows):
                self.db.execute(
                    """INSERT INTO task_segments
                       (task_id,segment_index,scheduled_start,scheduled_end,
                        google_event_id,google_calendar_id,calendar_provenance,calendar_sync_status)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        task_id, index, f"{start}:00", f"{end}:00",
                        f"{event_id}-{index}" if event_id else None,
                        "primary" if event_id else None,
                        provenance, "synced" if event_id else "not_synced",
                    ),
                )
            self.db.execute(
                "UPDATE tasks SET scheduled_start=?,scheduled_end=? WHERE task_id=?",
                (f"{windows[0][0]}:00", f"{windows[-1][1]}:00", task_id),
            )
        elif event_id:
            segment = self.db.get_task_segments(task_id, user_id)[0]
            self.db.update_segment_calendar_state(
                int(segment["segment_id"]), user_id,
                google_event_id=event_id,
                google_calendar_id="primary",
                calendar_provenance=provenance,
                calendar_sync_status="synced",
            )
        self.db.execute(
            """UPDATE tasks SET status=?,started_at=?,timer_segment_started_at=?
               WHERE task_id=?""",
            (
                status,
                "2026-10-01 07:00:00" if started else None,
                "2026-10-01 07:30:00" if running else None,
                task_id,
            ),
        )
        self.db.connection.commit()
        return task_id

    def move(self, task_id, start, *, blocked=None, revision=None, user_id="a"):
        return self.service.move_flexible_task(
            task_id,
            user_id,
            start,
            self.revision(user_id=user_id) if revision is None else revision,
            blocked_slots=blocked or [],
        )

    def windows(self, task_id, user_id="a"):
        return [
            (row["scheduled_start"], row["scheduled_end"])
            for row in self.db.get_task_segments(task_id, user_id)
        ]

    def snapshot(self, user_id="a", plan_id=None):
        return self.db._schedule_snapshot(plan_id or self.plan, user_id)

    # 1
    def test_01_free_interval_replans_scheduled_flexible_peers(self):
        moved = self.add_task("OOP", [("10:00", "11:00")])
        other = self.add_task("DB", [("11:00", "12:00")])
        result = self.move(moved, time(18))
        self.assertEqual([("18:00:00", "19:00:00")], self.windows(moved))
        self.assertEqual([("10:00:00", "11:00:00")], self.windows(other))
        self.assertEqual(2, len(result.movements))

    # 2
    def test_02_opened_earlier_window_is_recalculated(self):
        moved = self.add_task("OOP", [("10:00", "11:00")], priority=3)
        late = self.add_task("Algorithms", [("17:00", "18:00")], priority=2)
        self.move(moved, time(15))
        self.assertEqual([("10:00:00", "11:00:00")], self.windows(late))

    # 3
    def test_03_higher_priority_feasible_task_fills_opened_gap(self):
        moved = self.add_task("OOP", [("10:00", "11:00")])
        low = self.add_task("Low", [("16:00", "16:45")], priority=4)
        high = self.add_task("High", [("17:00", "18:00")], priority=2)
        self.move(moved, time(15))
        self.assertEqual([("10:00:00", "11:00:00")], self.windows(high))
        self.assertEqual([("11:00:00", "11:45:00")], self.windows(low))

    # 4
    def test_04_full_smart_replan_places_critical_first(self):
        moved = self.add_task("OOP", [("10:00", "10:30")])
        critical = self.add_task("Critical", [("17:00", "18:00")], priority=1)
        high = self.add_task("High", [("16:00", "16:30")], priority=2)
        self.move(moved, time(15))
        self.assertEqual([("10:00:00", "11:00:00")], self.windows(critical))
        self.assertEqual([("11:00:00", "11:30:00")], self.windows(high))

    # 5
    def test_05_priority_orders_full_smart_replan(self):
        moved = self.add_task("OOP", [("10:00", "10:30")])
        critical = self.add_task("Critical", [("17:00", "18:30")], priority=1)
        medium = self.add_task("Medium", [("16:00", "16:30")], priority=3)
        self.move(moved, time(15))
        self.assertEqual([("11:30:00", "12:00:00")], self.windows(medium))
        self.assertEqual([("10:00:00", "11:30:00")], self.windows(critical))

    # 6
    def test_06_one_flexible_conflict_replans_scheduled_flexible_set(self):
        moved = self.add_task("OOP", [("15:00", "16:00")])
        conflict = self.add_task("OS", [("10:00", "11:00")])
        untouched = self.add_task("DB", [("11:00", "12:00")])
        result = self.move(moved, time(10))
        self.assertEqual([("10:00:00", "11:00:00")], self.windows(moved))
        self.assertEqual([("11:00:00", "12:00:00")], self.windows(conflict))
        self.assertEqual([("12:00:00", "13:00:00")], self.windows(untouched))
        self.assertEqual(3, len(result.movements))

    # 7
    def test_07_flexible_conflict_chain_is_repaired_locally(self):
        # Removing the split task exposes four 15-minute fragments.  OS cannot
        # use them under the three-segment cap, so the local pool must cascade.
        moved = self.add_task(
            "OOP", [("12:00", "12:15"), ("13:00", "13:15"), ("14:00", "14:15")]
        )
        conflict = self.add_task("OS", [("10:00", "11:00")])
        chain = [
            self.add_task("DB", [("11:00", "12:00")]),
            self.add_task("Network", [("12:15", "13:00")]),
            self.add_task("Algorithms", [("13:15", "14:00")]),
            self.add_task("Review", [("14:15", "14:45")]),
        ]
        result = self.move(moved, time(10), blocked=[(time(15), time(23, 59))])
        self.assertEqual([("10:00:00", "10:45:00")], self.windows(moved))
        self.assertNotEqual([("10:00:00", "11:00:00")], self.windows(conflict))
        self.assertGreaterEqual(len(result.movements), 4)
        for task_id in [conflict, *chain]:
            self.assertTrue(self.windows(task_id))

    # 8
    def test_08_chain_repair_recalculates_remaining_free_window(self):
        moved = self.add_task(
            "OOP", [("12:00", "12:15"), ("13:00", "13:15"), ("14:00", "14:15")]
        )
        self.add_task("OS", [("10:00", "11:00")])
        self.add_task("DB", [("11:00", "12:00")])
        self.add_task("Network", [("12:15", "13:00")])
        self.add_task("Algorithms", [("13:15", "14:00")])
        self.add_task("Review", [("14:15", "14:45")])
        self.move(moved, time(10), blocked=[(time(15), time(23, 59))])
        occupied = sorted(
            (row["scheduled_start"], row["scheduled_end"])
            for row in self.db.get_plan_segments(self.plan, "a")
        )
        self.assertEqual("14:45:00", max(end for _, end in occupied))
        self.assertFalse(any(start < "15:00:00" and end > "14:45:00" for start, end in occupied))

    # 9
    def test_09_later_flexible_can_use_useful_vacated_window(self):
        moved = self.add_task("OOP", [("10:00", "11:00")])
        candidate = self.add_task("Candidate", [("19:00", "20:00")], priority=2)
        self.move(moved, time(16))
        self.assertEqual([("10:00:00", "11:00:00")], self.windows(candidate))

    # 10
    def test_10_fixed_task_rejects_without_writes(self):
        moved = self.add_task("OOP", [("09:00", "10:00")])
        self.add_task("Fixed OS", [("10:00", "11:00")], fixed=True)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "Fixed OS"):
            self.move(moved, time(10))
        self.assertEqual(before, self.snapshot())

    # 11
    def test_11_started_task_rejects_without_writes(self):
        moved = self.add_task("OOP", [("09:00", "10:00")])
        self.add_task("Started OS", [("10:00", "11:00")], status="in_progress", started=True, running=True)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "already started"):
            self.move(moved, time(10))
        self.assertEqual(before, self.snapshot())

    # 12
    def test_12_paused_started_task_rejects_without_writes(self):
        moved = self.add_task("OOP", [("09:00", "10:00")])
        paused = self.add_task("Paused", [("10:00", "11:00")], status="in_progress", started=True)
        before = self.windows(paused)
        with self.assertRaisesRegex(ValueError, "already started"):
            self.move(moved, time(10))
        self.assertEqual(before, self.windows(paused))

    # 13
    def test_13_completed_task_is_never_moved(self):
        moved = self.add_task("OOP", [("09:00", "10:00")])
        done = self.add_task("Done", [("10:00", "11:00")], status="completed", started=True)
        before = self.windows(done)
        with self.assertRaisesRegex(ValueError, "completed"):
            self.move(moved, time(10))
        self.assertEqual(before, self.windows(done))

    # 14
    def test_14_failed_task_is_never_moved(self):
        moved = self.add_task("OOP", [("09:00", "10:00")])
        failed = self.add_task("Failed", [("10:00", "11:00")], status="failed", started=True)
        before = self.windows(failed)
        with self.assertRaisesRegex(ValueError, "failed"):
            self.move(moved, time(10))
        self.assertEqual(before, self.windows(failed))

    # 15
    def test_15_break_blocks_movement(self):
        moved = self.add_task("OOP", [("09:00", "10:00")])
        break_id = self.add_task("Lunch", [("10:00", "10:30")], is_break=True)
        with self.assertRaisesRegex(ValueError, "Lunch"):
            self.move(moved, time(10))
        self.assertEqual([("10:00:00", "10:30:00")], self.windows(break_id))

    # 16
    def test_16_imported_calendar_task_blocks_movement(self):
        moved = self.add_task("OOP", [("09:00", "10:00")])
        imported = self.add_task(
            "Imported meeting", [("10:00", "11:00")],
            provenance="imported", event_id="imported-event",
        )
        with self.assertRaisesRegex(ValueError, "Calendar event"):
            self.move(moved, time(10))
        self.assertEqual([("10:00:00", "11:00:00")], self.windows(imported))

    # 17
    def test_17_coachai_owned_event_moves_and_marks_pending(self):
        moved = self.add_task(
            "Owned", [("09:00", "10:00")],
            provenance="coachai_created", event_id="owned-event",
        )
        self.move(moved, time(12))
        segment = self.db.get_task_segments(moved, "a")[0]
        self.assertEqual("owned-event", segment["google_event_id"])
        self.assertEqual("pending_update", segment["calendar_sync_status"])

    # 18
    def test_18_legacy_unknown_calendar_identity_is_protected(self):
        moved = self.add_task("OOP", [("09:00", "10:00")])
        legacy = self.add_task(
            "Legacy event", [("10:00", "11:00")],
            provenance="legacy_unknown", event_id="legacy-event",
        )
        with self.assertRaisesRegex(ValueError, "Calendar event"):
            self.move(moved, time(10))
        self.assertEqual([("10:00:00", "11:00:00")], self.windows(legacy))

    # 19
    def test_19_scheduled_flexible_peer_participates_across_fixed_anchor(self):
        moved = self.add_task("OOP", [("10:00", "11:00")])
        self.add_task("Boundary", [("15:00", "16:00")], fixed=True)
        unrelated = self.add_task("Unrelated", [("20:00", "21:00")])
        self.move(moved, time(12))
        self.assertEqual([("10:00:00", "11:00:00")], self.windows(unrelated))

    # 20
    def test_20_adjacent_equal_priority_task_uses_smart_schedule(self):
        moved = self.add_task("OOP", [("10:00", "10:30")], priority=3)
        adjacent = self.add_task("Adjacent", [("10:30", "11:00")], priority=3)
        self.move(moved, time(15))
        self.assertEqual([("10:00:00", "10:30:00")], self.windows(adjacent))

    # 21
    def test_21_full_replan_uses_available_whole_window(self):
        moved = self.add_task("OOP", [("10:00", "10:30")])
        long_task = self.add_task("Long", [("17:00", "18:00")])
        self.move(moved, time(15))
        self.assertEqual([("10:00:00", "11:00:00")], self.windows(long_task))

    # 22
    def test_22_multiple_old_segments_create_multiple_free_windows(self):
        moved = self.add_task("Split", [("10:00", "10:30"), ("12:00", "12:30")])
        first = self.add_task("First", [("17:00", "17:30")], priority=2)
        second = self.add_task("Second", [("18:00", "18:30")], priority=3)
        self.move(moved, time(15))
        self.assertEqual([("10:00:00", "10:30:00")], self.windows(first))
        self.assertEqual([("10:30:00", "11:00:00")], self.windows(second))

    # 23
    def test_23_work_day_end_is_enforced(self):
        moved = self.add_task("Late", [("20:00", "21:00")])
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "work-day"):
            self.move(moved, time(23, 30))
        self.assertEqual(before, self.snapshot())

    # 24
    def test_24_midnight_rollover_is_rejected(self):
        moved = self.add_task("Late", [("20:00", "22:00")])
        with self.assertRaisesRegex(ValueError, "work-day"):
            self.move(moved, time(23))

    # 25
    def test_25_minimum_segment_rule_remains_enforced(self):
        moved = self.add_task("Move", [("10:00", "10:30")])
        self.move(moved, time(15))
        for row in self.db.get_plan_segments(self.plan, "a"):
            self.assertGreaterEqual(self.minutes(row["scheduled_start"], row["scheduled_end"]), 15)

    # 26
    def test_26_segment_cap_remains_enforced(self):
        moved = self.add_task("Split", [("09:00", "09:20"), ("10:00", "10:20"), ("11:00", "11:20")])
        self.move(moved, time(15))
        self.assertLessEqual(len(self.db.get_task_segments(moved, "a")), 3)

    # 27
    def test_27_exact_duration_is_preserved(self):
        moved = self.add_task("Split", [("09:00", "09:30"), ("11:00", "11:30")])
        self.move(moved, time(15))
        total = sum(self.minutes(start, end) for start, end in self.windows(moved))
        self.assertEqual(60, total)

    # 28
    def test_28_complete_result_has_no_illegal_overlaps(self):
        moved = self.add_task("OOP", [("15:00", "16:00")])
        self.add_task("OS", [("10:00", "11:00")])
        self.add_task("DB", [("11:00", "12:00")])
        self.move(moved, time(10))
        segments = self.db.get_plan_segments(self.plan, "a")
        for index, left in enumerate(segments):
            for right in segments[index + 1:]:
                if int(left["task_id"]) != int(right["task_id"]):
                    self.assertFalse(
                        left["scheduled_start"] < right["scheduled_end"]
                        and right["scheduled_start"] < left["scheduled_end"]
                    )

    # 29
    def test_29_entire_move_and_repair_creates_one_operation(self):
        moved = self.add_task("OOP", [("15:00", "16:00")])
        self.add_task("OS", [("10:00", "11:00")])
        before = int(self.db.fetch_one("SELECT COUNT(*) n FROM schedule_operations")["n"])
        result = self.move(moved, time(10))
        after = int(self.db.fetch_one("SELECT COUNT(*) n FROM schedule_operations")["n"])
        self.assertEqual(before + 1, after)
        self.assertIsNotNone(self.db.get_schedule_operation(result.operation_id, "a"))

    # 30
    def test_30_undo_restores_exact_pre_operation_state(self):
        moved = self.add_task("OOP", [("10:00", "11:00")])
        self.add_task("Algorithms", [("17:00", "18:00")], priority=2)
        before = self.snapshot()
        result = self.move(moved, time(15))
        self.db.undo_schedule_operation(result.operation_id, "a")
        self.assertEqual(before, self.snapshot())

    # 31
    def test_31_undo_restores_every_repaired_task(self):
        moved = self.add_task("OOP", [("10:00", "11:00")])
        candidate = self.add_task("Algorithms", [("17:00", "18:00")], priority=2)
        before_moved = self.windows(moved)
        before_candidate = self.windows(candidate)
        result = self.move(moved, time(15))
        self.db.undo_schedule_operation(result.operation_id, "a")
        self.assertEqual(before_moved, self.windows(moved))
        self.assertEqual(before_candidate, self.windows(candidate))

    # 32
    def test_32_stale_undo_is_rejected(self):
        moved = self.add_task("OOP", [("10:00", "11:00")])
        result = self.move(moved, time(15))
        self.add_task("Newer work", [("18:00", "19:00")])
        with self.assertRaises(StaleScheduleError):
            self.db.undo_schedule_operation(result.operation_id, "a")

    # 33
    def test_33_stale_manual_move_revision_writes_nothing(self):
        moved = self.add_task("OOP", [("10:00", "11:00")])
        stale = self.revision()
        self.db.update_task(moved, "a", title="Renamed")
        before = self.snapshot()
        with self.assertRaises(StaleScheduleError):
            self.move(moved, time(15), revision=stale)
        self.assertEqual(before, self.snapshot())

    # 34
    def test_34_progress_state_race_rejects_without_writes(self):
        moved = self.add_task("OOP", [("10:00", "11:00")])
        candidate = self.add_task("Candidate", [("17:00", "18:00")], priority=2)
        before = self.snapshot()
        original = self.db.get_tasks_with_segments
        calls = 0

        def starts_while_computing(plan_id, user_id):
            nonlocal calls
            calls += 1
            rows = original(plan_id, user_id)
            if calls == 2:
                changed = [dict(row) for row in rows]
                for row in changed:
                    if int(row["task_id"]) == candidate:
                        row["status"] = "in_progress"
                        row["started_at"] = "2026-10-01 08:00:00"
                return changed
            return rows

        self.db.get_tasks_with_segments = starts_while_computing
        try:
            with self.assertRaises(StaleScheduleError):
                self.move(moved, time(15))
        finally:
            self.db.get_tasks_with_segments = original
        self.assertEqual(before, self.snapshot())

    # 35
    def test_35_cross_tenant_manual_move_is_rejected(self):
        other = self.add_task(
            "User B", [("10:00", "11:00")], user_id="b", plan_id=self.other_plan,
        )
        before = self.snapshot("b", self.other_plan)
        with self.assertRaises(OwnershipError):
            self.service.move_flexible_task(other, "a", time(15), self.revision())
        self.assertEqual(before, self.snapshot("b", self.other_plan))

    # 36
    def test_36_repair_candidates_never_cross_tenants(self):
        moved = self.add_task("User A", [("10:00", "11:00")])
        other = self.add_task(
            "User B candidate", [("17:00", "18:00")], priority=1,
            user_id="b", plan_id=self.other_plan,
        )
        before = self.windows(other, "b")
        self.move(moved, time(15))
        self.assertEqual(before, self.windows(other, "b"))

    # 37
    def test_37_undo_is_tenant_scoped(self):
        moved = self.add_task("OOP", [("10:00", "11:00")])
        result = self.move(moved, time(15))
        with self.assertRaises(OwnershipError):
            self.db.undo_schedule_operation(result.operation_id, "b")

    # 38
    def test_38_history_shape_remains_one_logical_task(self):
        moved = self.add_task("Split", [("10:00", "10:30"), ("12:00", "12:30")])
        self.move(moved, time(15))
        rows = [row for row in self.db.get_tasks_with_segments(self.plan, "a") if row["task_id"] == moved]
        self.assertEqual(1, len(rows))
        self.assertEqual(1, len(rows[0]["segments"]))

    # 39
    def test_39_timer_identity_remains_the_same_task_id(self):
        moved = self.add_task("Timer task", [("10:00", "11:00")])
        self.move(moved, time(15))
        self.assertEqual(moved, int(self.db.get_task(moved, "a")["task_id"]))
        self.assertEqual(1, int(self.db.fetch_one(
            "SELECT COUNT(*) n FROM tasks WHERE task_id=?", (moved,)
        )["n"]))

    # 40
    def test_40_analytics_logical_count_does_not_change(self):
        moved = self.add_task("Split", [("10:00", "10:30"), ("12:00", "12:30")])
        engine = AnalyticsEngine(self.db)
        before = engine.build_profile("a", window_days=1).total_tasks
        self.move(moved, time(15))
        after = engine.build_profile("a", window_days=1).total_tasks
        self.assertEqual(before, after)

    # 41
    def test_41_physical_segment_count_tracks_result_without_duplication(self):
        moved = self.add_task("Split", [("10:00", "10:30"), ("12:00", "12:30")])
        self.assertEqual(2, len(self.db.get_task_segments(moved, "a")))
        self.move(moved, time(15))
        self.assertEqual(1, len(self.db.get_task_segments(moved, "a")))
        self.assertEqual(1, len(self.db.get_tasks_with_segments(self.plan, "a")))

    # 42
    def test_42_analytics_reflect_resulting_segments(self):
        moved = self.add_task("Split", [("09:00", "09:30"), ("10:00", "10:30")])
        engine = AnalyticsEngine(self.db)
        before = engine.build_profile("a", window_days=1).burnout
        self.assertEqual(30.0, before.recovery_time)
        self.move(moved, time(15))
        after = engine.build_profile("a", window_days=1).burnout
        self.assertEqual(0.0, after.recovery_time)
        self.assertEqual(60.0, after.schedule_density)

    # 43
    def test_43_analytics_returns_to_previous_state_after_undo(self):
        moved = self.add_task("Split", [("09:00", "09:30"), ("10:00", "10:30")])
        engine = AnalyticsEngine(self.db)
        before = engine.build_profile("a", window_days=1).burnout
        result = self.move(moved, time(15))
        self.db.undo_schedule_operation(result.operation_id, "a")
        restored = engine.build_profile("a", window_days=1).burnout
        self.assertEqual(before.schedule_density, restored.schedule_density)
        self.assertEqual(before.recovery_time, restored.recovery_time)
        self.assertEqual(before.time_fragmentation, restored.time_fragmentation)

    # 44
    def test_44_calendar_partial_failure_is_durable_and_retryable(self):
        import services

        moved = self.add_task(
            "Owned split", [("09:00", "09:30"), ("11:00", "11:30")],
            provenance="coachai_created", event_id="owned",
        )
        result = self.move(moved, time(15))

        class FailingCalendar:
            def delete_event(self, calendar_id, event_id):
                raise RuntimeError("temporary failure")

        original = services.get_database
        services.get_database = lambda: self.db
        try:
            reconciliation = services.reconcile_schedule_calendar_operation(
                result.operation_id, "a", client=FailingCalendar(),
            )
        finally:
            services.get_database = original
        self.assertTrue(reconciliation["errors"])
        operation = self.db.get_schedule_operation(result.operation_id, "a")
        self.assertEqual(
            "Calendar provider operation failed.",
            operation["calendar_reconciliation_error"],
        )

    # 45
    def test_45_validation_failure_rolls_back_everything(self):
        moved = self.add_task("OOP", [("10:00", "11:00")])
        candidate = self.add_task("Candidate", [("17:00", "18:00")], priority=2)
        before = self.snapshot()
        revision = self.revision()
        operations = int(self.db.fetch_one("SELECT COUNT(*) n FROM schedule_operations")["n"])
        original = self.db.execute

        def fail_operation(sql, parameters=()):
            if "INSERT INTO schedule_operations" in sql:
                raise RuntimeError("injected failure")
            return original(sql, parameters)

        self.db.execute = fail_operation
        try:
            with self.assertRaises(RuntimeError):
                self.move(moved, time(15))
        finally:
            self.db.execute = original
        self.assertEqual(before, self.snapshot())
        self.assertEqual(revision, self.revision())
        self.assertEqual(operations, int(self.db.fetch_one("SELECT COUNT(*) n FROM schedule_operations")["n"]))
        self.assertEqual([("17:00:00", "18:00:00")], self.windows(candidate))

    # 46
    def test_46_no_stale_segments_remain_after_split_move(self):
        moved = self.add_task("Split", [("09:00", "09:30"), ("11:00", "11:30")])
        self.move(moved, time(15))
        rows = self.db.get_task_segments(moved, "a")
        self.assertEqual(1, len(rows))
        self.assertEqual(0, int(rows[0]["segment_index"]))

    # 47
    def test_47_no_duplicate_segment_indices_after_repair(self):
        moved = self.add_task("OOP", [("15:00", "16:00")])
        self.add_task("Conflict", [("10:00", "11:00")])
        self.move(moved, time(10))
        duplicates = self.db.fetch_one(
            """SELECT COUNT(*) n FROM (
                   SELECT task_id,segment_index,COUNT(*) c FROM task_segments
                   GROUP BY task_id,segment_index HAVING c>1
               )"""
        )["n"]
        self.assertEqual(0, duplicates)

    # 48
    def test_48_compatibility_cache_matches_authoritative_segments(self):
        moved = self.add_task("OOP", [("10:00", "11:00")])
        self.add_task("Candidate", [("17:00", "18:00")], priority=2)
        self.move(moved, time(15))
        for row in self.db.get_tasks_with_segments(self.plan, "a"):
            raw = self.db.get_task(int(row["task_id"]), "a")
            segments = row["segments"]
            self.assertEqual(segments[0]["scheduled_start"] if segments else None, raw["scheduled_start"])
            self.assertEqual(segments[-1]["scheduled_end"] if segments else None, raw["scheduled_end"])

    # 49
    def test_49_hard_anchor_is_occupied_but_not_a_replanning_partition(self):
        moved = self.add_task("OOP", [("10:00", "11:00")])
        fixed = self.add_task("Boundary", [("12:00", "13:00")], fixed=True)
        later = self.add_task("Later", [("17:00", "18:00")], priority=1)
        self.move(moved, time(11))
        self.assertEqual([("12:00:00", "13:00:00")], self.windows(fixed))
        self.assertEqual([("10:00:00", "11:00:00")], self.windows(later))

    # 50
    def test_50_manual_move_is_current_day_only(self):
        moved = self.add_task("OOP", [("20:00", "21:00")])
        before_plans = int(self.db.fetch_one("SELECT COUNT(*) n FROM plans")["n"])
        with self.assertRaises(ValueError):
            self.move(moved, time(23, 30))
        after_plans = int(self.db.fetch_one("SELECT COUNT(*) n FROM plans")["n"])
        self.assertEqual(before_plans, after_plans)
        self.assertEqual(
            user_today(self.db, "a"),
            date.fromisoformat(self.db.get_plan_by_id(self.plan, "a")["plan_date"]),
        )

    # 51
    def test_51_break_is_occupied_but_does_not_partition_smart_replanning(self):
        moved = self.add_task("Move me", [("09:00", "09:30")])
        break_task = self.add_task(
            "Break", [("10:00", "10:15")], is_break=True,
        )
        candidate = self.add_task(
            "Flexible after break", [("12:00", "13:00")], priority=1,
        )
        before = self.snapshot()
        result = self.move(moved, time(11))
        self.assertEqual([("11:00:00", "11:30:00")], self.windows(moved))
        self.assertEqual([("09:00:00", "10:00:00")], self.windows(candidate))
        self.assertEqual([("10:00:00", "10:15:00")], self.windows(break_task))
        self.db.undo_schedule_operation(result.operation_id, "a")
        self.assertEqual(before, self.snapshot())

    # 52
    def test_52_task_too_large_for_pre_break_window_uses_later_whole_window(self):
        moved = self.add_task("Move me", [("09:00", "09:30")])
        break_task = self.add_task(
            "Break", [("10:00", "10:15")], is_break=True,
        )
        candidate = self.add_task(
            "Large flexible", [("12:00", "13:30")], priority=1,
        )
        self.move(moved, time(11))
        self.assertEqual([("11:30:00", "13:00:00")], self.windows(candidate))
        self.assertEqual([("10:00:00", "10:15:00")], self.windows(break_task))

    # 53
    def test_53_deadline_and_earliest_start_constrain_smart_replanning(self):
        plan_day = user_today(self.db, "a")
        moved = self.add_task("Move me", [("09:00", "09:30")])
        deadline_task = self.add_task(
            "Deadline", [("17:00", "18:00")], priority=4,
            deadline=datetime.combine(plan_day, time(11)),
        )
        later_critical = self.add_task(
            "Critical", [("18:00", "19:00")], priority=1,
            earliest=datetime.combine(plan_day, time(10)),
        )
        self.move(moved, time(15))
        self.assertEqual([("09:00:00", "10:00:00")], self.windows(deadline_task))
        self.assertEqual([("10:00:00", "11:00:00")], self.windows(later_critical))


if __name__ == "__main__":
    unittest.main()
