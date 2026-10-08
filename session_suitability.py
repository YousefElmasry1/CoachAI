"""Deterministic, shared session-suitability rules for CoachAI schedulers.

This module decides *how much work belongs in each session*.  It never chooses
dates or clock times; SmartScheduler and FuturePlanningService remain the only
placement engines.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Iterable

from config import MAX_SEGMENTS_PER_TASK, MIN_SEGMENT_MINUTES


# Ninety minutes is a neutral reference scale, not a maximum.  The square-root
# relationship makes preferred sessions grow with the task while growing more
# slowly than total task duration.
_NEUTRAL_REFERENCE_MINUTES = 90

_HIGH_FOCUS_TERMS = {
    "algorithm", "algorithms", "coding", "deep work", "machine learning",
    "math", "mathematics", "programming", "research", "study", "studying",
    "writing", "بحث", "برمجة", "تعلم آلي", "خوارزمية", "خوارزميات", "دراسة",
    "رياضيات", "كتابة", "مذاكرة",
}
_LEISURE_TERMS = {
    "casual", "entertainment", "film", "game", "gaming", "movie", "play",
    "television", "watch", "watching", "ألعاب", "ترفيه", "فيلم", "لعب",
    "مشاهدة",
}
_PRACTICAL_TERMS = {
    "admin", "administration", "appointment", "call", "email", "errand",
    "quick", "إداري", "بريد", "سريع", "مكالمة", "مشوار", "موعد",
}


@dataclass(frozen=True)
class SessionProfile:
    """Explainable inputs and result of deterministic session inference."""

    preferred_minutes: int
    nature: str
    urgency: float


def _contains_term(text: str, terms: Iterable[str]) -> bool:
    normalized = f" {text.casefold()} "
    return any(term.casefold() in normalized for term in terms)


def _rounded_to_segment_unit(value: float) -> int:
    unit = MIN_SEGMENT_MINUTES
    return max(unit, int((value + unit / 2) // unit) * unit)


def build_session_profile(
    total_minutes: int,
    *,
    title: str = "",
    category: str = "",
    description: str = "",
    urgency: float = 0.0,
) -> SessionProfile:
    """Infer a preferred session length without imposing a hard maximum.

    The neutral preference is ``sqrt(total * 90)``.  Semantic information can
    shorten or lengthen that preference, while explicit deadline pressure can
    scale it up to twice its relaxed value.  Unknown/non-English tasks still receive
    the duration-derived neutral fallback.
    """
    total = int(total_minutes)
    if total <= 0:
        raise ValueError("total_minutes must be positive.")
    pressure = min(1.0, max(0.0, float(urgency)))
    text = " ".join(str(value or "") for value in (title, category, description))
    if _contains_term(text, _HIGH_FOCUS_TERMS):
        nature, semantic_factor = "high_focus", 0.82
    elif _contains_term(text, _LEISURE_TERMS):
        nature, semantic_factor = "leisure", 1.28
    elif _contains_term(text, _PRACTICAL_TERMS):
        nature, semantic_factor = "practical", 1.15
    else:
        nature, semantic_factor = "generic", 1.0

    duration_preference = sqrt(total * _NEUTRAL_REFERENCE_MINUTES)
    urgency_factor = 1.0 + pressure
    preferred = min(total, _rounded_to_segment_unit(
        duration_preference * semantic_factor * urgency_factor
    ))
    return SessionProfile(preferred, nature, pressure)


def balanced_session_lengths(total_minutes: int, count: int) -> tuple[int, ...]:
    """Return exact, deterministic, near-equal legal lengths for ``count``."""
    total, parts = int(total_minutes), int(count)
    if parts < 1 or parts > MAX_SEGMENTS_PER_TASK:
        raise ValueError("count must be within the configured segment limit.")
    if total < parts * MIN_SEGMENT_MINUTES:
        raise ValueError("total_minutes cannot form the requested legal sessions.")
    base, remainder = divmod(total, parts)
    return tuple(base + (1 if index < remainder else 0) for index in range(parts))


def fit_session_lengths(
    total_minutes: int, capacities: Iterable[int],
) -> tuple[int, ...] | None:
    """Balance exact work across differently sized candidate windows.

    This deterministic water-fill keeps every session legal, honours each
    window's capacity, and minimizes squared length differences for the fixed
    set of windows.  It is placement-agnostic and therefore shared policy, not
    a second allocator.
    """
    limits = tuple(int(value) for value in capacities)
    total = int(total_minutes)
    if (
        not limits or len(limits) > MAX_SEGMENTS_PER_TASK
        or any(value < MIN_SEGMENT_MINUTES for value in limits)
        or total < len(limits) * MIN_SEGMENT_MINUTES
        or sum(limits) < total
    ):
        return None
    low, high = MIN_SEGMENT_MINUTES, max(limits)
    while low < high:
        level = (low + high + 1) // 2
        if sum(min(limit, level) for limit in limits) <= total:
            low = level
        else:
            high = level - 1
    amounts = [min(limit, low) for limit in limits]
    left = total - sum(amounts)
    for index, limit in enumerate(limits):
        if not left:
            break
        addition = min(left, limit - amounts[index])
        amounts[index] += addition
        left -= addition
    return tuple(amounts) if left == 0 else None


def session_quality(lengths: Iterable[int], profile: SessionProfile) -> int:
    """Lower is better: suitability first, with a principled split overhead.

    Squared deviation strongly penalizes a very long uninterrupted session.
    Each additional session costs ``preferred * MIN_SEGMENT_MINUTES`` so small
    improvements cannot fragment a task unnecessarily.  Both terms derive from
    the inferred preference and the existing minimum segment invariant.
    """
    values = tuple(int(value) for value in lengths)
    if not values or any(value < MIN_SEGMENT_MINUTES for value in values):
        raise ValueError("Session lengths must respect MIN_SEGMENT_MINUTES.")
    deviation = sum((value - profile.preferred_minutes) ** 2 for value in values)
    split_overhead = (len(values) - 1) * profile.preferred_minutes * MIN_SEGMENT_MINUTES
    return deviation + split_overhead


def best_session_lengths(
    total_minutes: int,
    profile: SessionProfile,
    *,
    max_segments: int = MAX_SEGMENTS_PER_TASK,
) -> tuple[int, ...]:
    """Choose the best unconstrained legal partition for a logical task."""
    legal_max = min(int(max_segments), MAX_SEGMENTS_PER_TASK,
                    int(total_minutes) // MIN_SEGMENT_MINUTES)
    if legal_max < 1:
        raise ValueError("Task duration is shorter than MIN_SEGMENT_MINUTES.")
    candidates = [balanced_session_lengths(total_minutes, count)
                  for count in range(1, legal_max + 1)]
    return min(candidates, key=lambda values: (
        session_quality(values, profile), len(values), values,
    ))


def is_reasonable_existing(lengths: Iterable[int], profile: SessionProfile) -> bool:
    """Keep a stable placement only when its session shape is already optimal."""
    values = tuple(int(value) for value in lengths)
    if not values or len(values) > MAX_SEGMENTS_PER_TASK:
        return False
    best = best_session_lengths(sum(values), profile)
    return session_quality(values, profile) == session_quality(best, profile)
