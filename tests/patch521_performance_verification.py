"""Opt-in controlled draft-preview measurements for Patch 5.2.1."""

from __future__ import annotations

import statistics
import sys
import tempfile
import time as clock
from datetime import date, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import Database  # noqa: E402
from future_planner import FuturePlanningService  # noqa: E402
from planning_request import (  # noqa: E402
    PlanningInputMethod,
    PlanningScope,
    PlanningTaskRequest,
    StructuredPlanningRequest,
)


def measure(weeks: int) -> dict:
    with tempfile.TemporaryDirectory() as temporary:
        db = Database(f"sqlite:///{(Path(temporary) / 'draft-performance.db').as_posix()}")
        try:
            user_id = "performance"
            db.execute(
                "INSERT INTO users (user_id,email,display_name,timezone) VALUES (?,?,?,?)",
                (user_id, "performance@test.local", "Performance", "UTC"),
            )
            db.connection.commit()
            start = date.today() + timedelta(days=1)
            end = start + timedelta(weeks=weeks)
            durations = (600, 480, 360, 240, 180, 120, 60, 30)
            request = StructuredPlanningRequest(
                scope=PlanningScope.DATE_RANGE,
                input_method=PlanningInputMethod.MANUAL,
                planning_start=start,
                planning_end=end,
                work_day_start=time(8),
                tasks=[
                    PlanningTaskRequest(
                        temporary_id=f"draft:performance-{index}",
                        title=f"Draft {index}",
                        remaining_minutes=minutes,
                        priority=(index % 5) + 1,
                    )
                    for index, minutes in enumerate(durations)
                ],
            )
            planner = FuturePlanningService(db)
            before = db.fetch_one("SELECT COUNT(*) AS n FROM tasks")["n"]
            samples = []
            signatures = []
            for _ in range(3):
                began = clock.perf_counter()
                preview = planner.preview_request(user_id, request)
                samples.append(clock.perf_counter() - began)
                signatures.append(preview.draft_segments)
            if signatures[0] != signatures[1] or signatures[1] != signatures[2]:
                raise AssertionError("Draft previews were not deterministic.")
            if db.fetch_one("SELECT COUNT(*) AS n FROM tasks")["n"] != before:
                raise AssertionError("Performance Preview persisted a draft task.")
            return {
                "weeks": weeks,
                "days": (end - start).days + 1,
                "tasks": len(durations),
                "placed": sum(1 for outcome in preview.outcomes if outcome.placed),
                "minimum": min(samples),
                "median": statistics.median(samples),
                "maximum": max(samples),
            }
        finally:
            db.close()


if __name__ == "__main__":
    for weeks in (1, 4, 8, 12):
        result = measure(weeks)
        print(
            "DRAFT_PERFORMANCE "
            f"weeks={result['weeks']} inclusive_days={result['days']} "
            f"tasks={result['tasks']} placed={result['placed']} "
            f"min_seconds={result['minimum']:.6f} "
            f"median_seconds={result['median']:.6f} "
            f"max_seconds={result['maximum']:.6f}"
        )
