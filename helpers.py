"""
CoachAI – Helper Utilities
============================

Pure formatting and display helper functions.
No backend imports — this module is dependency-free.
"""

from __future__ import annotations

import hashlib
import random
from datetime import date, datetime
from typing import Any, Optional

from config import (
    BURNOUT_COLORS,
    BURNOUT_LABELS,
    BURNOUT_THRESHOLDS,
    CONFIDENCE_COLORS,
    FAILURE_REASONS,
    MOTIVATIONAL_QUOTES,
    PRIORITY_COLORS,
    PRIORITY_ICONS,
    PRIORITY_LABELS,
    STATUS_COLORS,
    STATUS_ICONS,
    STATUS_LABELS,
    TREND_COLORS,
    TREND_ICONS,
)


# ─────────────────────────────────────────────────────────────
# Time & Date Formatting
# ─────────────────────────────────────────────────────────────

def format_time_12h(time_str: Optional[str]) -> str:
    """Convert 'HH:MM' to '8:00 AM' format."""
    if not time_str:
        return "—"
    try:
        parts = str(time_str).split(":")
        h, m = int(parts[0]), int(parts[1])
        period = "AM" if h < 12 else "PM"
        display_h = h % 12 or 12
        return f"{display_h}:{m:02d} {period}"
    except (ValueError, IndexError):
        return str(time_str)


def format_time_24h(time_str: Optional[str]) -> str:
    """Normalise time to HH:MM."""
    if not time_str:
        return "—"
    try:
        parts = str(time_str).split(":")
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    except (ValueError, IndexError):
        return str(time_str)


def format_duration(minutes: Optional[int]) -> str:
    """Convert minutes to human-readable duration: 90 → '1h 30m'."""
    if minutes is None or minutes <= 0:
        return "—"
    hours = minutes // 60
    mins = minutes % 60
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


def format_date_long(date_str: Optional[str]) -> str:
    """Convert ISO date to 'July 25, 2026'."""
    if not date_str:
        return "—"
    try:
        d = date.fromisoformat(str(date_str))
        return d.strftime("%B %d, %Y")
    except (ValueError, TypeError):
        return str(date_str)


def format_date_short(date_str: Optional[str]) -> str:
    """Convert ISO date to 'Jul 25'."""
    if not date_str:
        return "—"
    try:
        d = date.fromisoformat(str(date_str))
        return d.strftime("%b %d")
    except (ValueError, TypeError):
        return str(date_str)


def format_date_relative(date_str: Optional[str]) -> str:
    """Convert ISO date to 'Today', 'Yesterday', '3 days ago', etc."""
    if not date_str:
        return "—"
    try:
        d = date.fromisoformat(str(date_str))
        diff = (date.today() - d).days
        if diff == 0:
            return "Today"
        if diff == 1:
            return "Yesterday"
        if diff < 7:
            return f"{diff} days ago"
        if diff < 30:
            weeks = diff // 7
            return f"{weeks} week{'s' if weeks > 1 else ''} ago"
        return d.strftime("%b %d")
    except (ValueError, TypeError):
        return str(date_str)


def format_percentage(value: Optional[float], decimals: int = 0) -> str:
    """Convert 0.75 → '75%'."""
    if value is None:
        return "—"
    return f"{value * 100:.{decimals}f}%"


def format_score(value: Optional[float], max_val: float = 100.0) -> str:
    """Format a score like 72.5 → '72.5'."""
    if value is None:
        return "—"
    return f"{value:.1f}"


# ─────────────────────────────────────────────────────────────
# Priority & Status Helpers
# ─────────────────────────────────────────────────────────────

def get_priority_label(priority: int) -> str:
    """Get human label for priority level."""
    return PRIORITY_LABELS.get(priority, f"P{priority}")


def get_priority_color(priority: int) -> str:
    """Get hex color for priority level."""
    return PRIORITY_COLORS.get(priority, "#6b7280")


def get_priority_icon(priority: int) -> str:
    """Get emoji icon for priority level."""
    return PRIORITY_ICONS.get(priority, "⚪")


def get_status_label(status: str) -> str:
    """Get display label for task status."""
    return STATUS_LABELS.get(status, status.replace("_", " ").title())


def get_status_color(status: str) -> str:
    """Get hex color for task status."""
    return STATUS_COLORS.get(status, "#6b7280")


def get_status_icon(status: str) -> str:
    """Get emoji icon for task status."""
    return STATUS_ICONS.get(status, "•")


# ─────────────────────────────────────────────────────────────
# Trend, Confidence, Burnout
# ─────────────────────────────────────────────────────────────

def get_trend_icon(direction: str) -> str:
    """Get emoji for trend direction."""
    return TREND_ICONS.get(direction, "➡️")


def get_trend_color(direction: str) -> str:
    """Get hex color for trend direction."""
    return TREND_COLORS.get(direction, "#6b7280")


def get_confidence_color(level: str) -> str:
    """Get hex color for confidence level."""
    return CONFIDENCE_COLORS.get(level, "#6b7280")


def get_burnout_level(risk: float) -> str:
    """Map burnout risk score (0–100) to severity key."""
    for level, (low, high) in BURNOUT_THRESHOLDS.items():
        if low <= risk < high:
            return level
    return "critical"


def get_burnout_label(risk: float) -> str:
    """Get display label for burnout risk level."""
    return BURNOUT_LABELS.get(get_burnout_level(risk), "Unknown")


def get_burnout_color(risk: float) -> str:
    """Get hex color for burnout risk level."""
    return BURNOUT_COLORS.get(get_burnout_level(risk), "#ef4444")


# ─────────────────────────────────────────────────────────────
# HTML Builders
# ─────────────────────────────────────────────────────────────

def badge_html(text: str, variant: str = "muted") -> str:
    """Return a styled badge HTML span."""
    return f'<span class="badge badge-{variant}">{text}</span>'


def status_badge_html(status: str) -> str:
    """Return a badge colored by task status."""
    variant_map = {
        "completed": "success",
        "failed": "danger",
        "in_progress": "info",
        "pending": "muted",
    }
    variant = variant_map.get(status, "muted")
    icon = get_status_icon(status)
    label = get_status_label(status)
    return f'<span class="badge badge-{variant}">{icon} {label}</span>'


def priority_badge_html(priority: int) -> str:
    """Return a badge colored by priority level."""
    color = get_priority_color(priority)
    label = get_priority_label(priority)
    return (
        f'<span class="badge" style="background: {color}20; color: {color};">'
        f'{get_priority_icon(priority)} {label}</span>'
    )


def confidence_badge_html(level: str) -> str:
    """Return a badge colored by confidence level."""
    color = get_confidence_color(level)
    return (
        f'<span class="badge" style="background: {color}18; color: {color};">'
        f'{level.title()} Confidence</span>'
    )


def kpi_card_html(
    label: str,
    value: str,
    subtitle: str = "",
    accent: str = "purple",
    icon: str = "",
) -> str:
    """Render a premium KPI card as raw HTML."""
    icon_html = f'<span style="font-size:1.4rem;margin-right:4px;">{icon}</span>' if icon else ""
    subtitle_html = f'<div class="kpi-subtitle">{subtitle}</div>' if subtitle else ""
    return f"""
    <div class="kpi-card kpi-accent-{accent}">
        <div class="kpi-label">{icon_html}{label}</div>
        <div class="kpi-value">{value}</div>
        {subtitle_html}
    </div>
    """


def progress_bar_html(value: float, color: str = "#6c63ff", height: int = 8) -> str:
    """Render a custom progress bar (value 0.0–1.0)."""
    pct = max(0.0, min(1.0, value)) * 100
    return f"""
    <div class="custom-progress" style="height:{height}px;">
        <div class="bar" style="width:{pct:.1f}%;background:{color};"></div>
    </div>
    """


# ─────────────────────────────────────────────────────────────
# Data Conversion
# ─────────────────────────────────────────────────────────────

def row_to_dict(row: Any) -> dict:
    """Convert a sqlite3.Row (or dict-like object) to a plain dict."""
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def rows_to_dicts(rows: list) -> list[dict]:
    """Convert a list of sqlite3.Row objects to list of dicts."""
    return [row_to_dict(r) for r in rows]


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safe division with zero guard."""
    if denominator == 0:
        return default
    return numerator / denominator


# ─────────────────────────────────────────────────────────────
# Motivational Quotes
# ─────────────────────────────────────────────────────────────

def get_daily_quote() -> dict[str, str]:
    """Get a deterministic daily quote (same quote all day)."""
    today_seed = hashlib.md5(date.today().isoformat().encode()).hexdigest()
    idx = int(today_seed, 16) % len(MOTIVATIONAL_QUOTES)
    return MOTIVATIONAL_QUOTES[idx]


# ─────────────────────────────────────────────────────────────
# Greeting
# ─────────────────────────────────────────────────────────────

def get_greeting() -> str:
    """Get a time-appropriate greeting."""
    hour = datetime.now().hour
    if hour < 12:
        return "Good Morning"
    if hour < 17:
        return "Good Afternoon"
    return "Good Evening"


def get_today_display() -> str:
    """Get today's date formatted nicely."""
    return datetime.now().strftime("%A, %B %d, %Y")
