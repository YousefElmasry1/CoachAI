"""
CoachAI – Insight/Pattern/Correlation Text Templates
=====================================================

Renders the human-readable `observation`/`evidence`/`description` text
for PatternEntry, InsightEntry, and CorrelationEntry from raw numeric
data — in English or Arabic.

Why this exists:
    PatternCalculator, InsightCalculator, and CorrelationCalculator in
    analytics.py are pure, deterministic Python (no LLM). Previously
    they built their observation/evidence text directly as hardcoded
    English f-strings, which meant this text could never be Arabic no
    matter what any LLM prompt said elsewhere in the app — the LLM
    prompts never touch this text, it is pre-built and simply consumed
    (in the recommendation LLM context, the chatbot's tool results,
    and any future API response).

    This module fixes that at the source: calculators now build a
    small `data` dict of raw numbers/strings and call `render_*()`
    here to produce the final sentence in whichever language is
    requested. Adding a third language later means adding one more
    "xx" key per template below — no calculator logic changes.

Usage:
    observation, evidence = render_pattern("failure_time_cluster", data, "ar")
    observation, evidence = render_insight("completion_rate_excellent", data, "en")
    description = render_correlation("priority_vs_completion", data, "ar")
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Failure-reason value translation
#
# failure_reason values stored in the database (schema.sql CHECK
# constraint) are fixed English strings used as internal codes, e.g.
# 'Distracted', 'Ran out of time'. When a template needs to display one
# of these to the user, translate it here rather than duplicating the
# translation logic at every call site — this is the single place
# where enum values become display text.
# ---------------------------------------------------------------------------

REASON_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "Harder than expected": "Harder than expected",
        "Distracted": "Distracted",
        "Tired": "Tired",
        "Unexpected event": "Unexpected event",
        "Changed priorities": "Changed priorities",
        "Ran out of time": "Ran out of time",
    },
    "ar": {
        "Harder than expected": "أصعب من المتوقع",
        "Distracted": "تشتت الانتباه",
        "Tired": "إرهاق",
        "Unexpected event": "حدث غير متوقع",
        "Changed priorities": "تغيّرت الأولويات",
        "Ran out of time": "انتهى الوقت",
    },
}

DIRECTION_LABELS: dict[str, dict[str, str]] = {
    "en": {"underestimates": "underestimates", "overestimates": "overestimates"},
    "ar": {"underestimates": "يقلل من تقدير", "overestimates": "يبالغ في تقدير"},
}


def translate_reason(reason: str, language: str = "en") -> str:
    """Translate a stored failure_reason enum value for display."""
    return REASON_LABELS.get(language, REASON_LABELS["en"]).get(reason, reason)


def translate_direction(direction: str, language: str = "en") -> str:
    """Translate the planning-bias direction word for display."""
    return DIRECTION_LABELS.get(language, DIRECTION_LABELS["en"]).get(direction, direction)


# ---------------------------------------------------------------------------
# Pattern templates (PatternCalculator — 6 detectors)
# ---------------------------------------------------------------------------

PATTERN_TEMPLATES: dict[str, dict[str, dict[str, str]]] = {
    "failure_time_cluster": {
        "en": {
            "observation": (
                "Failures cluster around hour {hour}:00 with a "
                "{failure_rate_at_hour:.0%} failure rate vs "
                "{overall_failure_rate:.0%} overall."
            ),
            "evidence": (
                "{worst_count} failures out of {total_at_hour} tasks "
                "at hour {hour}:00."
            ),
        },
        "ar": {
            "observation": (
                "الفشل بيتركز حوالين الساعة {hour}:00 بنسبة فشل "
                "{failure_rate_at_hour:.0%} مقابل {overall_failure_rate:.0%} "
                "إجمالاً."
            ),
            "evidence": (
                "{worst_count} حالة فشل من أصل {total_at_hour} مهمة في "
                "الساعة {hour}:00."
            ),
        },
    },
    "category_failure_pattern": {
        "en": {
            "observation": (
                "Category '{category}' has a {category_failure_rate:.0%} "
                "failure rate, significantly above "
                "{overall_failure_rate:.0%} overall."
            ),
            "evidence": "{fail_count} failures in {total_tasks} '{category}' tasks.",
        },
        "ar": {
            "observation": (
                "فئة '{category}' نسبة فشلها {category_failure_rate:.0%}، "
                "أعلى بكتير من {overall_failure_rate:.0%} الإجمالية."
            ),
            "evidence": "{fail_count} حالة فشل من {total_tasks} مهمة في فئة '{category}'.",
        },
    },
    "dominant_failure_reason": {
        "en": {
            "observation": (
                "'{reason}' accounts for {ratio:.0%} of all failures "
                "({count} out of {total_failed})."
            ),
            "evidence": "{count}/{total_failed} failed tasks cite '{reason}'.",
        },
        "ar": {
            "observation": (
                "سبب '{reason}' مسؤول عن {ratio:.0%} من كل حالات الفشل "
                "({count} من أصل {total_failed})."
            ),
            "evidence": "{count} من {total_failed} مهمة فاشلة سببها '{reason}'.",
        },
    },
    "high_priority_struggle": {
        "en": {
            "observation": (
                "High-priority tasks (P1-P2) have a "
                "{hp_failure_rate:.0%} failure rate."
            ),
            "evidence": "{hp_fail} failures in {hp_total} high-priority tasks.",
        },
        "ar": {
            "observation": (
                "المهام عالية الأولوية (P1-P2) نسبة فشلها "
                "{hp_failure_rate:.0%}."
            ),
            "evidence": "{hp_fail} حالة فشل من {hp_total} مهمة عالية الأولوية.",
        },
    },
    "late_day_overload": {
        "en": {
            "observation": (
                "Tasks scheduled after 14:00 have a "
                "{afternoon_failure_rate:.0%} failure rate vs "
                "{overall_failure_rate:.0%} overall."
            ),
            "evidence": (
                "{pm_fail} failures in {afternoon_total} "
                "afternoon/evening tasks."
            ),
        },
        "ar": {
            "observation": (
                "المهام المجدولة بعد الساعة 2 الضهر نسبة فشلها "
                "{afternoon_failure_rate:.0%} مقابل "
                "{overall_failure_rate:.0%} إجمالاً."
            ),
            "evidence": "{pm_fail} حالة فشل من {afternoon_total} مهمة بعد الضهر.",
        },
    },
    "duration_sweet_spot": {
        "en": {
            "observation": (
                "Tasks in the {bucket} min range have a "
                "{bucket_completion_rate:.0%} completion rate, above "
                "the {overall_completion_rate:.0%} overall."
            ),
            "evidence": "{bucket_total} tasks in {bucket} min bucket.",
        },
        "ar": {
            "observation": (
                "المهام في مدى {bucket} دقيقة نسبة إنجازها "
                "{bucket_completion_rate:.0%}، أعلى من "
                "{overall_completion_rate:.0%} الإجمالية."
            ),
            "evidence": "{bucket_total} مهمة في مدى {bucket} دقيقة.",
        },
    },
}


# ---------------------------------------------------------------------------
# Insight templates (InsightCalculator — 5 detectors, 6 type codes)
# ---------------------------------------------------------------------------

INSIGHT_TEMPLATES: dict[str, dict[str, dict[str, str]]] = {
    "completion_rate_excellent": {
        "en": {
            "observation": (
                "Completion rate of {completion_rate:.0%} is excellent, "
                "indicating strong follow-through."
            ),
            "evidence": "{total_completed}/{total_tasks} completed.",
        },
        "ar": {
            "observation": (
                "نسبة الإنجاز {completion_rate:.0%} ممتازة، وده بيدل على "
                "التزام قوي بتنفيذ المهام."
            ),
            "evidence": "{total_completed} من {total_tasks} مهمة مكتملة.",
        },
    },
    "completion_rate_low": {
        "en": {
            "observation": (
                "Completion rate of {completion_rate:.0%} is below 50%, "
                "suggesting task load may exceed capacity."
            ),
            "evidence": "{total_completed}/{total_tasks} completed.",
        },
        "ar": {
            "observation": (
                "نسبة الإنجاز {completion_rate:.0%} أقل من 50%، وده بيدل "
                "على إن حمل المهام ممكن يكون أكتر من الطاقة المتاحة."
            ),
            "evidence": "{total_completed} من {total_tasks} مهمة مكتملة.",
        },
    },
    "planning_bias": {
        "en": {
            "observation": (
                "Planning consistently {direction} task duration by "
                "{planning_error:.0%}, which affects schedule reliability."
            ),
            "evidence": (
                "Average planning error of {avg_planning_error:.2f} "
                "across {tasks_with_actual} measured tasks."
            ),
        },
        "ar": {
            "observation": (
                "التخطيط بشكل مستمر {direction} مدة المهام بنسبة "
                "{planning_error:.0%}، وده بيأثر على دقة الجدول."
            ),
            "evidence": (
                "متوسط خطأ التخطيط {avg_planning_error:.2f} عبر "
                "{tasks_with_actual} مهمة تم قياسها."
            ),
        },
    },
    "consistency_strong": {
        "en": {
            "observation": (
                "Active on {consistency:.0%} of observed days, "
                "indicating strong habit formation."
            ),
            "evidence": "{active_days}/{observation_days} active days.",
        },
        "ar": {
            "observation": (
                "نشط في {consistency:.0%} من الأيام اللي تم رصدها، وده "
                "بيدل على تكوّن عادة قوية ومستمرة."
            ),
            "evidence": "{active_days} من {observation_days} يوم نشط.",
        },
    },
    "category_diversity": {
        "en": {
            "observation": (
                "Active across {num_categories} categories, indicating "
                "well-rounded engagement."
            ),
            "evidence": "Categories: {categories_list}.",
        },
        "ar": {
            "observation": (
                "نشط في {num_categories} فئات مختلفة، وده بيدل على "
                "تنوع وتوازن في الاهتمامات."
            ),
            "evidence": "الفئات: {categories_list}.",
        },
    },
    "failure_concentration": {
        "en": {
            "observation": (
                "'{top_reason}' dominates failures at {concentration:.0%} "
                "concentration, making it the primary target for "
                "improvement."
            ),
            "evidence": "{top_count}/{total_reasons} failures cite '{top_reason}'.",
        },
        "ar": {
            "observation": (
                "سبب '{top_reason}' هو المسيطر على حالات الفشل بنسبة "
                "{concentration:.0%}، وده أهم نقطة تستاهل تتحسن."
            ),
            "evidence": "{top_count} من {total_reasons} حالة فشل سببها '{top_reason}'.",
        },
    },
}


# ---------------------------------------------------------------------------
# Correlation templates (CorrelationCalculator — 6 detectors, incl. the
# "insufficient data" fallback each one can emit)
# ---------------------------------------------------------------------------

CORRELATION_TEMPLATES: dict[str, dict[str, dict[str, str]]] = {
    "priority_vs_completion": {
        "en": {
            "description": (
                "Positive = higher-priority tasks have better completion. "
                "P{top_priority} rate: {top_rate:.2f}, "
                "P{bottom_priority} rate: {bottom_rate:.2f}."
            ),
        },
        "ar": {
            "description": (
                "قيمة موجبة = المهام عالية الأولوية بتتنجز أكتر. "
                "نسبة الأولوية {top_priority}: {top_rate:.2f}، "
                "نسبة الأولوية {bottom_priority}: {bottom_rate:.2f}."
            ),
        },
    },
    "priority_vs_completion_insufficient": {
        "en": {"description": "Insufficient priority diversity."},
        "ar": {"description": "تنوع غير كافٍ في مستويات الأولوية."},
    },
    "priority_vs_failure": {
        "en": {
            "description": (
                "Positive = higher-priority tasks fail more. "
                "P{top_priority} fail: {top_rate:.2f}, "
                "P{bottom_priority} fail: {bottom_rate:.2f}."
            ),
        },
        "ar": {
            "description": (
                "قيمة موجبة = المهام عالية الأولوية بتفشل أكتر. "
                "نسبة فشل الأولوية {top_priority}: {top_rate:.2f}، "
                "نسبة فشل الأولوية {bottom_priority}: {bottom_rate:.2f}."
            ),
        },
    },
    "priority_vs_failure_insufficient": {
        "en": {"description": "Insufficient priority diversity."},
        "ar": {"description": "تنوع غير كافٍ في مستويات الأولوية."},
    },
    "duration_vs_completion": {
        "en": {
            "description": (
                "Positive = shorter tasks complete more. Shortest bucket "
                "rate: {shortest_rate:.2f}, longest: {longest_rate:.2f}."
            ),
        },
        "ar": {
            "description": (
                "قيمة موجبة = المهام الأقصر بتتنجز أكتر. نسبة أقصر مدى: "
                "{shortest_rate:.2f}، أطول مدى: {longest_rate:.2f}."
            ),
        },
    },
    "duration_vs_completion_insufficient": {
        "en": {"description": "Insufficient duration diversity."},
        "ar": {"description": "تنوع غير كافٍ في مدة المهام."},
    },
    "duration_vs_failure": {
        "en": {
            "description": (
                "Positive = longer tasks fail more. Shortest bucket fail: "
                "{shortest_rate:.2f}, longest: {longest_rate:.2f}."
            ),
        },
        "ar": {
            "description": (
                "قيمة موجبة = المهام الأطول بتفشل أكتر. فشل أقصر مدى: "
                "{shortest_rate:.2f}، أطول مدى: {longest_rate:.2f}."
            ),
        },
    },
    "duration_vs_failure_insufficient": {
        "en": {"description": "Insufficient duration diversity."},
        "ar": {"description": "تنوع غير كافٍ في مدة المهام."},
    },
    "delay_vs_failure": {
        "en": {
            "description": (
                "Positive = delayed tasks fail more. Delayed fail: "
                "{delayed_fail:.2f}, non-delayed fail: {non_delayed_fail:.2f}."
            ),
        },
        "ar": {
            "description": (
                "قيمة موجبة = المهام المتأخرة بتفشل أكتر. فشل المتأخر: "
                "{delayed_fail:.2f}، فشل غير المتأخر: {non_delayed_fail:.2f}."
            ),
        },
    },
    "delay_vs_failure_insufficient": {
        "en": {"description": "No contrast between delayed and non-delayed tasks."},
        "ar": {"description": "مفيش فرق واضح بين المهام المتأخرة وغير المتأخرة."},
    },
    "category_vs_delay": {
        "en": {
            "description": (
                "Max delay spread across categories: '{max_delay_cat}' "
                "({max_delay_value:.1f} min) vs '{min_delay_cat}' "
                "({min_delay_value:.1f} min)."
            ),
        },
        "ar": {
            "description": (
                "أكبر فرق تأخير بين الفئات: '{max_delay_cat}' "
                "({max_delay_value:.1f} دقيقة) مقابل '{min_delay_cat}' "
                "({min_delay_value:.1f} دقيقة)."
            ),
        },
    },
    "category_vs_delay_insufficient": {
        "en": {"description": "Insufficient category diversity for delay comparison."},
        "ar": {"description": "تنوع غير كافٍ في الفئات لمقارنة التأخير."},
    },
}


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _resolve(template_set: dict, type_code: str, language: str) -> dict:
    entry = template_set.get(type_code, {})
    return entry.get(language) or entry.get("en", {})


def render_pattern(pattern_type: str, data: dict, language: str = "en") -> tuple[str, str]:
    """Render (observation, evidence) for a pattern type code."""
    tpl = _resolve(PATTERN_TEMPLATES, pattern_type, language)
    observation = tpl.get("observation", "").format(**data)
    evidence = tpl.get("evidence", "").format(**data)
    return observation, evidence


def render_insight(insight_type: str, data: dict, language: str = "en") -> tuple[str, str]:
    """Render (observation, evidence) for an insight type code."""
    tpl = _resolve(INSIGHT_TEMPLATES, insight_type, language)
    observation = tpl.get("observation", "").format(**data)
    evidence = tpl.get("evidence", "").format(**data)
    return observation, evidence


def render_correlation(correlation_type: str, data: dict, language: str = "en") -> str:
    """Render the description for a correlation type code."""
    tpl = _resolve(CORRELATION_TEMPLATES, correlation_type, language)
    return tpl.get("description", "").format(**data)