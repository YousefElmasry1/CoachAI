"""Compatibility entry point for the standalone maintenance process.

CoachAI deliberately exposes no FastAPI application yet. Background jobs are
separate supervised processes, so importing this module can never start a
scheduler in a web worker.
"""

from maintenance_worker import main, run_daily_stale_task_cleanup


if __name__ == "__main__":
    raise SystemExit(main())
