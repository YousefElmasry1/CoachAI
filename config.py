"""
CoachAI – Application Configuration
=====================================

Central configuration for the Streamlit frontend.
Contains all constants, color palettes, page metadata, and feature flags.

Never imports backend modules — this is a pure-config file.
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────
# Application Metadata
# ─────────────────────────────────────────────────────────────

APP_NAME: str = "CoachAI"
APP_VERSION: str = "1.0.0"
APP_TAGLINE: str = "Your AI-Powered Productivity Coach"
APP_DESCRIPTION: str = (
    "An intelligent productivity platform that analyzes your habits, "
    "schedules your day, and provides personalized coaching."
)

# ─────────────────────────────────────────────────────────────
# Default User (for demo / single-user mode)
# ─────────────────────────────────────────────────────────────

DEFAULT_USER_ID: int = 1
DEFAULT_ANALYTICS_WINDOW: int = 30  # days

# ─────────────────────────────────────────────────────────────
# Color Palette — Dark Theme
# ─────────────────────────────────────────────────────────────

DARK_COLORS: dict[str, str] = {
    "bg_primary": "#0a0a0f",
    "bg_secondary": "#12121a",
    "bg_card": "#1a1a2e",
    "bg_card_hover": "#1f1f35",
    "bg_elevated": "#16213e",
    "border": "rgba(255,255,255,0.06)",
    "border_hover": "rgba(255,255,255,0.12)",
    "text_primary": "#f0f0f5",
    "text_secondary": "#8888a0",
    "text_muted": "#55556a",
    "accent_primary": "#6c63ff",
    "accent_secondary": "#a78bfa",
    "accent_gradient_start": "#6c63ff",
    "accent_gradient_end": "#a78bfa",
    "success": "#10b981",
    "success_bg": "rgba(16,185,129,0.1)",
    "warning": "#f59e0b",
    "warning_bg": "rgba(245,158,11,0.1)",
    "danger": "#ef4444",
    "danger_bg": "rgba(239,68,68,0.1)",
    "info": "#3b82f6",
    "info_bg": "rgba(59,130,246,0.1)",
}

# ─────────────────────────────────────────────────────────────
# Color Palette — Light Theme
# ─────────────────────────────────────────────────────────────

LIGHT_COLORS: dict[str, str] = {
    "bg_primary": "#f8f9fc",
    "bg_secondary": "#ffffff",
    "bg_card": "#ffffff",
    "bg_card_hover": "#f3f4f8",
    "bg_elevated": "#f0f2f8",
    "border": "rgba(0,0,0,0.06)",
    "border_hover": "rgba(0,0,0,0.12)",
    "text_primary": "#1a1a2e",
    "text_secondary": "#64748b",
    "text_muted": "#94a3b8",
    "accent_primary": "#6c63ff",
    "accent_secondary": "#8b7cf7",
    "accent_gradient_start": "#6c63ff",
    "accent_gradient_end": "#a78bfa",
    "success": "#059669",
    "success_bg": "rgba(5,150,105,0.08)",
    "warning": "#d97706",
    "warning_bg": "rgba(217,119,6,0.08)",
    "danger": "#dc2626",
    "danger_bg": "rgba(220,38,38,0.08)",
    "info": "#2563eb",
    "info_bg": "rgba(37,99,235,0.08)",
}

# ─────────────────────────────────────────────────────────────
# Priority System
# ─────────────────────────────────────────────────────────────

PRIORITY_LABELS: dict[int, str] = {
    1: "Critical",
    2: "High",
    3: "Medium",
    4: "Low",
    5: "Optional",
}

PRIORITY_COLORS: dict[int, str] = {
    1: "#ef4444",
    2: "#f97316",
    3: "#eab308",
    4: "#3b82f6",
    5: "#6b7280",
}

PRIORITY_ICONS: dict[int, str] = {
    1: "🔴",
    2: "🟠",
    3: "🟡",
    4: "🔵",
    5: "⚪",
}

# ─────────────────────────────────────────────────────────────
# Task Status System
# ─────────────────────────────────────────────────────────────

STATUS_LABELS: dict[str, str] = {
    "pending": "Pending",
    "in_progress": "In Progress",
    "completed": "Completed",
    "failed": "Failed",
}

STATUS_COLORS: dict[str, str] = {
    "pending": "#6b7280",
    "in_progress": "#3b82f6",
    "completed": "#10b981",
    "failed": "#ef4444",
}

STATUS_ICONS: dict[str, str] = {
    "pending": "⏳",
    "in_progress": "🔄",
    "completed": "✅",
    "failed": "❌",
}

# Allowed failure reasons (must match schema.sql CHECK constraint)
FAILURE_REASONS: list[str] = [
    "Harder than expected",
    "Distracted",
    "Tired",
    "Unexpected event",
    "Changed priorities",
    "Ran out of time",
]

# ─────────────────────────────────────────────────────────────
# Trend & Confidence
# ─────────────────────────────────────────────────────────────

TREND_ICONS: dict[str, str] = {
    "improving": "📈",
    "declining": "📉",
    "stable": "➡️",
}

TREND_COLORS: dict[str, str] = {
    "improving": "#10b981",
    "declining": "#ef4444",
    "stable": "#6b7280",
}

CONFIDENCE_COLORS: dict[str, str] = {
    "high": "#10b981",
    "medium": "#f59e0b",
    "low": "#f97316",
    "insufficient": "#ef4444",
}

# ─────────────────────────────────────────────────────────────
# Chart Configuration
# ─────────────────────────────────────────────────────────────

CHART_COLORS: list[str] = [
    "#6c63ff", "#a78bfa", "#3b82f6", "#10b981",
    "#f59e0b", "#ef4444", "#ec4899", "#14b8a6",
    "#8b5cf6", "#06b6d4", "#84cc16", "#f97316",
]

PLOTLY_LAYOUT_DEFAULTS: dict = {
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "font": {"family": "Inter, -apple-system, sans-serif", "color": "#8888a0"},
    "margin": {"l": 20, "r": 20, "t": 40, "b": 20},
    "showlegend": False,
}

# ─────────────────────────────────────────────────────────────
# Motivational Quotes
# ─────────────────────────────────────────────────────────────

MOTIVATIONAL_QUOTES: list[dict[str, str]] = [
    {"text": "The secret of getting ahead is getting started.", "author": "Mark Twain"},
    {"text": "Productivity is never an accident. It is always the result of a commitment to excellence.", "author": "Paul J. Meyer"},
    {"text": "Focus on being productive instead of busy.", "author": "Tim Ferriss"},
    {"text": "It's not always that we need to do more but rather that we need to focus on less.", "author": "Nathan W. Morris"},
    {"text": "The key is not to prioritize what's on your schedule, but to schedule your priorities.", "author": "Stephen Covey"},
    {"text": "Success is the sum of small efforts repeated day in and day out.", "author": "Robert Collier"},
    {"text": "Don't watch the clock; do what it does. Keep going.", "author": "Sam Levenson"},
    {"text": "You don't have to be great to start, but you have to start to be great.", "author": "Zig Ziglar"},
    {"text": "Action is the foundational key to all success.", "author": "Pablo Picasso"},
    {"text": "Well done is better than well said.", "author": "Benjamin Franklin"},
    {"text": "Start where you are. Use what you have. Do what you can.", "author": "Arthur Ashe"},
    {"text": "Small daily improvements over time lead to stunning results.", "author": "Robin Sharma"},
]

# ─────────────────────────────────────────────────────────────
# Realistic Capacity — Pre-Plan Warning
# ─────────────────────────────────────────────────────────────
# When a freshly-generated (not yet saved) plan's total planned minutes
# for the day exceeds the user's historical recommended_daily_minutes
# by at least this fraction, the Today's Schedule page shows a
# capacity warning instead of silently saving the plan.
CAPACITY_OVERLOAD_MARGIN: float = 0.15  # 15% over recommended triggers it

# Below this confidence level there isn't enough history to trust the
# recommendation, so the warning is skipped entirely rather than
# nagging a brand-new user with a guess.
CAPACITY_MIN_CONFIDENCE_TO_WARN: set[str] = {"low", "medium", "high"}

# ─────────────────────────────────────────────────────────────
# Burnout Thresholds
# ─────────────────────────────────────────────────────────────

BURNOUT_THRESHOLDS: dict[str, tuple[float, float]] = {
    "low": (0.0, 30.0),
    "moderate": (30.0, 55.0),
    "high": (55.0, 75.0),
    "critical": (75.0, 100.0),
}

BURNOUT_LABELS: dict[str, str] = {
    "low": "Low Risk",
    "moderate": "Moderate",
    "high": "High Risk",
    "critical": "Critical",
}

BURNOUT_COLORS: dict[str, str] = {
    "low": "#10b981",
    "moderate": "#f59e0b",
    "high": "#f97316",
    "critical": "#ef4444",
}

# ─────────────────────────────────────────────────────────────
# Weekday Names
# ─────────────────────────────────────────────────────────────

WEEKDAY_NAMES: list[str] = [
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
]

WEEKDAY_SHORT: list[str] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# ─────────────────────────────────────────────────────────────
# Google Calendar Integration
# ─────────────────────────────────────────────────────────────

GOOGLE_CALENDAR_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]
GOOGLE_CALENDAR_SYNC_INTERVAL_MINUTES: int = 15
GOOGLE_CALENDAR_EVENT_COLOR: str = "#4285F4"  # Google Blue (fallback)
# ─────────────────────────────────────────────────────────────
# Timezones (manual selector — temporary, until the mobile app
# sends the device's real IANA timezone automatically)
# ─────────────────────────────────────────────────────────────

# A curated shortlist rather than the full ~600-zone IANA database,
# for a usable dropdown. (city, IANA name) pairs, roughly ordered by
# UTC offset west-to-east. Extend freely — any valid IANA name works,
# this list only affects what's easy to pick from Settings.
TIMEZONE_CHOICES: list[tuple[str, str]] = [
    ("UTC", "UTC"),
    ("Los Angeles (USA)", "America/Los_Angeles"),
    ("Denver (USA)", "America/Denver"),
    ("Chicago (USA)", "America/Chicago"),
    ("New York (USA)", "America/New_York"),
    ("São Paulo (Brazil)", "America/Sao_Paulo"),
    ("London (UK)", "Europe/London"),
    ("Paris (France)", "Europe/Paris"),
    ("Cairo (Egypt)", "Africa/Cairo"),
    ("Riyadh (Saudi Arabia)", "Asia/Riyadh"),
    ("Dubai (UAE)", "Asia/Dubai"),
    ("Karachi (Pakistan)", "Asia/Karachi"),
    ("Delhi (India)", "Asia/Kolkata"),
    ("Dhaka (Bangladesh)", "Asia/Dhaka"),
    ("Bangkok (Thailand)", "Asia/Bangkok"),
    ("Singapore", "Asia/Singapore"),
    ("Shanghai (China)", "Asia/Shanghai"),
    ("Tokyo (Japan)", "Asia/Tokyo"),
    ("Sydney (Australia)", "Australia/Sydney"),
]