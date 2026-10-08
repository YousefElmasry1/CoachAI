"""Opt-in controlled performance measurements for Patch 5 horizons."""

from __future__ import annotations

import statistics
import sys
import tempfile
import time as clock
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import Database  # noqa: E402
from future_planner import FuturePlanningService  # noqa: E402
from scheduler import SchedulingPreferences  # noqa: E402


def measure(weeks: int) -> dict:
    with tempfile.TemporaryDirectory() as temporary:
        db = Database(f"sqlite:///{(Path(temporary) / 'performance.db').as_posix()}")
        try:
            user_id = "performance"
            db.execute(
                "INSERT INTO users (user_id,email,display_name,timezone) VALUES (?,?,?,?)",
                (user_id, "performance@test.local", "Performance", "UTC"),
            )
            db.connection.commit()
            start = date.today() + timedelta(days=1)
            end = start + timedelta(weeks=weeks)
            plans = {}
            cursor = start
            while cursor <= end:
                plans[cursor] = db.create_plan(user_id, cursor, "controlled performance")
                cursor += timedelta(days=1)

            durations = (600, 480, 360, 240, 180, 120, 60, 30)
            for index, minutes in enumerate(durations):
                db.add_task(
                    plans[start], user_id, f"Flexible {index}", estimated_minutes=minutes,
                    priority=(index % 5) + 1,
                    earliest_start=datetime.combine(start, time(8)),
                    deadline=datetime.combine(end, time(21, 30)),
                )
            for index, day in enumerate(sorted(plans)):
                if index % 7 == 0:
                    db.add_task(
                        plans[day], user_id, f"Weekly fixed anchor {index}",
                        estimated_minutes=60, scheduled_start="13:00", scheduled_end="14:00",
                        is_fixed_time=True,
                    )
                if index % 14 == 3:
                    db.upsert_google_calendar_event(
                        user_id, f"external-{index}", "External occupancy",
                        "16:00", "16:30", day.isoformat(), "performance-calendar",
                    )

            planner = FuturePlanningService(db)
            preferences = SchedulingPreferences(work_day_start=time(8))
            samples = []
            signatures = []
            for _ in range(3):
                began = clock.perf_counter()
                preview = planner.preview(user_id, start, end, preferences)
                samples.append(clock.perf_counter() - began)
                signatures.append(preview.segments_by_task)
            if signatures[0] != signatures[1] or signatures[1] != signatures[2]:
                raise AssertionError("Performance previews were not deterministic.")
            placed = sum(1 for outcome in preview.outcomes if outcome.placed)
            return {
                "weeks": weeks,
                "inclusive_days": (end - start).days + 1,
                "tasks": len(durations),
                "placed": placed,
                "minimum": min(samples),
                "median": statistics.median(samples),
                "maximum": max(samples),
            }
        finally:
            db.close()


def main() -> None:
    for weeks in (1, 4, 8, 12):
        result = measure(weeks)
        print(
            "PERFORMANCE "
            f"weeks={result['weeks']} inclusive_days={result['inclusive_days']} "
            f"tasks={result['tasks']} placed={result['placed']} "
            f"min_seconds={result['minimum']:.6f} "
            f"median_seconds={result['median']:.6f} "
            f"max_seconds={result['maximum']:.6f}"
        )


if __name__ == "__main__":
    main()
