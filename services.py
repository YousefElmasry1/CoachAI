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
) -> list:
    """
    Run the deterministic Scheduler against an existing plan and persist
    the resulting time slots.

    Args:
        plan_id: The plan whose tasks should be scheduled.
        work_day_start: A ``datetime.time`` marking the start of the day.
        breaks: Optional list of ``(start_time, duration_minutes)`` tuples.

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
        return service.schedule_plan(plan_id=plan_id, preferences=preferences)
    except Exception:
        import traceback
        traceback.print_exc()  # prints the real error to the terminal running `streamlit run`
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
    return run_scheduler_for_plan(
        plan_id=plan["plan_id"],
        work_day_start=work_day_start,
        breaks=breaks,
    )


# ─────────────────────────────────────────────────────────────
# Cache Management
# ─────────────────────────────────────────────────────────────

def clear_all_caches() -> None:
    """Clear every st.cache_data store used across the app."""
    load_analytics_profile.clear()