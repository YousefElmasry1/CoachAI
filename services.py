"""
CoachAI – Backend Service Wrappers
====================================

Thin caching layer over the existing backend services.
Never duplicates backend logic — only wraps and caches.

Threading note
--------------
sqlite3 connections may only be used on the thread that created them.
Streamlit can execute a rerun on a different worker thread than the one
that built a cached resource, so every factory below that transitively
holds a Database connection is cached per-thread (via threading.local())
instead of with @st.cache_resource, which caches once for the whole
process. This keeps the same "build once, reuse" performance benefit
without ever handing a connection to the wrong thread.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional

import streamlit as st

from config import DEFAULT_ANALYTICS_WINDOW, DEFAULT_USER_ID
from text_matching import detect_language

_local = threading.local()


# ─────────────────────────────────────────────────────────────
# Identity
#
# Lightweight local identity helpers for the Streamlit frontend.
# The Android app uses Firebase Auth; the Streamlit demo uses these
# simple wrappers so the UI works without a Firebase project.
# ─────────────────────────────────────────────────────────────


def get_current_user_id():
    """Return the active user_id from Streamlit session state,
    falling back to DEFAULT_USER_ID for single-user / demo mode."""
    return st.session_state.get("user_id") or DEFAULT_USER_ID


def create_guest_user(display_name: str) -> Optional[str]:
    """Create an isolated guest account with a random UUID-based id.

    Returns the new user_id (str) or None on failure.
    """
    try:
        db = get_database()
        guest_uid = f"guest_{uuid.uuid4().hex[:12]}"
        user_id = db.create_user(
            user_id=guest_uid,
            email=f"{guest_uid}@guest.local",
            display_name=display_name,
        )
        return user_id
    except Exception:
        return None


_PBKDF2_ITERATIONS: int = 260_000


def _hash_password(password: str) -> str:
    """Hash a plaintext password into a 'salt$hash' string for storage."""
    import hashlib

    salt = uuid.uuid4().hex
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERATIONS
    ).hex()
    return f"{salt}${digest}"


def _verify_password(password: str, stored_hash: str) -> bool:
    """Check a plaintext password against a 'salt$hash' string from the DB."""
    import hashlib
    import hmac

    try:
        salt, digest = stored_hash.split("$", 1)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERATIONS
    ).hex()
    return hmac.compare_digest(candidate, digest)


def sign_up(email: str, password: str, display_name: str) -> tuple:
    """Register a new account with email + password.

    Returns (user_id, None) on success, or (None, error_message) on failure.
    """
    email = (email or "").strip().lower()
    display_name = (display_name or "").strip()
    if not email or "@" not in email:
        return None, "Please enter a valid email."
    if len(password or "") < 6:
        return None, "Password must be at least 6 characters."
    if not display_name:
        return None, "Please enter your name."

    try:
        db = get_database()
        existing = db.get_user_by_email(email)
        if existing is not None:
            return None, "An account with this email already exists."

        uid = f"local_{uuid.uuid4().hex[:12]}"
        user_id = db.create_user(
            user_id=uid,
            email=email,
            display_name=display_name,
        )
        # Store the password hash in the user row
        pw_hash = _hash_password(password)
        db.execute(
            "UPDATE users SET password_hash = ? WHERE user_id = ?",
            (pw_hash, user_id),
        )
        db.connection.commit()
        return user_id, None
    except Exception as e:
        return None, f"Sign-up failed: {e}"


def log_in(email: str, password: str) -> tuple:
    """Verify email + password against an existing account.

    Returns (user_id, display_name) on success, or (None, error_message)
    on failure.
    """
    cleaned_email = (email or "").strip().lower()
    if not cleaned_email or not password:
        return None, "Please enter your email and password."

    db = get_database()
    row = db.get_user_by_email(cleaned_email)
    if row is None or not row["password_hash"] or not _verify_password(password, row["password_hash"]):
        return None, "Incorrect email or password."
    return row["user_id"], row["display_name"]



# ─────────────────────────────────────────────────────────────
# Singleton Service Factories (cached per-thread, not per-process)
# ─────────────────────────────────────────────────────────────

def get_database():
    """Return a Database connection scoped to the current thread."""
    if getattr(_local, "database", None) is None:
        from database import Database
        _local.database = Database()
    return _local.database


def get_analytics_engine():
    """Return an AnalyticsEngine scoped to the current thread."""
    if getattr(_local, "analytics_engine", None) is None:
        from analytics import AnalyticsEngine
        _local.analytics_engine = AnalyticsEngine(get_database())
    return _local.analytics_engine


def get_analytics_formatter():
    """Return an AnalyticsFormatter scoped to the current thread."""
    if getattr(_local, "analytics_formatter", None) is None:
        from analytics import AnalyticsFormatter
        _local.analytics_formatter = AnalyticsFormatter()
    return _local.analytics_formatter


def get_recommendation_service():
    """Return a RecommendationService scoped to the current thread."""
    if getattr(_local, "recommendation_service", None) is None:
        from recommendation_service import RecommendationService
        _local.recommendation_service = RecommendationService(get_database())
    return _local.recommendation_service


def get_scheduler_service():
    """Return a SchedulerService scoped to the current thread."""
    if getattr(_local, "scheduler_service", None) is None:
        from scheduler_service import SchedulerService
        _local.scheduler_service = SchedulerService(get_database())
    return _local.scheduler_service


# Holds the error message from the most recent failed scheduler run (see
# run_scheduler_for_plan / get_last_scheduler_error below). A plain
# module-level dict rather than a bare variable so it's mutable from
# inside run_scheduler_for_plan without a `global` statement.
_LAST_SCHEDULER_ERROR: dict = {"message": None}


def get_planner_service():
    """Return a PlannerService scoped to the current thread."""
    if getattr(_local, "planner_service", None) is None:
        from planner_service import PlannerService
        _local.planner_service = PlannerService(get_database())
    return _local.planner_service


# ─────────────────────────────────────────────────────────────
# Voice Input (Speech-to-Text via Gemini)
# ─────────────────────────────────────────────────────────────

def transcribe_voice_note(audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
    """
    Transcribe a recorded voice note into plain text via Gemini.

    Thin wrapper only — all transcription logic lives in
    voice_service.py. The returned text is meant to be passed straight
    into generate_today_plan() exactly like typed free-form input.

    Args:
        audio_bytes: Raw audio bytes from st.audio_input().
        mime_type: MIME type of the audio (defaults to "audio/wav").

    Returns:
        The transcribed text.

    Raises:
        ValueError: If audio_bytes is empty.
        voice_service.VoiceTranscriptionError: If Gemini fails to
            transcribe the recording.
    """
    from voice_service import transcribe_audio
    return transcribe_audio(audio_bytes, mime_type=mime_type)



# ─────────────────────────────────────────────────────────────
# Cached Data Loaders
# ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def load_analytics_profile(
    user_id: str = DEFAULT_USER_ID,
    window_days: int = DEFAULT_ANALYTICS_WINDOW,
    language: str = "en",
) -> Any:
    """
    Build and cache the full AnalyticsProfile.

    TTL = 5 minutes so the profile refreshes periodically
    without hammering the database.
    """
    engine = get_analytics_engine()
    return engine.build_profile(user_id=user_id, window_days=window_days, language=language)


def load_analytics_dashboard(
    user_id: str = DEFAULT_USER_ID,
    window_days: int = DEFAULT_ANALYTICS_WINDOW,
) -> dict[str, Any]:
    """Load analytics formatted for dashboard consumption."""
    profile = load_analytics_profile(user_id, window_days)
    formatter = get_analytics_formatter()
    return formatter.to_dashboard(profile)


def load_analytics_summary(
    user_id: str = DEFAULT_USER_ID,
    window_days: int = DEFAULT_ANALYTICS_WINDOW,
) -> str:
    """Load a short text summary of analytics."""
    profile = load_analytics_profile(user_id, window_days)
    formatter = get_analytics_formatter()
    return formatter.to_summary(profile)


@st.cache_data(ttl=300, show_spinner=False)
def load_pause_matrix(
    user_id: str = DEFAULT_USER_ID,
    window_days: int = DEFAULT_ANALYTICS_WINDOW,
) -> list[dict]:
    """
    Cached Category x Time-of-day focus matrix (see
    Database.get_pause_matrix for the full shape/meaning of each row).

    This is deliberately a standalone, cacheable data source — not
    folded into AnalyticsProfile itself — specifically so any other
    part of the app (a dedicated analytics view, the AI Coach's
    recommendation prompt, a future dashboard widget) can pull the same
    numbers without needing to touch analytics.py. TTL matches
    load_analytics_profile (5 min) for consistency; cleared automatically
    whenever a task is paused (see pause_task_timer), so it never lags
    more than one page load behind reality.

    Returns:
        List of {category, time_bucket, task_count, paused_task_count,
        total_pauses, avg_pauses_per_task} dicts, sorted by total_pauses
        descending (heaviest distraction pattern first). Empty list if
        the user has no started tasks in the window.
    """
    db = get_database()
    since = date.today() - timedelta(days=window_days)
    return db.get_pause_matrix(user_id=user_id, since_date=since.isoformat())


# ─────────────────────────────────────────────────────────────
# User Data
# ─────────────────────────────────────────────────────────────

def load_user(user_id: str = DEFAULT_USER_ID) -> dict:
    """Load user information as a dict. Returns empty dict if not found."""
    db = get_database()
    row = db.get_user(user_id)
    if row is None:
        return {}
    return dict(row)


def set_user_timezone(user_id: str, tz_name: str) -> bool:
    """Update the user's stored IANA timezone (users.timezone column).

    Used by the Settings page's manual timezone picker — a temporary
    stand-in until the mobile app can set this automatically. Affects
    the greeting's time-of-day and analytics like the Focus Pattern
    Matrix, both of which read timezone off load_user().

    Returns True on success, False on error.
    """
    try:
        db = get_database()
        db.execute(
            "UPDATE users SET timezone = ? WHERE user_id = ?",
            (tz_name, user_id),
        )
        db.connection.commit()
        return True
    except Exception:
        return False


def load_user_profile(user_id: str = DEFAULT_USER_ID) -> dict:
    """Load the user's cached profile from user_profiles table."""
    db = get_database()
    row = db.get_profile(user_id)
    if row is None:
        return {}
    return dict(row)


def load_user_badges(user_id: str = DEFAULT_USER_ID) -> list[dict]:
    """Load badges earned by the user."""
    db = get_database()
    rows = db.get_user_badges(user_id)
    return [dict(r) for r in rows]


def load_all_badges() -> list[dict]:
    """Load all badge definitions."""
    db = get_database()
    rows = db.get_all_badges()
    return [dict(r) for r in rows]


def load_categories(user_id: str = DEFAULT_USER_ID) -> list[dict]:
    """Load user's categories."""
    db = get_database()
    rows = db.get_categories(user_id)
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────
# Plan & Task Data
# ─────────────────────────────────────────────────────────────

def load_today_plan(user_id: str = DEFAULT_USER_ID) -> Optional[dict]:
    """Load today's plan as a dict, or None if no plan exists."""
    db = get_database()
    row = db.get_today_plan(user_id)
    if row is None:
        return None
    return dict(row)


def load_plan_tasks(plan_id: int) -> list[dict]:
    """Load all tasks for a plan as a list of dicts."""
    db = get_database()
    rows = db.get_tasks_by_plan(plan_id)
    return [dict(r) for r in rows]


def load_today_tasks(user_id: str = DEFAULT_USER_ID) -> list[dict]:
    """Load today's tasks. Returns empty list if no plan."""
    plan = load_today_plan(user_id)
    if plan is None:
        return []
    return load_plan_tasks(plan["plan_id"])


def load_recent_plans(
    user_id: str = DEFAULT_USER_ID,
    days: int = 30,
) -> list[dict]:
    """Load recent plans with their tasks."""
    db = get_database()
    since = date.today() - timedelta(days=days)
    rows = db.get_recent_plans(user_id=user_id, since_date=since)
    plans = []
    for row in rows:
        plan = dict(row)
        plan["tasks"] = load_plan_tasks(plan["plan_id"])
        plans.append(plan)
    return plans


# ─────────────────────────────────────────────────────────────
# Task Actions
# ─────────────────────────────────────────────────────────────

def update_task_status(
    task_id: int,
    status: str,
    failure_reason: Optional[str] = None,
    actual_minutes: Optional[int] = None,
) -> bool:
    """
    Update a task's status and clear relevant caches.

    Returns True on success, False on error.
    """
    try:
        db = get_database()
        db.update_task_status(
            task_id=task_id,
            status=status,
            failure_reason=failure_reason,
            actual_minutes=actual_minutes,
        )
        # Clear cached analytics so they rebuild next load
        load_analytics_profile.clear()
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────
# Task Timer (pause / resume without losing accuracy)
#
# A task's `status` never gains a "paused" value — it stays
# 'in_progress' the whole time. Whether a timer is actively running or
# paused is tracked purely by timer_segment_started_at: non-None means
# a segment is currently running, None means paused. Every time a
# segment ends (pause, complete, or fail), its duration gets folded
# into timer_accumulated_seconds, so paused time is never counted.
# ─────────────────────────────────────────────────────────────

def _utc_now_str() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def get_task_elapsed_seconds(task: dict) -> int:
    """
    Total active (non-paused) seconds elapsed on a task's timer right
    now: everything already banked, plus the still-running segment (if
    any). Safe to call regardless of status — returns the banked total
    even for a task that's paused, completed, or never started.
    """
    accumulated = int(task.get("timer_accumulated_seconds") or 0)
    segment_started_raw = task.get("timer_segment_started_at")
    if not segment_started_raw:
        return accumulated
    try:
        segment_started = datetime.fromisoformat(str(segment_started_raw))
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        return accumulated + max(0, int((now_utc - segment_started).total_seconds()))
    except (ValueError, TypeError):
        return accumulated


def get_task_paused_seconds(task: dict) -> int:
    """
    Total time this task has spent paused, ever: every finished pause's
    duration, plus however long the CURRENT pause has run so far (if
    it's paused right now). Complements get_task_elapsed_seconds — the
    two together fully account for the time since a task was first
    started (elapsed + paused == wall-clock time since started_at, for
    a task that hasn't finished yet).
    """
    total = int(task.get("timer_total_paused_seconds") or 0)
    paused_at_raw = task.get("paused_at")
    if not paused_at_raw:
        return total
    try:
        paused_at = datetime.fromisoformat(str(paused_at_raw))
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        return total + max(0, int((now_utc - paused_at).total_seconds()))
    except (ValueError, TypeError):
        return total


def is_task_timer_paused(task: dict) -> bool:
    """True if the task is in_progress but its timer is currently paused."""
    return task.get("status") == "in_progress" and not task.get("timer_segment_started_at")


def start_task_timer(task_id: int) -> bool:
    """Start a task's timer for the first time (pending -> in_progress)."""
    try:
        db = get_database()
        db.update_task_status(task_id, status="in_progress")  # stamps started_at once
        db.set_task_timer_state(
            task_id, accumulated_seconds=0, segment_started_at=_utc_now_str(),
            paused_at=None, total_paused_seconds=0,
        )
        return True
    except Exception:
        return False


def pause_task_timer(task_id: int) -> bool:
    """Pause a running task's timer: banks the elapsed active segment,
    starts the pause clock (paused_at), and bumps pause_count (feeds
    get_pause_matrix / load_pause_matrix)."""
    try:
        db = get_database()
        task = db.get_task(task_id)
        if task is None:
            return False
        task_dict = dict(task)
        elapsed = get_task_elapsed_seconds(task_dict)
        db.set_task_timer_state(
            task_id, accumulated_seconds=elapsed, segment_started_at=None,
            paused_at=_utc_now_str(),
            total_paused_seconds=int(task_dict.get("timer_total_paused_seconds") or 0),
        )
        db.increment_task_pause_count(task_id)
        load_pause_matrix.clear()
        return True
    except Exception:
        return False


def resume_task_timer(task_id: int) -> bool:
    """
    Resume a paused task's timer: folds however long that pause just
    lasted into timer_total_paused_seconds (so it's never lost), then
    starts a new active segment.
    """
    try:
        db = get_database()
        task = db.get_task(task_id)
        if task is None:
            return False
        task_dict = dict(task)
        # Finalise the pause that's ending right now.
        total_paused = get_task_paused_seconds(task_dict)
        accumulated = int(task_dict.get("timer_accumulated_seconds") or 0)
        db.set_task_timer_state(
            task_id, accumulated_seconds=accumulated, segment_started_at=_utc_now_str(),
            paused_at=None, total_paused_seconds=total_paused,
        )
        return True
    except Exception:
        return False


def finish_task_with_timer(
    task_id: int,
    status: str,
    failure_reason: Optional[str] = None,
) -> bool:
    """
    Mark a task completed/failed, using its real accumulated timer
    time (banked segments + whatever's still running) as actual_minutes
    instead of the naive (now - started_at) fallback — which would
    incorrectly include any paused stretches. Also finalises an
    in-progress pause (if the task happened to be paused, not running,
    at the moment it's marked done), so timer_total_paused_seconds
    always reflects the complete picture once a task is finished.

    Falls back to letting update_task_status compute actual_minutes
    the old way if the task was never started (no timer data at all).
    """
    try:
        db = get_database()
        task = db.get_task(task_id)
        actual_minutes = None
        if task is not None:
            task_dict = dict(task)
            elapsed_seconds = get_task_elapsed_seconds(task_dict)
            total_paused = get_task_paused_seconds(task_dict)
            if elapsed_seconds > 0:
                actual_minutes = max(1, round(elapsed_seconds / 60))
            # Freeze both the active segment and any open pause, so a
            # completed/failed task never shows a "still running" or
            # "still paused" timer if re-read later.
            db.set_task_timer_state(
                task_id, accumulated_seconds=elapsed_seconds, segment_started_at=None,
                paused_at=None, total_paused_seconds=total_paused,
            )
        return update_task_status(
            task_id, status=status, failure_reason=failure_reason,
            actual_minutes=actual_minutes,
        )
    except Exception:
        return False


def close_out_stale_tasks(user_id: str = DEFAULT_USER_ID) -> int:
    """
    Auto-fail any pending/in_progress task left over from a past day,
    so tasks never sit unresolved forever and analytics always sees a
    final status. Safe to call on every page load — it's a no-op once
    there's nothing stale left.

    Returns the number of tasks that were closed out (0 most of the time).
    """
    try:
        db = get_database()
        closed = db.close_out_stale_tasks(user_id=user_id)
        if closed:
            load_analytics_profile.clear()
        return closed
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────
# AI Coach (Recommendations)
# ─────────────────────────────────────────────────────────────

def load_recommendations_today(user_id: str = DEFAULT_USER_ID) -> Any:
    """
    Get AI coaching recommendations for today.

    Returns RecommendationOutput or raises on error.
    """
    service = get_recommendation_service()
    return service.get_recommendations_for_today(user_id=user_id)


def load_recommendations_for_plan(plan_id: int) -> Any:
    """Get AI coaching recommendations for a specific plan."""
    service = get_recommendation_service()
    return service.get_recommendations_for_plan(plan_id=plan_id)


# ─────────────────────────────────────────────────────────────
# System Status
# ─────────────────────────────────────────────────────────────

def get_database_status() -> dict[str, Any]:
    """Check database connectivity and return status info."""
    try:
        db = get_database()
        # Quick connectivity test
        db.fetch_one("SELECT 1")

        db_size = 0
        db_path = db.db_url
        if db.is_sqlite:
            # sqlite:///absolute/path -> absolute/path
            sqlite_path = db.db_url.split("sqlite:///", 1)[-1]
            db_path = sqlite_path
            if os.path.exists(sqlite_path):
                db_size = os.path.getsize(sqlite_path)

        return {
            "connected": True,
            "path": db_path,
            "size_bytes": db_size,
            "size_display": _format_file_size(db_size) if db.is_sqlite else "—",
        }
    except Exception as e:
        return {
            "connected": False,
            "error": str(e),
            "path": "",
            "size_bytes": 0,
            "size_display": "—",
        }


def get_system_info() -> dict[str, str]:
    """Gather system/version information."""
    import sys
    import sqlite3
    try:
        from analytics import ANALYTICS_VERSION, PROFILE_VERSION, STATISTICS_VERSION
    except ImportError:
        ANALYTICS_VERSION = PROFILE_VERSION = STATISTICS_VERSION = "?"

    try:
        from recommendation import RecommendationEngine
        model_name = RecommendationEngine._MODEL_NAME
    except Exception:
        model_name = "Unknown"

    return {
        "app_version": "1.0.0",
        "python_version": sys.version.split()[0],
        "streamlit_version": st.__version__,
        "sqlite_version": sqlite3.sqlite_version,
        "analytics_version": ANALYTICS_VERSION,
        "profile_version": PROFILE_VERSION,
        "statistics_version": STATISTICS_VERSION,
        "ai_model": model_name,
    }


def _format_file_size(size_bytes: int) -> str:
    """Format bytes to human readable size."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


# ─────────────────────────────────────────────────────────────
# Plan Lookup
# ─────────────────────────────────────────────────────────────

def load_plan(plan_id: int) -> Optional[dict]:
    """Load a single plan by id as a dict, or None if not found."""
    db = get_database()
    row = db.get_plan_by_id(plan_id)
    if row is None:
        return None
    return dict(row)


# ─────────────────────────────────────────────────────────────
# Plan & Task Creation / Mutation
# (thin wrappers around Database — no new business logic)
# ─────────────────────────────────────────────────────────────

def create_today_plan(
    raw_input: str,
    user_id: str = DEFAULT_USER_ID,
) -> Optional[int]:
    """
    Create today's plan container for a user.

    Returns the new plan_id, or None if a plan for today already exists
    or creation fails.
    """
    db = get_database()
    try:
        plan_id = db.create_plan(
            user_id=user_id,
            plan_date=date.today(),
            raw_input=raw_input or "Created from the CoachAI dashboard.",
        )
        return plan_id
    except Exception:
        return None


def generate_today_plan(
    raw_input: str,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    """
    Turn a free-form description of the day into structured, categorized
    tasks via the AI Planner, and save them to today's plan.

    Returns a dict with plan_id, planning_notes, and the list of saved
    tasks (each flagged with needs_review / review_reason where relevant).

    Raises:
        ValueError: If raw_input is empty.
        RuntimeError: If the Planner engine fails.
    """
    service = get_planner_service()
    return service.generate_and_save_plan(raw_input=raw_input, user_id=user_id)


def draft_today_plan(raw_input: str, user_id: str = DEFAULT_USER_ID) -> Any:
    """
    Ask the AI Planner to draft structured tasks WITHOUT saving them.

    Use this (instead of generate_today_plan) whenever the caller needs
    to inspect the draft first — e.g. to run a realistic-capacity check
    via check_capacity_for_today() — before deciding whether to save it.

    Returns the raw DayPlanOutput (see planner.py).

    Raises:
        ValueError: If raw_input is empty.
        RuntimeError: If the Planner engine fails.
    """
    service = get_planner_service()
    cal_events = get_google_calendar_events_today(user_id)
    return service.draft_plan(
        raw_input=raw_input, user_id=user_id, calendar_events=cal_events,
    )


def save_planner_draft(
    plan_output: Any,
    raw_input: str,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    """
    Persist a DayPlanOutput previously returned by draft_today_plan().

    Returns the same shape as generate_today_plan().
    """
    service = get_planner_service()
    result = service.save_plan(
        plan_output=plan_output, raw_input=raw_input, user_id=user_id,
    )
    load_analytics_profile.clear()
    return result


# ─────────────────────────────────────────────────────────────
# Realistic Capacity — Pre-Plan Warning
# ─────────────────────────────────────────────────────────────

def draft_plan_total_minutes(plan_output: Any) -> int:
    """Sum estimated_minutes across every task in a (not-yet-saved) draft."""
    return sum(int(t.estimated_minutes) for t in (plan_output.tasks or []))


def check_capacity_for_today(
    additional_minutes: int,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    """
    Check whether adding `additional_minutes` of newly planned/drafted
    work to today would push the day's total planned load meaningfully
    past this user's historical Realistic Capacity.

    This looks at the FULL day (today's existing tasks, if any, plus the
    new load being considered) — not just the new tasks in isolation —
    since what matters is whether the day as a whole is overloaded.

    Args:
        additional_minutes: Total estimated minutes of the new tasks
            being considered (from a draft plan, or a single manually
            added task).
        user_id: Whose capacity/today to check.

    Returns:
        A dict:
            triggered: bool — True if a warning should be shown.
            planned_minutes: int — today's existing + additional minutes.
            existing_minutes: int — today's already-saved planned minutes.
            recommended_minutes: float — the user's Realistic Capacity.
            overload_fraction: float — how far over capacity (0.20 = 20% over).
            basis: str — how recommended_minutes was derived.
            confidence: str — confidence level behind the recommendation.
            light_day_completion_rate: float
            heavy_day_completion_rate: float
    """
    from config import CAPACITY_OVERLOAD_MARGIN, CAPACITY_MIN_CONFIDENCE_TO_WARN

    profile = load_analytics_profile(user_id, DEFAULT_ANALYTICS_WINDOW)
    capacity = profile.capacity

    existing_minutes = sum(
        int(t.get("estimated_minutes") or 0) for t in load_today_tasks(user_id)
    )
    planned_minutes = existing_minutes + max(0, int(additional_minutes))
    recommended = capacity.recommended_daily_minutes

    overload_fraction = (
        (planned_minutes - recommended) / recommended if recommended > 0 else 0.0
    )

    triggered = (
        capacity.confidence.level in CAPACITY_MIN_CONFIDENCE_TO_WARN
        and overload_fraction >= CAPACITY_OVERLOAD_MARGIN
    )

    return {
        "triggered": triggered,
        "planned_minutes": planned_minutes,
        "existing_minutes": existing_minutes,
        "recommended_minutes": recommended,
        "overload_fraction": overload_fraction,
        "basis": capacity.basis,
        "confidence": capacity.confidence.level,
        "light_day_completion_rate": capacity.light_day_completion_rate,
        "heavy_day_completion_rate": capacity.heavy_day_completion_rate,
    }


def add_task_to_plan(
    plan_id: int,
    title: str,
    category_id: Optional[int] = None,
    description: str = "",
    priority: int = 3,
    estimated_minutes: int = 30,
    order_index: int = 0,
) -> Optional[int]:
    """Add a task to a plan. Returns the new task_id, or None on failure."""
    db = get_database()
    try:
        return db.add_task(
            plan_id=plan_id,
            title=title,
            category_id=category_id,
            description=description,
            priority=priority,
            estimated_minutes=estimated_minutes,
            order_index=order_index,
        )
    except Exception:
        return None


def delete_task(task_id: int) -> bool:
    """Delete a task. Returns True on success, False on error."""
    db = get_database()
    try:
        db.delete_task(task_id)
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────
# Breaks (persisted as real tasks — see is_break on the tasks table)
#
# A break is stored as an ordinary task row with is_break=1 and
# is_fixed_time=1. That's deliberate: it means a break automatically
# gets everything a real task already has for free — it survives page
# reloads, the Scheduler already treats any is_fixed_time task as an
# immovable blocked slot (so other tasks route around it with no
# scheduler changes needed), and it can reuse update_task_status for
# its own start/complete timer instead of a parallel mechanism.
# ─────────────────────────────────────────────────────────────

def add_break_to_plan(
    plan_id: int,
    start_time: time,
    duration_minutes: int,
    title: Optional[str] = None,
) -> Optional[int]:
    """
    Add a break to a plan as a fixed-time task (is_break=1).

    Args:
        plan_id: The plan to add the break to.
        start_time: When the break starts.
        duration_minutes: How long the break lasts.
        title: Display title. If not given, defaults to "Break" or
            "استراحة" depending on the language of this plan's
            raw_input, so a break added without an explicit title
            still matches the language the user is writing in rather
            than always defaulting to English.

    Returns:
        The new task_id, or None on error.
    """
    db = get_database()
    if title is None:
        plan = db.get_plan_by_id(plan_id)
        plan_text = plan["raw_input"] if plan is not None else ""
        title = "استراحة" if detect_language(plan_text) == "ar" else "Break"
    try:
        existing = db.get_tasks_by_plan(plan_id)
        order_index = len(existing)
        end_time = (
            datetime.combine(date.today(), start_time) + timedelta(minutes=duration_minutes)
        ).time()
        return db.add_task(
            plan_id=plan_id,
            title=title,
            priority=1,
            estimated_minutes=duration_minutes,
            scheduled_start=start_time.strftime("%H:%M"),
            scheduled_end=end_time.strftime("%H:%M"),
            order_index=order_index,
            is_fixed_time=True,
            is_break=True,
        )
    except Exception:
        return None


def reschedule_break(task_id: int, new_start_time: time) -> bool:
    """
    Shift a break to a new start time, keeping its original duration.
    Used by the conflict-resolution actions ("move before/after task").

    Returns True on success, False on error.
    """
    db = get_database()
    try:
        task = db.get_task(task_id)
        if task is None:
            return False
        duration = int(task["estimated_minutes"])
        new_end_time = (
            datetime.combine(date.today(), new_start_time) + timedelta(minutes=duration)
        ).time()
        db.update_task(
            task_id,
            scheduled_start=new_start_time.strftime("%H:%M"),
            scheduled_end=new_end_time.strftime("%H:%M"),
        )
        return True
    except Exception:
        return False


def start_break(task_id: int) -> bool:
    """Mark a break as started (stamps started_at, like any task timer)."""
    return update_task_status(task_id, status="in_progress")


def complete_break(task_id: int) -> bool:
    """Mark a break as finished."""
    return update_task_status(task_id, status="completed")


def defer_task_to_tomorrow(task_id: int, user_id: str = DEFAULT_USER_ID) -> bool:
    """
    Move a task from its current plan to the user's plan for tomorrow,
    creating tomorrow's plan container if it doesn't exist yet.

    Used by the Realistic Capacity warning on the Manual tab, to let a
    user free up room in today's plan without leaving the warning.
    Clears any scheduled_start/scheduled_end, since a time slot computed
    for today's schedule doesn't carry over to tomorrow's.

    Returns True on success, False on error.
    """
    db = get_database()
    try:
        tomorrow = date.today() + timedelta(days=1)
        target_plan = db.get_plan_by_date(user_id=user_id, plan_date=tomorrow)
        if target_plan is None:
            plan_id = db.create_plan(
                user_id=user_id,
                plan_date=tomorrow,
                raw_input="Tasks deferred from a previous day.",
            )
        else:
            plan_id = int(target_plan["plan_id"])

        next_order_index = len(db.get_tasks_by_plan(plan_id))
        db.update_task(
            task_id,
            plan_id=plan_id,
            order_index=next_order_index,
            scheduled_start=None,
            scheduled_end=None,
        )
        load_analytics_profile.clear()
        return True
    except Exception:
        return False


def save_draft_tasks_to_plan(tasks: list, plan_id: int) -> int:
    """
    Persist a list of not-yet-saved drafted task objects (from a
    planner DayPlanOutput, e.g. `plan_output.tasks`) as real rows on an
    existing plan.

    Args:
        tasks: Task objects/dicts with at least a title and
            estimated_minutes (title/priority/description/is_fixed_time/
            fixed_start are read via getattr with sane fallbacks, so this
            works whether `tasks` holds Pydantic objects or plain dicts).
        plan_id: The plan to attach them to.

    Returns:
        How many tasks were successfully saved.
    """
    def _get(t, name, default=None):
        if hasattr(t, name):
            return getattr(t, name)
        if isinstance(t, dict):
            return t.get(name, default)
        return default

    db = get_database()
    existing_count = len(db.get_tasks_by_plan(plan_id))
    saved = 0
    for i, t in enumerate(tasks):
        try:
            is_fixed = bool(_get(t, "is_fixed_time", False))
            fixed_start = _get(t, "fixed_start", None)
            db.add_task(
                plan_id=plan_id,
                title=str(_get(t, "title", "Untitled")),
                description=str(_get(t, "description", "") or ""),
                priority=int(_get(t, "priority", 3)),
                estimated_minutes=int(_get(t, "estimated_minutes", 30)),
                scheduled_start=str(fixed_start) if (is_fixed and fixed_start) else None,
                order_index=existing_count + i,
                is_fixed_time=is_fixed,
            )
            saved += 1
        except Exception:
            continue

    if saved:
        load_analytics_profile.clear()
    return saved


def defer_draft_tasks_to_tomorrow(tasks: list, user_id: str = DEFAULT_USER_ID) -> int:
    """
    Create real task rows for TOMORROW from a list of not-yet-saved
    drafted task objects — the counterpart to defer_task_to_tomorrow()
    for tasks that were never saved to today's plan in the first place
    (e.g. the "defer the rest" side of a Realistic Capacity split,
    where the AI Planner's draft output is trimmed before saving and
    the trimmed-out tasks would otherwise just vanish instead of
    actually showing up tomorrow).

    Creates tomorrow's plan container if it doesn't exist yet.

    Returns:
        How many tasks were successfully saved to tomorrow's plan.
    """
    if not tasks:
        return 0
    db = get_database()
    tomorrow = date.today() + timedelta(days=1)
    target_plan = db.get_plan_by_date(user_id=user_id, plan_date=tomorrow)
    if target_plan is None:
        plan_id = db.create_plan(
            user_id=user_id,
            plan_date=tomorrow,
            raw_input="Tasks deferred from a previous day's capacity check.",
        )
    else:
        plan_id = int(target_plan["plan_id"])
    return save_draft_tasks_to_plan(tasks, plan_id)


def create_category(
    user_id: int,
    name: str,
    color: str = "#3B82F6",
) -> Optional[int]:
    """Create a category for a user. Returns category_id, or None if it
    already exists / creation fails (e.g. duplicate name)."""
    db = get_database()
    try:
        cat_id = db.create_category(user_id=user_id, name=name, color=color)
        load_categories.clear()
        return cat_id
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# Scheduler (deterministic time-slot assignment)
# ─────────────────────────────────────────────────────────────

def run_scheduler_for_plan(
    plan_id: int,
    work_day_start,
    breaks: Optional[list[tuple]] = None,
    blocked_slots: Optional[list[tuple]] = None,
) -> list:
    """
    Run the deterministic Scheduler against an existing plan and persist
    the resulting time slots.

    Args:
        plan_id: The plan whose tasks should be scheduled.
        work_day_start: A ``datetime.time`` marking the start of the day.
        breaks: Optional list of ``(start_time, duration_minutes)`` tuples.
        blocked_slots: Optional list of ``(start_time, end_time)`` tuples
            from Google Calendar events.

    Returns:
        The list of ScheduledTask objects, or [] on failure.
    """
    from scheduler import SchedulingPreferences, UserBreak

    service = get_scheduler_service()
    user_breaks = [
        UserBreak(start_time=start, duration_minutes=minutes)
        for start, minutes in (breaks or [])
    ]
    preferences = SchedulingPreferences(
        work_day_start=work_day_start,
        user_breaks=user_breaks,
    )
    try:
        result = service.schedule_plan(
            plan_id=plan_id,
            preferences=preferences,
            blocked_slots=blocked_slots,
        )
        _LAST_SCHEDULER_ERROR["message"] = None
        return result
    except Exception as exc:
        import traceback
        traceback.print_exc()
        _LAST_SCHEDULER_ERROR["message"] = str(exc)
        return []


def get_last_scheduler_error() -> Optional[str]:
    """
    Return the error message from the most recent failed
    run_scheduler_for_plan()/run_scheduler_for_today() call, or None if
    the last run succeeded (or the scheduler hasn't run yet).

    Call this when run_scheduler_for_today() returns an empty list, to
    show the person the real reason (e.g. "a task can't fit in one day")
    instead of a generic "scheduling failed".
    """
    return _LAST_SCHEDULER_ERROR.get("message")


def get_last_scheduling_conflicts() -> list:
    """Return break-vs-fixed-task conflicts from the most recent
    run_scheduler_for_plan() / run_scheduler_for_today() call.

    Both a user break and a fixed-time task have an immovable time,
    so when their windows overlap the scheduler cannot silently pick
    a winner -- it leaves both times untouched and records the
    conflict here instead. Call this right after running the
    scheduler and show the result to the user if non-empty, e.g.:

        "Could not fit your 11:30-12:00 break: it overlaps your
         fixed task 'Team standup' at 11:15-12:00."

    Returns:
        A list of SchedulingConflict objects (empty if none, or if
        the scheduler has not run yet this session).
    """
    service = get_scheduler_service()
    return getattr(service, "last_conflicts", [])


def get_persisted_scheduling_conflicts(user_id: str = DEFAULT_USER_ID) -> list:
    """Return break-vs-fixed-task conflicts straight from what's already
    saved in the database for today's plan.

    Unlike get_last_scheduling_conflicts(), this does NOT require the
    scheduler to have run in this session — so it also catches a
    conflict left over from an earlier session or a different page
    (e.g. a break added yesterday whose overlap was never resolved).
    Call this on every page load so a stale, unresolved conflict is
    never silently hidden just because nobody happened to press
    "Run Scheduler" this time.

    Returns:
        A list of SchedulingConflict objects (empty if none).
    """
    from scheduler_service import detect_persisted_break_conflicts
    tasks = load_today_tasks(user_id=user_id)
    return detect_persisted_break_conflicts(tasks)


def get_last_fixed_task_conflicts() -> list:
    """Return fixed-task-vs-fixed-task conflicts (two ordinary,
    non-break tasks overlapping each other) from the most recent
    run_scheduler_for_plan() / run_scheduler_for_today() call.

    Counterpart to get_last_scheduling_conflicts() for two ordinary
    fixed-time tasks instead of a break and a task — e.g. two imported
    calendar events both pinned to 10:00-11:00.

    Returns:
        A list of FixedTaskConflict objects (empty if none, or if the
        scheduler has not run yet this session).
    """
    service = get_scheduler_service()
    return getattr(service, "last_fixed_conflicts", [])


def get_persisted_fixed_task_conflicts(user_id: str = DEFAULT_USER_ID) -> list:
    """Return fixed-task-vs-fixed-task conflicts straight from what's
    already saved in the database for today's plan — the
    fixed-vs-fixed counterpart to get_persisted_scheduling_conflicts().
    Does NOT require the scheduler to have run this session.

    Returns:
        A list of FixedTaskConflict objects (empty if none).
    """
    from scheduler_service import detect_persisted_fixed_task_conflicts
    tasks = load_today_tasks(user_id=user_id)
    return detect_persisted_fixed_task_conflicts(tasks)


def reschedule_fixed_task(task_id: int, new_start_time: time) -> bool:
    """
    Shift any fixed-time task (break or not) to a new start time,
    keeping its original duration. Generalises reschedule_break() to
    ordinary fixed-time tasks too, for resolving a fixed-vs-fixed
    conflict (e.g. "move Task B to right after Task A").

    Returns True on success, False on error.
    """
    db = get_database()
    try:
        task = db.get_task(task_id)
        if task is None:
            return False
        duration = int(task["estimated_minutes"])
        new_end_time = (
            datetime.combine(date.today(), new_start_time) + timedelta(minutes=duration)
        ).time()
        db.update_task(
            task_id,
            scheduled_start=new_start_time.strftime("%H:%M"),
            scheduled_end=new_end_time.strftime("%H:%M"),
        )
        return True
    except Exception:
        return False


def run_scheduler_for_today(
    work_day_start,
    breaks: Optional[list[tuple]] = None,
    user_id: str = DEFAULT_USER_ID,
) -> list:
    """Convenience wrapper: schedule today's plan for a user."""
    plan = load_today_plan(user_id)
    if plan is None:
        return []
    blocked = get_google_calendar_blocked_slots(user_id)
    return run_scheduler_for_plan(
        plan_id=plan["plan_id"],
        work_day_start=work_day_start,
        breaks=breaks,
        blocked_slots=blocked,
    )


# ─────────────────────────────────────────────────────────────
# Google Calendar Integration
# ─────────────────────────────────────────────────────────────


def _get_google_client(user_id: int):
    """
    Build a GoogleCalendarClient for the given user, refreshing
    the access token if expired.  Returns None if the user has
    not connected Google Calendar.
    """
    from google_calendar import GoogleCalendarClient, GoogleCalendarError

    db = get_database()
    tokens = db.get_google_tokens(user_id)
    if tokens is None:
        return None

    client = GoogleCalendarClient(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_expiry=tokens["token_expiry"],
    )

    try:
        refreshed = client.refresh_if_expired()
        if refreshed:
            db.update_google_tokens(
                user_id=user_id,
                access_token=refreshed["access_token"],
                token_expiry=(
                    refreshed["token_expiry"].isoformat()
                    if hasattr(refreshed["token_expiry"], "isoformat")
                    else str(refreshed["token_expiry"])
                ),
            )
    except GoogleCalendarError:
        pass  # Use existing token, may still work

    return client


def is_google_calendar_connected(user_id: str = DEFAULT_USER_ID) -> bool:
    """Check if the user has stored Google Calendar OAuth tokens."""
    db = get_database()
    return db.get_google_tokens(user_id) is not None


def get_google_auth_url() -> str:
    """Generate Google OAuth2 consent URL."""
    from google_calendar import GoogleCalendarClient

    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8501")
    return GoogleCalendarClient.build_auth_url(client_id, redirect_uri)


def connect_google_calendar(
    auth_code: str,
    user_id: str = DEFAULT_USER_ID,
) -> bool:
    """Exchange OAuth code for tokens and store them."""
    from google_calendar import GoogleCalendarClient, GoogleCalendarError

    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8501")

    try:
        token_data = GoogleCalendarClient.exchange_code(
            code=auth_code,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        )
        db = get_database()
        db.save_google_tokens(
            user_id=user_id,
            access_token=token_data["access_token"],
            refresh_token=token_data["refresh_token"],
            token_expiry=(
                token_data["token_expiry"].isoformat()
                if hasattr(token_data["token_expiry"], "isoformat")
                else str(token_data["token_expiry"])
            ),
            scopes="calendar.readonly,calendar.events",
        )
        return True
    except GoogleCalendarError as e:
        import traceback
        print(f"[CoachAI] Google Calendar connection failed: {e}")
        traceback.print_exc()
        return False


def disconnect_google_calendar(user_id: str = DEFAULT_USER_ID) -> None:
    """Remove tokens, selected calendars, and all synced events."""
    db = get_database()
    db.delete_all_google_calendar_events(user_id)
    db.delete_selected_calendars(user_id)
    db.delete_google_tokens(user_id)


def fetch_google_calendars(
    user_id: str = DEFAULT_USER_ID,
) -> list[dict]:
    """Fetch available calendars from Google API."""
    from google_calendar import GoogleCalendarError

    client = _get_google_client(user_id)
    if client is None:
        return []
    try:
        return client.list_calendars()
    except GoogleCalendarError:
        return []


def save_selected_calendars(
    user_id: int,
    selections: list[dict],
) -> None:
    """
    Persist the user's calendar choices and clean up events
    from any deselected calendars.
    """
    db = get_database()
    old_ids = set(db.get_selected_calendar_ids(user_id))
    new_ids = {cal["calendar_id"] for cal in selections}

    # Remove events from deselected calendars
    for removed_id in old_ids - new_ids:
        db.delete_google_events_by_calendar(user_id, removed_id)

    db.save_selected_calendars(user_id, selections)


def get_selected_calendars(
    user_id: str = DEFAULT_USER_ID,
) -> list[dict]:
    """Load persisted calendar selections."""
    db = get_database()
    rows = db.get_selected_calendars(user_id)
    return [
        {
            "calendar_id": row["calendar_id"],
            "calendar_name": row["calendar_name"],
            "color": row["color"],
            "is_primary": bool(row["is_primary"]),
        }
        for row in rows
    ]


def sync_google_calendar(
    user_id: str = DEFAULT_USER_ID,
) -> dict:
    """
    Fetch events from selected calendars for today, upsert into DB,
    remove stale events.  Returns a sync summary dict.
    """
    from google_calendar import GoogleCalendarError

    client = _get_google_client(user_id)
    if client is None:
        return {"synced_count": 0, "error": "Not connected"}

    db = get_database()
    selected_ids = db.get_selected_calendar_ids(user_id)
    if not selected_ids:
        return {"synced_count": 0, "error": "No calendars selected"}

    today = date.today()
    today_str = today.isoformat()
    total_synced = 0

    try:
        for cal_id in selected_ids:
            events = client.fetch_events(cal_id, today_str)
            synced_ids = []
            for ev in events:
                db.upsert_google_calendar_event(
                    user_id=user_id,
                    google_event_id=ev["google_event_id"],
                    title=ev["title"],
                    start_time=ev["start_time"],
                    end_time=ev["end_time"],
                    event_date=today_str,
                    calendar_id=ev["calendar_id"],
                )
                synced_ids.append(ev["google_event_id"])
                total_synced += 1

            # Remove events that no longer exist in Google
            db.delete_google_events_not_in(
                user_id, today_str, cal_id, synced_ids
            )

        # Don't silently move anything — just tell the caller which
        # already-scheduled tasks now collide with a freshly-synced
        # event, so the UI can warn the user and let them decide
        # (reschedule manually, edit the task, etc).
        conflicts = find_task_conflicts_with_google_events(user_id)

        return {
            "synced_count": total_synced,
            "calendars_synced": len(selected_ids),
            "conflicts": conflicts,
        }
    except GoogleCalendarError as exc:
        return {"synced_count": total_synced, "error": str(exc)}


def find_task_conflicts_with_google_events(
    user_id: str = DEFAULT_USER_ID,
) -> list[dict]:
    """
    Find already-scheduled tasks in today's plan whose time overlaps a
    synced Google Calendar event, WITHOUT changing anything. Used to
    surface a warning after sync so the user can decide how to resolve
    it themselves.
    """
    from datetime import time as dt_time

    db = get_database()
    plan = db.get_today_plan(user_id)
    if plan is None:
        return []

    blocked = get_google_calendar_blocked_slots(user_id)
    if not blocked:
        return []

    conflicts = []
    for task in db.get_tasks_by_plan(plan["plan_id"]):
        if not task["scheduled_start"] or not task["scheduled_end"]:
            continue
        t_start = dt_time.fromisoformat(task["scheduled_start"])
        t_end = dt_time.fromisoformat(task["scheduled_end"])
        for block_start, block_end in blocked:
            if t_start < block_end and t_end > block_start:
                conflicts.append({
                    "task_id": task["task_id"],
                    "title": task["title"],
                    "scheduled_start": task["scheduled_start"],
                    "scheduled_end": task["scheduled_end"],
                })
                break
    return conflicts


def get_google_calendar_events_today(
    user_id: str = DEFAULT_USER_ID,
) -> list[dict]:
    """Load today's synced Google Calendar events from DB."""
    db = get_database()
    today_str = date.today().isoformat()
    rows = db.get_google_calendar_events(user_id, today_str)
    return [
        {
            "title": row["title"],
            "start_time": row["start_time"],
            "end_time": row["end_time"],
            "calendar_id": row["calendar_id"],
            "google_event_id": row["google_event_id"],
        }
        for row in rows
    ]


def import_calendar_event_as_task(
    plan_id: int,
    event: dict,
    priority: int = 3,
) -> Optional[int]:
    """
    Create a real, fixed-time task from a synced Google Calendar event
    ("Import as Task").

    A synced event otherwise only ever acts as an invisible obstacle:
    get_google_calendar_blocked_slots() feeds it to the Scheduler as a
    blocked_slots entry so other tasks route around it, but it never
    shows up in Tasks/the Timeline and can't be started, completed, or
    tracked like anything else the user is actually doing. This is what
    lets the user opt an event into being a real task instead.

    The new task's google_event_id is set to the event's own id. That
    does double duty: get_google_calendar_blocked_slots() already skips
    any event whose id matches a task's google_event_id (originally so
    an exported task's own event isn't fed back as an obstacle against
    itself) — here it means once imported, the event is no longer ALSO
    counted as a separate blocked slot fighting its own task. It also
    means a later "Export to Google Calendar" on this task updates the
    same event instead of creating a duplicate.

    Args:
        plan_id: The plan to add the task to.
        event: A dict as returned by get_google_calendar_events_today()
            — needs title, start_time ('HH:MM'), end_time ('HH:MM'),
            and google_event_id.
        priority: Priority for the new task (default 3 = Medium) —
            calendar events carry no priority signal of their own.

    Returns:
        The new task_id, or None on failure — including if this event
        was already imported (checked via google_event_id, so calling
        this twice on the same event is safe and never creates a
        duplicate task).
    """
    db = get_database()
    try:
        start_str = str(event.get("start_time") or "")[:5]
        end_str = str(event.get("end_time") or "")[:5]
        start_t = datetime.strptime(start_str, "%H:%M")
        end_t = datetime.strptime(end_str, "%H:%M")
        duration = int((end_t - start_t).total_seconds() // 60)
        if duration <= 0:
            return None

        target_event_id = event.get("google_event_id")
        existing = db.get_tasks_by_plan(plan_id)
        for row in existing:
            row_event_id = row["google_event_id"] if "google_event_id" in row.keys() else None
            if row_event_id and target_event_id and row_event_id == target_event_id:
                return None  # already imported

        order_index = len(existing)
        task_id = db.add_task(
            plan_id=plan_id,
            title=str(event.get("title") or "Untitled event"),
            priority=priority,
            estimated_minutes=duration,
            scheduled_start=start_str,
            scheduled_end=end_str,
            order_index=order_index,
            is_fixed_time=True,
        )
        db.update_task_google_event_id(task_id, target_event_id)
        load_analytics_profile.clear()
        return task_id
    except Exception:
        return None


def get_google_calendar_blocked_slots(
    user_id: str = DEFAULT_USER_ID,
) -> list[tuple]:
    """
    Convert today's Google Calendar events into (start_time, end_time)
    tuples for the Scheduler's blocked_slots parameter.

    Excludes events that the app itself created by exporting a task.
    Without this, an exported task's own event gets synced back in and
    the scheduler treats it as an external obstacle blocking the very
    slot the task already occupies — pushing that task (and everything
    scheduled after it) later every time you export, then reschedule.
    """
    from datetime import time as dt_time

    db = get_database()
    own_event_ids = set()
    plan = db.get_today_plan(user_id)
    if plan is not None:
        for t in db.get_tasks_by_plan(plan["plan_id"]):
            eid = t["google_event_id"] if "google_event_id" in t.keys() else None
            if eid:
                own_event_ids.add(eid)

    events = get_google_calendar_events_today(user_id)
    slots = []
    for ev in events:
        if ev.get("google_event_id") in own_event_ids:
            continue
        try:
            start = dt_time.fromisoformat(ev["start_time"])
            end = dt_time.fromisoformat(ev["end_time"])
            slots.append((start, end))
        except (ValueError, TypeError):
            continue
    return slots


def get_last_sync_time(
    user_id: str = DEFAULT_USER_ID,
) -> Optional[str]:
    """Return the most recent last_synced_at from google_calendar_events."""
    db = get_database()
    row = db.fetch_one(
        """
        SELECT MAX(last_synced_at) AS last_sync
        FROM google_calendar_events
        WHERE user_id = ?
        """,
        (user_id,),
    )
    return row["last_sync"] if row and row["last_sync"] else None


def export_task_to_google_calendar(
    task_id: int,
    user_id: str = DEFAULT_USER_ID,
) -> bool:
    """Create or update a Google Calendar event for a scheduled task."""
    from google_calendar import GoogleCalendarError

    client = _get_google_client(user_id)
    if client is None:
        return False

    db = get_database()
    task = db.get_task(task_id)
    if task is None or not task["scheduled_start"] or not task["scheduled_end"]:
        return False

    plan = db.get_plan_by_id(task["plan_id"])
    if plan is None:
        return False

    plan_date = str(plan["plan_date"])

    try:
        existing_event_id = task["google_event_id"] if "google_event_id" in task.keys() else None

        if existing_event_id:
            client.update_event(
                calendar_id="primary",
                google_event_id=existing_event_id,
                title=task["title"],
                start_time=task["scheduled_start"],
                end_time=task["scheduled_end"],
                event_date=plan_date,
            )
            # google_event_id doesn't change on this path, but the
            # export timestamp still needs to move forward — this is
            # what clears the "stale export" warning after a re-export.
            db.mark_task_exported(task_id)
        else:
            new_event_id = client.create_event(
                calendar_id="primary",
                title=task["title"],
                start_time=task["scheduled_start"],
                end_time=task["scheduled_end"],
                event_date=plan_date,
            )
            db.update_task_google_event_id(task_id, new_event_id)

        return True
    except GoogleCalendarError:
        return False


def export_all_scheduled_tasks(
    user_id: str = DEFAULT_USER_ID,
) -> dict:
    """Export all scheduled tasks from today's plan to Google Calendar."""
    plan = load_today_plan(user_id)
    if plan is None:
        return {"exported": 0, "error": "No plan for today"}

    db = get_database()
    tasks = db.get_tasks_by_plan(plan["plan_id"])
    exported = 0
    errors = 0

    for task in tasks:
        if task["scheduled_start"] and task["scheduled_end"]:
            if export_task_to_google_calendar(task["task_id"], user_id):
                exported += 1
            else:
                errors += 1

    return {"exported": exported, "errors": errors}


def get_stale_export_count(user_id: str = DEFAULT_USER_ID) -> int:
    """
    Count today's tasks whose Google Calendar event no longer matches
    their current scheduled time — i.e. they were exported once, then
    the schedule changed (Start Day, a new blocked slot, a manual
    re-run) without a follow-up export.

    Returns 0 if there's no plan today, or nothing has ever been
    exported (nothing can be "stale" relative to an export that never
    happened).
    """
    plan = load_today_plan(user_id)
    if plan is None:
        return 0
    db = get_database()
    return len(db.get_stale_google_exports(plan["plan_id"]))


# ─────────────────────────────────────────────────────────────
# Cache Management
# ─────────────────────────────────────────────────────────────

def clear_all_caches() -> None:
    """Clear every st.cache_data store used across the app."""
    load_analytics_profile.clear()
    load_pause_matrix.clear()