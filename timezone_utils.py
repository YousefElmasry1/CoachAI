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

from datetime import datetime, timezone as dt_timezone
from typing import Optional
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