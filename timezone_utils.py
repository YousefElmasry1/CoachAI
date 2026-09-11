"""
CoachAI - Timezone Utilities
==============================

Single source of truth for converting the UTC timestamps stored in the
database (started_at, completed_at, paused_at, ...) into a user's local
wall-clock time.

Design notes
------------
- All timestamps stay stored in UTC in the database. That part of the
  design was already correct and does not change.
- `users.timezone` stores an IANA timezone name (e.g. "Africa/Cairo"),
  not a fixed numeric offset, so DST (where applicable) is handled
  correctly "for free" by the standard library.
- Right now every caller falls back to "UTC" until the mobile app
  starts sending the device's real timezone at login/launch (see
  Database.update_user_timezone). Once that lands, no other code in
  this module needs to change - callers just start passing a real
  IANA name instead of the "UTC" default.
"""

from __future__ import annotations

from datetime import date, datetime, timezone as dt_timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "UTC"


def safe_zoneinfo(tz_name: Optional[str]) -> ZoneInfo:
    """
    Resolve an IANA timezone name to a ZoneInfo, falling back to UTC
    for anything missing/invalid (bad data should never crash a page).
    """
    if not tz_name:
        return ZoneInfo(DEFAULT_TIMEZONE)
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def utc_naive_to_local(utc_dt: datetime, tz_name: Optional[str]) -> datetime:
    """
    Convert a naive UTC datetime (as stored in the DB, e.g. from
    ``datetime.now(timezone.utc).replace(tzinfo=None)``) into an
    aware local datetime in the user's timezone.

    Args:
        utc_dt: Naive datetime that represents a UTC instant.
        tz_name: IANA timezone name (e.g. "Africa/Cairo"). Falls back
            to UTC if None/invalid.

    Returns:
        Timezone-aware datetime in the target timezone.
    """
    aware_utc = utc_dt.replace(tzinfo=dt_timezone.utc)
    return aware_utc.astimezone(safe_zoneinfo(tz_name))


def local_hour_from_utc_string(
    utc_str: Optional[str], tz_name: Optional[str]
) -> Optional[int]:
    """
    Parse a stored UTC timestamp string and return the hour-of-day
    (0-23) in the user's local timezone.

    This is the piece that was missing from ``get_pause_matrix``: it
    used to take ``.hour`` directly off the raw UTC string, which is
    only correct for users physically in the UTC timezone.

    Args:
        utc_str: ISO-ish timestamp string as stored by sqlite
            (e.g. "2026-08-27 22:14:05"), or None.
        tz_name: IANA timezone name of the user.

    Returns:
        Local hour (0-23), or None if utc_str is missing/unparseable.
    """
    if not utc_str:
        return None
    try:
        naive_utc = datetime.fromisoformat(str(utc_str))
    except (ValueError, TypeError):
        return None
    local_dt = utc_naive_to_local(naive_utc, tz_name)
    return local_dt.hour


def utc_now_naive() -> datetime:
    """
    The single helper every write path should use to timestamp "now" in
    the database: a naive datetime representing the current UTC instant
    (no tzinfo, no trailing 'Z'). Storing naive-but-UTC consistently is
    what makes ``utc_naive_to_local`` safe to apply uniformly on read —
    mixing naive-local and naive-UTC values in the same column is what
    causes silent timezone bugs.
    """
    return datetime.now(dt_timezone.utc).replace(tzinfo=None)


def get_user_timezone(db: Any, user_id: str) -> str:
    """
    Look up a user's stored IANA timezone name, falling back to
    ``DEFAULT_TIMEZONE`` if the user can't be found or has none set.

    This is the ONE place that should ever read ``users.timezone`` off
    the database, so every caller (scheduler, analytics, Google
    Calendar ranges, stale-task cleanup, ...) resolves "the user's
    timezone" the exact same way instead of each guessing at the
    server's local timezone.

    Args:
        db: A Database instance (duck-typed: only needs ``get_user``).
        user_id: Whose timezone to resolve.

    Returns:
        An IANA timezone name, e.g. "Africa/Cairo", or "UTC".
    """
    try:
        user = db.get_user(user_id)
    except Exception:
        return DEFAULT_TIMEZONE
    if not user:
        return DEFAULT_TIMEZONE
    tz_name = user["timezone"] if "timezone" in user.keys() else None
    return tz_name or DEFAULT_TIMEZONE


def user_now(db: Any, user_id: str) -> datetime:
    """
    The current wall-clock moment in a specific user's timezone —
    NEVER the server's local timezone. Use this (not a bare
    ``datetime.now()``) anywhere "now, from this user's point of view"
    is needed: computing "today", the current hour for analytics
    bucketing, scheduler anchoring, break placement, and Google
    Calendar day ranges.

    Args:
        db: A Database instance used to resolve the user's timezone.
        user_id: Whose "now" to compute.

    Returns:
        A timezone-aware datetime in the user's IANA timezone.
    """
    tz_name = get_user_timezone(db, user_id)
    return datetime.now(safe_zoneinfo(tz_name))


def user_today(db: Any, user_id: str) -> date:
    """
    Today's calendar date FROM THE USER'S TIMEZONE, not the server's.

    A user in UTC-5 at 11pm UTC is still on "yesterday" locally; a user
    in UTC+3 at 10pm UTC is already on "tomorrow" locally. Using the
    server's ``date.today()`` for either produces the wrong plan_date,
    the wrong "stale task" cutoff, and the wrong analytics day
    boundary. Always resolve "today" through this helper instead.

    Args:
        db: A Database instance used to resolve the user's timezone.
        user_id: Whose "today" to compute.

    Returns:
        The user's current local calendar date.
    """
    return user_now(db, user_id).date()