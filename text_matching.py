"""
CoachAI – Text Matching Helpers
================================

Shared normalization for matching/deduplicating user-typed text
(category names, keyword search, fixed-term detection like "Break")
across both English and Arabic.

Why this exists:
    `.lower()` alone is enough for English but does nothing useful for
    Arabic, which has no letter case. Two Arabic strings a user
    considers "the same" can still differ at the byte level:
    - Diacritics (tashkeel) may or may not be present: "مُذاكرة" vs "مذاكرة"
    - Alef variants: "أعمال" vs "اعمال" vs "إعمال" vs "آعمال"
    - Alef maksura vs yeh: "ذكرى" vs "ذكري"
    - Teh marbuta vs heh: "مذاكرة" vs "مذاكره"
    - Hamza carriers: "مؤتمر" vs "مواتمر", "بيئة" vs "بيئه"

    normalize_for_matching() collapses these variants down to a single
    canonical form so lookups/dedup/keyword-search treat them as
    equal. This is a MATCHING KEY ONLY — never display the normalized
    form to a user, only use it internally to compare or look up
    against other normalized strings.
"""

from __future__ import annotations

import re

_TASHKEEL_PATTERN = re.compile(r"[\u064B-\u065F\u0670\u06D6-\u06ED]")

_ARABIC_NORMALIZE_MAP: dict[int, str] = str.maketrans({
    "أ": "ا",
    "إ": "ا",
    "آ": "ا",
    "ٱ": "ا",
    "ى": "ي",
    "ة": "ه",
    "ؤ": "و",
    "ئ": "ي",
})


def normalize_for_matching(text: str) -> str:
    """
    Normalize text (English or Arabic) into a canonical matching key.

    Applies (in order): strip, lowercase (a no-op for Arabic, correct
    for English), tashkeel removal, and Arabic letter-variant folding.

    Args:
        text: Raw user-typed text (category name, task title, search
            keyword, etc.).

    Returns:
        A normalized string suitable for equality comparison, dict
        keys, or substring search — NOT for display.
    """
    text = (text or "").strip().lower()
    text = _TASHKEEL_PATTERN.sub("", text)
    text = text.translate(_ARABIC_NORMALIZE_MAP)
    return text


def detect_language(text: str) -> str:
    """
    Return 'ar' if the text contains any Arabic script, else 'en'.

    Used to pick a sensible default for text this backend generates
    itself (e.g. a default task title) so it matches the language the
    user is already writing in, rather than always defaulting to
    English.
    """
    return "ar" if re.search(r"[\u0600-\u06FF]", text or "") else "en"


# Fixed terms that must match regardless of the user's language. Each
# entry lists every accepted spelling; normalize_for_matching() is
# applied to both sides before comparison, so diacritics/alef variants
# in the user's own spelling are already handled — this set only needs
# one representative spelling per real variant (e.g. "راحة" vs
# "استراحة" are different words, not spelling variants, so both are
# listed).
BREAK_TERMS: frozenset[str] = frozenset(
    normalize_for_matching(t) for t in ["break", "استراحة", "راحة"]
)


def is_break_term(text: str) -> bool:
    """True if the (already-trimmed) text names a break, in any supported language."""
    return normalize_for_matching(text) in BREAK_TERMS