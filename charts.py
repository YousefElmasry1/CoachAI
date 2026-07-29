"""
CoachAI – Plotly Chart Factory
================================

All chart creation functions for the dashboard.
Returns plotly.graph_objects.Figure instances with consistent theming.
"""

from __future__ import annotations

from typing import Any, Optional

import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config import CHART_COLORS, DARK_COLORS, LIGHT_COLORS, PLOTLY_LAYOUT_DEFAULTS, WEEKDAY_SHORT


def _theme() -> dict[str, str]:
    """
    Read the app's current dark/light mode from session_state and return
    the matching color palette. Chart text/number colors must follow this
    — the old hardcoded dark-theme hex values (e.g. near-white "#f0f0f5")
    become invisible once the app is switched to light mode.
    """
    try:
        dark_mode = st.session_state.get("dark_mode", True)
    except Exception:
        dark_mode = True
    return DARK_COLORS if dark_mode else LIGHT_COLORS


def _grid_color(alpha: float = 0.05) -> str:
    """Gridline color that stays faint-but-visible in both themes."""
    theme = _theme()
    is_dark = theme is DARK_COLORS
    return f"rgba(255,255,255,{alpha})" if is_dark else f"rgba(0,0,0,{alpha})"


def _base_layout(**overrides) -> dict:
    """Merge default layout (with theme-correct font color) with overrides."""
    theme = _theme()
    layout = {**PLOTLY_LAYOUT_DEFAULTS}
    layout["font"] = {**layout.get("font", {}), "color": theme["text_secondary"]}
    layout.update(overrides)
    return layout


def hex_to_rgba(hex_color: str, alpha: float = 0.08) -> str:
    """
    Convert a '#rrggbb' hex color into an 'rgba(r,g,b,a)' string.

    Plotly's color properties (e.g. fillcolor) do not accept 8-digit
    hex colors with an alpha channel (e.g. '#6c63ff15') — only 3/6-digit
    hex, or explicit rgb/rgba/hsl/hsv strings. This helper produces a
    format Plotly always accepts.

    Args:
        hex_color: A '#rrggbb' (or '#rgb') color string.
        alpha: Opacity from 0.0 (transparent) to 1.0 (opaque).

    Returns:
        An 'rgba(r, g, b, a)' string. Falls back to a translucent grey
        if hex_color isn't a valid hex string.
    """
    h = (hex_color or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return f"rgba(107, 114, 128, {alpha})"
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return f"rgba(107, 114, 128, {alpha})"
    return f"rgba({r}, {g}, {b}, {alpha})"


# ─────────────────────────────────────────────────────────────
# Gauge / Indicator
# ─────────────────────────────────────────────────────────────

def create_gauge(
    value: float,
    title: str = "",
    max_val: float = 100.0,
    suffix: str = "",
    ranges: Optional[list[dict]] = None,
    height: int = 250,
) -> go.Figure:
    """
    Create a Plotly gauge indicator.

    Args:
        value: Current value.
        title: Gauge title.
        max_val: Maximum gauge value.
        suffix: Value suffix (e.g. '%').
        ranges: List of dicts with 'range' and 'color' keys.
        height: Chart height in pixels.
    """
    if ranges is None:
        ranges = [
            {"range": [0, max_val * 0.3], "color": "rgba(239,68,68,0.15)"},
            {"range": [max_val * 0.3, max_val * 0.6], "color": "rgba(245,158,11,0.15)"},
            {"range": [max_val * 0.6, max_val * 0.85], "color": "rgba(59,130,246,0.15)"},
            {"range": [max_val * 0.85, max_val], "color": "rgba(16,185,129,0.15)"},
        ]

    steps = [{"range": r["range"], "color": r["color"]} for r in ranges]
    theme = _theme()

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=value,
        number={"suffix": suffix, "font": {"size": 36, "family": "Inter", "color": theme["text_primary"]}},
        title={"text": title, "font": {"size": 14, "color": theme["text_secondary"], "family": "Inter"}},
        gauge={
            "axis": {"range": [0, max_val], "tickwidth": 0, "tickcolor": "rgba(0,0,0,0)", "dtick": max_val},
            "bar": {"color": "#6c63ff", "thickness": 0.7},
            "bgcolor": "rgba(0,0,0,0)",
            "borderwidth": 0,
            "steps": steps,
            "threshold": {
                "line": {"color": "#a78bfa", "width": 2},
                "thickness": 0.8,
                "value": value,
            },
        },
    ))

    fig.update_layout(
        **_base_layout(margin={"l": 30, "r": 30, "t": 60, "b": 10}),
        height=height,
    )
    return fig


# ─────────────────────────────────────────────────────────────
# Radar / Spider Chart
# ─────────────────────────────────────────────────────────────

def create_radar(
    categories: list[str],
    values: list[float],
    title: str = "",
    fill_color: str = "rgba(108,99,255,0.2)",
    line_color: str = "#6c63ff",
    height: int = 350,
) -> go.Figure:
    """Create a radar/spider chart."""
    theme = _theme()
    # Close the polygon
    cats = list(categories) + [categories[0]]
    vals = list(values) + [values[0]]

    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=vals,
        theta=cats,
        fill="toself",
        fillcolor=fill_color,
        line={"color": line_color, "width": 2},
        marker={"size": 6, "color": line_color},
    ))

    fig.update_layout(
        **_base_layout(),
        height=height,
        title={"text": title, "font": {"size": 14}},
        polar={
            "bgcolor": "rgba(0,0,0,0)",
            "radialaxis": {
                "visible": True,
                "range": [0, 1],
                "tickvals": [0.25, 0.5, 0.75, 1.0],
                "ticktext": ["25%", "50%", "75%", "100%"],
                "gridcolor": _grid_color(0.08),
                "linecolor": "rgba(0,0,0,0)",
                "tickfont": {"color": theme["text_muted"], "size": 10},
            },
            "angularaxis": {
                "gridcolor": _grid_color(0.08),
                "linecolor": "rgba(0,0,0,0)",
                "tickfont": {"color": theme["text_secondary"], "size": 11},
            },
        },
    )
    return fig


# ─────────────────────────────────────────────────────────────
# Donut / Pie
# ─────────────────────────────────────────────────────────────

def create_donut(
    labels: list[str],
    values: list[float],
    colors: Optional[list[str]] = None,
    title: str = "",
    height: int = 300,
    hole: float = 0.65,
) -> go.Figure:
    """Create a donut chart."""
    theme = _theme()
    if colors is None:
        colors = CHART_COLORS[:len(labels)]

    fig = go.Figure(go.Pie(
        labels=labels,
        values=values,
        hole=hole,
        marker={"colors": colors, "line": {"width": 0}},
        textinfo="label+percent",
        textposition="outside",
        textfont={"size": 11, "color": theme["text_secondary"], "family": "Inter"},
        hoverinfo="label+value+percent",
        pull=[0.02] * len(labels),
    ))

    fig.update_layout(
        **_base_layout(showlegend=False),
        height=height,
        title={"text": title, "font": {"size": 14}},
    )
    return fig


# ─────────────────────────────────────────────────────────────
# Bar Charts
# ─────────────────────────────────────────────────────────────

def create_bar(
    x: list,
    y: list[float],
    colors: Optional[list[str]] = None,
    title: str = "",
    height: int = 300,
    orientation: str = "v",
    x_title: str = "",
    y_title: str = "",
) -> go.Figure:
    """Create a vertical or horizontal bar chart."""
    theme = _theme()
    if colors is None:
        colors = CHART_COLORS[:len(x)]

    bar_kwargs = {
        "marker": {"color": colors, "cornerradius": 6},
        "textfont": {"size": 11, "family": "Inter"},
    }

    if orientation == "h":
        bar_kwargs["x"] = y
        bar_kwargs["y"] = x
        bar_kwargs["orientation"] = "h"
        bar_kwargs["text"] = [f"{v:.1f}" if isinstance(v, float) else str(v) for v in y]
        bar_kwargs["textposition"] = "outside"
    else:
        bar_kwargs["x"] = x
        bar_kwargs["y"] = y
        bar_kwargs["text"] = [f"{v:.1f}" if isinstance(v, float) else str(v) for v in y]
        bar_kwargs["textposition"] = "outside"

    fig = go.Figure(go.Bar(**bar_kwargs))

    fig.update_layout(
        **_base_layout(),
        height=height,
        title={"text": title, "font": {"size": 14}},
        xaxis={"title": x_title, "gridcolor": _grid_color(0.06), "tickfont": {"color": theme["text_secondary"]}},
        yaxis={"title": y_title, "gridcolor": _grid_color(0.06), "tickfont": {"color": theme["text_secondary"]}},
    )
    return fig


def create_grouped_bar(
    x: list,
    datasets: list[dict],
    title: str = "",
    height: int = 350,
) -> go.Figure:
    """
    Create a grouped bar chart.

    datasets: list of {"name": str, "values": list[float], "color": str}
    """
    theme = _theme()
    fig = go.Figure()
    for ds in datasets:
        fig.add_trace(go.Bar(
            x=x,
            y=ds["values"],
            name=ds["name"],
            marker={"color": ds.get("color", CHART_COLORS[0]), "cornerradius": 4},
        ))

    fig.update_layout(
        **_base_layout(showlegend=True),
        height=height,
        title={"text": title, "font": {"size": 14}},
        barmode="group",
        legend={"font": {"color": theme["text_secondary"], "size": 11}},
        xaxis={"gridcolor": _grid_color(0.06), "tickfont": {"color": theme["text_secondary"]}},
        yaxis={"gridcolor": _grid_color(0.06), "tickfont": {"color": theme["text_secondary"]}},
    )
    return fig


# ─────────────────────────────────────────────────────────────
# Line / Area Chart
# ─────────────────────────────────────────────────────────────

def create_area_chart(
    x: list,
    y: list[float],
    title: str = "",
    color: str = "#6c63ff",
    height: int = 300,
    fill: bool = True,
    x_title: str = "",
    y_title: str = "",
) -> go.Figure:
    """Create an area or line chart."""
    theme = _theme()
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x,
        y=y,
        mode="lines+markers",
        fill="tozeroy" if fill else None,
        fillcolor=hex_to_rgba(color, alpha=0.08),
        line={"color": color, "width": 2.5, "shape": "spline"},
        marker={"size": 5, "color": color},
    ))

    fig.update_layout(
        **_base_layout(),
        height=height,
        title={"text": title, "font": {"size": 14}},
        xaxis={"title": x_title, "gridcolor": _grid_color(0.06), "tickfont": {"color": theme["text_secondary"]}},
        yaxis={"title": y_title, "gridcolor": _grid_color(0.06), "tickfont": {"color": theme["text_secondary"]}, "rangemode": "tozero"},
    )
    return fig


# ─────────────────────────────────────────────────────────────
# Heatmap
# ─────────────────────────────────────────────────────────────

def create_heatmap(
    z: list[list[float]],
    x_labels: list,
    y_labels: list,
    title: str = "",
    colorscale: Optional[str] = None,
    height: int = 350,
) -> go.Figure:
    """Create a heatmap chart."""
    theme = _theme()
    if colorscale is None:
        colorscale = [
            [0.0, "rgba(108,99,255,0.05)"],
            [0.25, "rgba(108,99,255,0.2)"],
            [0.5, "rgba(108,99,255,0.4)"],
            [0.75, "rgba(108,99,255,0.65)"],
            [1.0, "#6c63ff"],
        ]

    fig = go.Figure(go.Heatmap(
        z=z,
        x=x_labels,
        y=y_labels,
        colorscale=colorscale,
        showscale=True,
        colorbar={"thickness": 12, "outlinewidth": 0, "tickfont": {"color": theme["text_secondary"]}},
        hoverongaps=False,
        xgap=2,
        ygap=2,
    ))

    fig.update_layout(
        **_base_layout(),
        height=height,
        title={"text": title, "font": {"size": 14}},
        xaxis={"tickfont": {"color": theme["text_secondary"], "size": 10}, "side": "bottom"},
        yaxis={"tickfont": {"color": theme["text_secondary"], "size": 10}, "autorange": "reversed"},
    )
    return fig


# ─────────────────────────────────────────────────────────────
# Timeline (Gantt-style)
# ─────────────────────────────────────────────────────────────

def create_timeline(
    tasks: list[dict],
    title: str = "Today's Schedule",
    height: int = 400,
) -> go.Figure:
    """
    Create a Gantt-style timeline from task dicts.

    Each task needs: title, scheduled_start, scheduled_end, priority, status.
    """
    from config import PRIORITY_COLORS, STATUS_COLORS
    from datetime import datetime as dt

    theme = _theme()
    fig = go.Figure()

    if not tasks:
        fig.update_layout(**_base_layout(), height=200)
        return fig

    today_str = dt.now().strftime("%Y-%m-%d")

    for i, task in enumerate(reversed(tasks)):
        start = task.get("scheduled_start", "")
        end = task.get("scheduled_end", "")

        if not start or not end:
            continue

        try:
            start_dt = dt.strptime(f"{today_str} {start}", "%Y-%m-%d %H:%M")
            end_dt = dt.strptime(f"{today_str} {end}", "%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            continue

        priority = task.get("priority", 3)
        status = task.get("status", "pending")
        color = PRIORITY_COLORS.get(priority, "#6b7280")

        # Dim completed/failed tasks
        opacity = 0.4 if status in ("completed", "failed") else 1.0

        # Plotly's date-typed Bar traces accept the bar length as
        # milliseconds. Passing a raw datetime.timedelta works with
        # Plotly's own renderer, but orjson (used by Streamlit's
        # plotly_chart for serialization) cannot JSON-encode a
        # timedelta object directly, so we convert it explicitly.
        duration_ms = (end_dt - start_dt).total_seconds() * 1000

        fig.add_trace(go.Bar(
            x=[duration_ms],
            y=[task.get("title", "Task")],
            base=[start_dt],
            orientation="h",
            marker={
                "color": color,
                "opacity": opacity,
                "cornerradius": 6,
                "line": {"width": 0},
            },
            text=f"{start}–{end}",
            textposition="inside",
            textfont={"color": "white", "size": 10, "family": "Inter"},
            hovertemplate=(
                f"<b>{task.get('title', '')}</b><br>"
                f"Time: {start} – {end}<br>"
                f"Duration: {task.get('estimated_minutes', 0)} min<br>"
                f"Status: {status.title()}<br>"
                "<extra></extra>"
            ),
            showlegend=False,
        ))

    fig.update_layout(
        **_base_layout(),
        height=max(height, len(tasks) * 45 + 80),
        title={"text": title, "font": {"size": 14}},
        xaxis={
            "type": "date",
            "tickformat": "%H:%M",
            "gridcolor": _grid_color(0.06),
            "tickfont": {"color": theme["text_secondary"]},
        },
        yaxis={
            "tickfont": {"color": theme["text_secondary"], "size": 11},
            "autorange": "reversed",
        },
        barmode="overlay",
    )
    return fig


# ─────────────────────────────────────────────────────────────
# Treemap
# ─────────────────────────────────────────────────────────────

def create_treemap(
    labels: list[str],
    parents: list[str],
    values: list[float],
    title: str = "",
    colors: Optional[list[str]] = None,
    height: int = 350,
) -> go.Figure:
    """Create a treemap chart."""
    if colors is None:
        colors = CHART_COLORS[:len(labels)]

    fig = go.Figure(go.Treemap(
        labels=labels,
        parents=parents,
        values=values,
        marker={"colors": colors, "cornerradius": 8},
        textfont={"family": "Inter", "size": 13},
        textinfo="label+value",
    ))

    fig.update_layout(
        **_base_layout(margin={"l": 10, "r": 10, "t": 40, "b": 10}),
        height=height,
        title={"text": title, "font": {"size": 14}},
    )
    return fig


# ─────────────────────────────────────────────────────────────
# Waterfall
# ─────────────────────────────────────────────────────────────

def create_waterfall(
    x: list[str],
    y: list[float],
    title: str = "",
    height: int = 300,
) -> go.Figure:
    """Create a waterfall chart."""
    theme = _theme()
    measures = ["relative"] * (len(y) - 1) + ["total"]
    fig = go.Figure(go.Waterfall(
        x=x,
        y=y,
        measure=measures,
        connector={"line": {"color": _grid_color(0.15)}},
        increasing={"marker": {"color": "#10b981", "cornerradius": 4}},
        decreasing={"marker": {"color": "#ef4444", "cornerradius": 4}},
        totals={"marker": {"color": "#6c63ff", "cornerradius": 4}},
        textposition="outside",
        textfont={"color": theme["text_secondary"], "size": 11, "family": "Inter"},
    ))

    fig.update_layout(
        **_base_layout(),
        height=height,
        title={"text": title, "font": {"size": 14}},
        xaxis={"tickfont": {"color": theme["text_secondary"]}},
        yaxis={"gridcolor": _grid_color(0.06), "tickfont": {"color": theme["text_secondary"]}},
    )
    return fig