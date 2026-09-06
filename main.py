"""
CoachAI – FastAPI Backend Entry Point
========================================

Serves the Android app and owns the one process-wide background job
that Streamlit never could: closing out stale tasks at exactly
midnight, for every user, with no dependency on anyone opening a page.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

logger = logging.getLogger("coachai.scheduler")

# Change this if your users aren't all in the same timezone as your
# server — for a single-country launch this is fine; a truly
# per-user-timezone midnight is a bigger feature for later.
_APP_TIMEZONE = ZoneInfo("Africa/Cairo")

scheduler = BackgroundScheduler(timezone=_APP_TIMEZONE)


def run_daily_stale_task_cleanup() -> None:
    """
    Runs once a day at 00:00, for every user — this is the job that
    replaces Streamlit's page-load-triggered maybe_close_out_stale_tasks().

    Imported lazily (not at module load) and calls database.py directly
    rather than services.py's wrapper, since that wrapper also clears a
    Streamlit cache (load_analytics_profile.clear()) that doesn't exist
    in this process.
    """
    from database import Database

    db = Database()
    user_ids = db.get_all_user_ids()
    total_closed = 0
    for user_id in user_ids:
        try:
            total_closed += db.close_out_stale_tasks(user_id=user_id)
        except Exception:
            logger.exception("close_out_stale_tasks failed for user_id=%s", user_id)
    logger.info(
        "Daily stale-task cleanup done: %d task(s) closed across %d user(s).",
        total_closed, len(user_ids),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(
        run_daily_stale_task_cleanup,
        trigger=CronTrigger(hour=0, minute=0),
        id="daily_stale_task_cleanup",
        replace_existing=True,
        misfire_grace_time=3600,  # still run if the server was busy/down
                                   # for up to an hour past midnight
    )
    scheduler.start()
    logger.info("Scheduler started — daily cleanup set for 00:00 %s.", _APP_TIMEZONE)
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="CoachAI API", lifespan=lifespan)


# ... your existing/future routers (plans, tasks, analytics, auth, etc.)
# get included here via app.include_router(...)