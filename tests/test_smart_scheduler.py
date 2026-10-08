import unittest
from datetime import date, time

from scheduler import SchedulingPreferences
from smart_scheduler import SmartScheduler


def task(task_id, title, minutes, priority=3, **extra):
    return {
        "task_id": task_id,
        "title": title,
        "category": "Work",
        "estimated_minutes": minutes,
        "priority": priority,
        **extra,
    }


class SmartSchedulerTests(unittest.TestCase):
    day = date(2026, 9, 29)

    def allocate(self, tasks, blocked=None, start=time(9)):
        return SmartScheduler(SchedulingPreferences(start, blocked_slots=blocked or [])).allocate(tasks, self.day)

    def test_filler_first_then_later_whole_window(self):
        result = self.allocate(
            [task(1, "Primary", 90), task(2, "Filler", 30, priority=1)],
            [(time(10), time(11))],
        )
        by_id = {item.task_id: item for item in result.placed_tasks}
        self.assertEqual((time(9), time(9, 30)), (by_id[2].scheduled_start, by_id[2].scheduled_end))
        self.assertEqual((time(11), time(12, 30)), (by_id[1].scheduled_start, by_id[1].scheduled_end))
        self.assertEqual(1, len(by_id[1].segments))

    def test_splits_only_when_no_later_whole_window_fits(self):
        result = self.allocate(
            [task(1, "Primary", 150)],
            [(time(10), time(11)), (time(12), time(22, 30))],
        )
        placed = result.placed_tasks[0]
        self.assertEqual(3, len(placed.segments))
        self.assertEqual(150, sum(segment.duration_minutes for segment in placed.segments))
        self.assertEqual([], result.unplaced_tasks)

    def test_fragmentation_reason_and_no_partial_segments(self):
        result = self.allocate(
            [task(1, "Fragmented", 60)],
            [
                (time(9, 15), time(9, 30)),
                (time(9, 45), time(10)),
                (time(10, 15), time(10, 30)),
                (time(10, 45), time(23, 59, 59)),
            ],
        )
        self.assertEqual([], result.placed_tasks)
        self.assertEqual("fragmentation_segment_cap", result.unplaced_tasks[0].reason)

    def test_capacity_shortfall_reason(self):
        result = self.allocate(
            [task(1, "Too much", 120)],
            [(time(10), time(23, 59, 59))],
        )
        self.assertEqual("capacity_shortfall", result.unplaced_tasks[0].reason)

    def test_fixed_task_and_break_are_immutable_and_conflict_is_reported(self):
        result = self.allocate([
            task(1, "Meeting", 60, is_fixed_time=True, fixed_start="10:00", fixed_end="11:00"),
            task(2, "Break", 30, is_fixed_time=True, is_break=True, fixed_start="10:30", fixed_end="11:00"),
        ])
        by_id = {item.task_id: item for item in result.placed_tasks}
        self.assertEqual((time(10), time(11)), (by_id[1].scheduled_start, by_id[1].scheduled_end))
        self.assertEqual((time(10, 30), time(11)), (by_id[2].scheduled_start, by_id[2].scheduled_end))
        self.assertEqual(1, len(result.conflicts))

    def test_midnight_rollover_is_never_emitted(self):
        result = self.allocate([task(1, "Late", 60)], start=time(23, 30))
        self.assertEqual([], result.placed_tasks)
        self.assertEqual("capacity_shortfall", result.unplaced_tasks[0].reason)


if __name__ == "__main__":
    unittest.main()
