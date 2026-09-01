"""
CoachAI – Formatting & HTML Snippet Helpers
========================================

Small, pure, side-effect-free helper functions shared across app.py and
the pages/ modules: date/time/number formatting, icon/color lookups for
priority/status/trend/confidence/burnout, and tiny HTML-fragment builders
(kpi cards, badges, progress bars) that are rendered via
``st.markdown(..., unsafe_allow_html=True)``.

Nothing in this module calls any Streamlit API — it must be safe to
import from any page without side effects (no ``st.*`` calls at import
time). Page-level setup lives in ``layout.page_setup``.
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Optional

from config import (
    BURNOUT_COLORS,
    BURNOUT_LABELS,
    BURNOUT_THRESHOLDS,
    MOTIVATIONAL_QUOTES,
    PRIORITY_ICONS,
    STATUS_ICONS,
    STATUS_LABELS,
    TREND_COLORS,
    TREND_ICONS,
)
from timezone_utils import safe_zoneinfo

# ─────────────────────────────────────────────────────────────
# Greeting & Date Display (timezone-aware)
# ─────────────────────────────────────────────────────────────


def get_greeting(tz_name: Optional[str] = None) -> str:
    """Return a time-of-day greeting in the user's local timezone."""
    hour = datetime.now(safe_zoneinfo(tz_name)).hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


def get_today_display(tz_name: Optional[str] = None) -> str:
    """Return today's date as e.g. 'Sunday, August 31, 2026' in the user's timezone."""
    return datetime.now(safe_zoneinfo(tz_name)).strftime("%A, %B %d, %Y")


def get_daily_quote() -> dict[str, str]:
    """Return a random motivational quote ({'text', 'author'})."""
    return random.choice(MOTIVATIONAL_QUOTES)


# ─────────────────────────────────────────────────────────────
# Formatting
# ─────────────────────────────────────────────────────────────


def format_time_12h(time_str: Optional[str]) -> str:
    """Convert a 'HH:MM' (24h) string into '3:45 PM' style. Safe on None/bad input."""
    if not time_str:
        return "—"
    try:
        parsed = datetime.strptime(str(time_str)[:5], "%H:%M")
    except (ValueError, TypeError):
        return str(time_str)
    formatted = parsed.strftime("%I:%M %p")
    return formatted.lstrip("0") or formatted


def format_date_long(date_str: Optional[str]) -> str:
    """Convert a 'YYYY-MM-DD' string into 'August 31, 2026' style. Safe on bad input."""
    if not date_str:
        return "—"
    try:
        parsed = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return str(date_str)
    return parsed.strftime("%B %d, %Y")


def format_duration(minutes: Optional[int]) -> str:
    """Convert a minute count into '1h 30m' / '45m' / '2h' style."""
    total = int(minutes or 0)
    hours, mins = divmod(total, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


def format_percentage(value: Optional[float]) -> str:
    """Format a 0.0–1.0 fraction as a whole-number percentage string, e.g. '82%'."""
    return f"{(value or 0.0) * 100:.0f}%"


def format_score(score: Optional[float]) -> str:
    """Format a 0–100 composite score as '82/100'."""
    return f"{(score or 0.0):.0f}/100"


# ─────────────────────────────────────────────────────────────
# Icon / Color Lookups
# ─────────────────────────────────────────────────────────────


def get_priority_icon(priority: int) -> str:
    return PRIORITY_ICONS.get(priority, "⚪")


def get_status_icon(status: str) -> str:
    return STATUS_ICONS.get(status, "⏳")


def get_trend_icon(trend: str) -> str:
    return TREND_ICONS.get(trend, "➡️")


def get_trend_color(trend: str) -> str:
    return TREND_COLORS.get(trend, "#6b7280")


def _burnout_key(risk: float) -> str:
    """Resolve a 0–100 burnout risk score to its BURNOUT_THRESHOLDS bucket."""
    risk = risk or 0.0
    for key, (low, high) in BURNOUT_THRESHOLDS.items():
        if low <= risk <= high:
            return key
    return "critical" if risk > 100 else "low"


def get_burnout_color(risk: float) -> str:
    return BURNOUT_COLORS[_burnout_key(risk)]


def get_burnout_label(risk: float) -> str:
    return BURNOUT_LABELS[_burnout_key(risk)]


# ─────────────────────────────────────────────────────────────
# HTML Fragment Builders (paired with styles.py classes)
# ─────────────────────────────────────────────────────────────


def kpi_card_html(label: str, value: str, subtitle: str = "", accent: str = "purple", icon: str = "") -> str:
    """Build a `.kpi-card.kpi-accent-{accent}` fragment (see styles.py)."""
    return f"""
    <div class="kpi-card kpi-accent-{accent}">
        <div class="kpi-label">{icon} {label}</div>
        <div class="kpi-value">{value}</div>
        <div class="kpi-subtitle">{subtitle}</div>
    </div>
    """


def badge_html(text: str, kind: str = "muted") -> str:
    """Build a generic `.badge.badge-{kind}` fragment. kind: success|warning|danger|info|muted."""
    return f'<span class="badge badge-{kind}">{text}</span>'


_STATUS_BADGE_KIND = {
    "completed": "success",
    "failed": "danger",
    "in_progress": "info",
    "pending": "muted",
}


def status_badge_html(status: str) -> str:
    """Build a task-status badge using STATUS_LABELS/STATUS_ICONS from config."""
    label = STATUS_LABELS.get(status, str(status).replace("_", " ").title())
    icon = STATUS_ICONS.get(status, "")
    kind = _STATUS_BADGE_KIND.get(status, "muted")
    return badge_html(f"{icon} {label}".strip(), kind)


_CONFIDENCE_BADGE_KIND = {
    "high": "success",
    "medium": "warning",
    "low": "warning",
    "insufficient": "danger",
}


def confidence_badge_html(level: str) -> str:
    """Build a confidence-level badge (high/medium/low/insufficient, see CONFIDENCE_COLORS)."""
    level_key = (level or "insufficient").lower()
    kind = _CONFIDENCE_BADGE_KIND.get(level_key, "muted")
    return badge_html(level_key.title(), kind)


def progress_bar_html(value: float, color: Optional[str] = None, height: int = 8) -> str:
    """Build a `.custom-progress` bar. value is a 0.0–1.0 fraction."""
    pct = max(0.0, min(1.0, value or 0.0)) * 100
    bar_color = color or "var(--accent)"
    return f"""
    <div class="custom-progress" style="height:{height}px;">
        <div class="bar" style="width:{pct}%; background:{bar_color};"></div>
    </div>
    """