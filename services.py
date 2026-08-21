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
from datetime import date, timedelta
from typing import Any, Optional

import streamlit as st

from config import DEFAULT_ANALYTICS_WINDOW, DEFAULT_USER_ID

_local = threading.local()


# ─────────────────────────────────────────────────────────────
# Per-Session Identity (lightweight guest accounts — no real auth)
# ─────────────────────────────────────────────────────────────

def get_current_user_id() -> int:
    """
    Return the user_id for the current browser session.

    Every visitor gets their own guest row in `users` (created by
    create_guest_user() once they answer the name prompt), so their
    plans/tasks/categories/analytics never mix with anyone else's.
    Falls back to DEFAULT_USER_ID if that hasn't happened yet (e.g.
    the key is missing, or present but still None).
    """
    user_id = st.session_state.get("user_id")
    return user_id if user_id is not None else DEFAULT_USER_ID


def create_guest_user(display_name: str) -> Optional[int]:
    """
    Register a brand-new, fully isolated guest account for this
    browser session. Not real authentication (no login, no password
    the user knows) — just a unique row so this visitor's data is
    separate from everyone else's.

    Returns the new user_id, or None on failure.
    """
    db = get_database()
    guest_email = f"guest-{uuid.uuid4().hex}@coachai.local"
    guest_password_placeholder = uuid.uuid4().hex
    try:
        return db.create_user(
            email=guest_email,
            password_hash=guest_password_placeholder,
            display_name=display_name,
        )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# Real Accounts (email + password, so people can come back to the
# same data later). Hashing uses only the standard library (PBKDF2)
# so no new dependency is needed.
# ─────────────────────────────────────────────────────────────

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


def sign_up(email: str, password: str, display_name: str) -> tuple[Optional[int], Optional[str]]:
    """
    Create a real account with email + password.

    Returns:
        (user_id, None) on success, or (None, error_message) on failure
        (e.g. the email is already registered).
    """
    cleaned_email = email.strip().lower()
    if not cleaned_email or "@" not in cleaned_email:
        return None, "Please enter a valid email address."
    if len(password) < 6:
        return None, "Password must be at least 6 characters."
    if not display_name.strip():
        return None, "Please enter a name."

    db = get_database()
    if db.get_user_by_email(cleaned_email) is not None:
        return None, "An account with this email already exists — try logging in instead."

    try:
        user_id = db.create_user(
            email=cleaned_email,
            password_hash=_hash_password(password),
            display_name=display_name.strip(),
        )
        return user_id, None
    except Exception:
        return None, "Something went wrong creating your account. Please try again."


def log_in(email: str, password: str) -> tuple[Optional[int], Optional[str]]:
    """
    Verify email + password against an existing account.

    Returns:
        (user_id, display_name) on success, or (None, error_message) on
        failure (unknown email or wrong password — same generic message
        for both, so we don't leak which emails are registered).
    """
    cleaned_email = email.strip().lower()
    db = get_database()
    row = db.get_user_by_email(cleaned_email)
    if row is None or not _verify_password(password, row["password_hash"]):
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
    user_id: int = DEFAULT_USER_ID,
    window_days: int = DEFAULT_ANALYTICS_WINDOW,
) -> Any:
    """
    Build and cache the full AnalyticsProfile.

    TTL = 5 minutes so the profile refreshes periodically
    without hammering the database.
    """
    engine = get_analytics_engine()
    return engine.build_profile(user_id=user_id, window_days=window_days)


def load_analytics_dashboard(
    user_id: int = DEFAULT_USER_ID,
    window_days: int = DEFAULT_ANALYTICS_WINDOW,
) -> dict[str, Any]:
    """Load analytics formatted for dashboard consumption."""
    profile = load_analytics_profile(user_id, window_days)
    formatter = get_analytics_formatter()
    return formatter.to_dashboard(profile)


def load_analytics_summary(
    user_id: int = DEFAULT_USER_ID,
    window_days: int = DEFAULT_ANALYTICS_WINDOW,
) -> str:
    """Load a short text summary of analytics."""
    profile = load_analytics_profile(user_id, window_days)
    formatter = get_analytics_formatter()
    return formatter.to_summary(profile)


# ─────────────────────────────────────────────────────────────
# User Data
# ─────────────────────────────────────────────────────────────

def load_user(user_id: int = DEFAULT_USER_ID) -> dict:
    """Load user information as a dict. Returns empty dict if not found."""
    db = get_database()
    row = db.get_user(user_id)
    if row is None:
        return {}
    return dict(row)


def load_user_profile(user_id: int = DEFAULT_USER_ID) -> dict:
    """Load the user's cached profile from user_profiles table."""
    db = get_database()
    row = db.get_profile(user_id)
    if row is None:
        return {}
    return dict(row)


def load_user_badges(user_id: int = DEFAULT_USER_ID) -> list[dict]:
    """Load badges earned by the user."""
    db = get_database()
    rows = db.get_user_badges(user_id)
    return [dict(r) for r in rows]


def load_all_badges() -> list[dict]:
    """Load all badge definitions."""
    db = get_database()
    rows = db.get_all_badges()
    return [dict(r) for r in rows]


def load_categories(user_id: int = DEFAULT_USER_ID) -> list[dict]:
    """Load user's categories."""
    db = get_database()
    rows = db.get_categories(user_id)
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────
# Plan & Task Data
# ─────────────────────────────────────────────────────────────

def load_today_plan(user_id: int = DEFAULT_USER_ID) -> Optional[dict]:
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


def load_today_tasks(user_id: int = DEFAULT_USER_ID) -> list[dict]:
    """Load today's tasks. Returns empty list if no plan."""
    plan = load_today_plan(user_id)
    if plan is None:
        return []
    return load_plan_tasks(plan["plan_id"])


def load_recent_plans(
    user_id: int = DEFAULT_USER_ID,
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


def close_out_stale_tasks(user_id: int = DEFAULT_USER_ID) -> int:
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

def load_recommendations_today(user_id: int = DEFAULT_USER_ID) -> Any:
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
        db_size = os.path.getsize(db.db_path) if os.path.exists(db.db_path) else 0
        return {
            "connected": True,
            "path": db.db_path,
            "size_bytes": db_size,
            "size_display": _format_file_size(db_size),
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
    user_id: int = DEFAULT_USER_ID,
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
    user_id: int = DEFAULT_USER_ID,
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


def draft_today_plan(raw_input: str, user_id: int = DEFAULT_USER_ID) -> Any:
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
    user_id: int = DEFAULT_USER_ID,
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
    user_id: int = DEFAULT_USER_ID,
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


def defer_task_to_tomorrow(task_id: int, user_id: int = DEFAULT_USER_ID) -> bool:
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
        return service.schedule_plan(
            plan_id=plan_id,
            preferences=preferences,
            blocked_slots=blocked_slots,
        )
    except Exception:
        import traceback
        traceback.print_exc()
        return []


def run_scheduler_for_today(
    work_day_start,
    breaks: Optional[list[tuple]] = None,
    user_id: int = DEFAULT_USER_ID,
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


def is_google_calendar_connected(user_id: int = DEFAULT_USER_ID) -> bool:
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
    user_id: int = DEFAULT_USER_ID,
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


def disconnect_google_calendar(user_id: int = DEFAULT_USER_ID) -> None:
    """Remove tokens, selected calendars, and all synced events."""
    db = get_database()
    db.delete_all_google_calendar_events(user_id)
    db.delete_selected_calendars(user_id)
    db.delete_google_tokens(user_id)


def fetch_google_calendars(
    user_id: int = DEFAULT_USER_ID,
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
    user_id: int = DEFAULT_USER_ID,
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
    user_id: int = DEFAULT_USER_ID,
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
    user_id: int = DEFAULT_USER_ID,
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
    user_id: int = DEFAULT_USER_ID,
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


def get_google_calendar_blocked_slots(
    user_id: int = DEFAULT_USER_ID,
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
    user_id: int = DEFAULT_USER_ID,
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
    user_id: int = DEFAULT_USER_ID,
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
    user_id: int = DEFAULT_USER_ID,
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


# ─────────────────────────────────────────────────────────────
# Cache Management
# ─────────────────────────────────────────────────────────────

def clear_all_caches() -> None:
    """Clear every st.cache_data store used across the app."""
    load_analytics_profile.clear()