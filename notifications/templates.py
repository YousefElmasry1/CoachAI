"""Curated, versioned Arabic/English notification templates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from timezone_utils import safe_zoneinfo

from .domain import Tone


SUPPORTED_LOCALES = {"ar", "en"}


def normalize_locale(locale: str | None) -> str:
    value = str(locale or "en").replace("_", "-").lower()
    language = value.split("-", 1)[0]
    return language if language in SUPPORTED_LOCALES else "en"


def format_local_datetime(
    value: datetime,
    *,
    timezone_name: str,
    locale: str = "en",
    time_format: str = "12h",
) -> str:
    """Format a UTC instant using the user's existing IANA timezone.

    Notification templates can use this helper without introducing a second
    timezone model or scattering Arabic/English branches through services.
    Naive values are interpreted as UTC, matching persisted notification
    timestamps in this foundation.
    """
    if time_format not in {"12h", "24h"}:
        raise ValueError("Time format must be '12h' or '24h'.")
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    local = aware.astimezone(safe_zoneinfo(timezone_name))
    language = normalize_locale(locale)
    if time_format == "24h":
        rendered_time = local.strftime("%H:%M")
    else:
        rendered_time = local.strftime("%I:%M %p")
    if language == "en":
        return f"{local:%Y-%m-%d}, {rendered_time}"
    rendered_time = rendered_time.replace("AM", "ص").replace("PM", "م")
    rendered = f"{local:%Y/%m/%d}، {rendered_time}"
    return rendered.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))


@dataclass(frozen=True)
class RenderedTemplate:
    template_id: str
    template_version: int
    title: str
    body: str
    locale: str


class _SafeFormat(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class TemplateCatalog:
    def __init__(self, locale_dir: Path | None = None) -> None:
        self.locale_dir = locale_dir or Path(__file__).with_name("locales")
        self._cache: dict[str, dict[str, Any]] = {}

    def _load(self, locale: str) -> dict[str, Any]:
        normalized = normalize_locale(locale)
        if normalized not in self._cache:
            path = self.locale_dir / f"{normalized}.json"
            with path.open("r", encoding="utf-8") as handle:
                self._cache[normalized] = json.load(handle)
        return self._cache[normalized]

    def select(
        self,
        template_key: str,
        *,
        locale: str,
        tone: Tone | str,
        context: Mapping[str, Any],
        recent_template_ids: Iterable[str] = (),
        seed: str = "",
    ) -> RenderedTemplate:
        normalized_locale = normalize_locale(locale)
        normalized_tone = Tone(tone).value
        data = self._load(normalized_locale)
        group = data.get(template_key)
        if not isinstance(group, dict):
            raise KeyError(f"Unknown notification template key: {template_key}")
        candidates = group.get(normalized_tone) or group.get(Tone.NEUTRAL.value) or []
        if not candidates:
            raise KeyError(
                f"No templates for {template_key!r} in locale {normalized_locale!r}."
            )
        recent = set(recent_template_ids)
        unused = [item for item in candidates if item["id"] not in recent]
        pool = unused or candidates
        digest = hashlib.sha256(
            f"{normalized_locale}:{normalized_tone}:{template_key}:{seed}".encode("utf-8")
        ).digest()
        selected = pool[int.from_bytes(digest[:4], "big") % len(pool)]
        values = _SafeFormat({key: value for key, value in context.items()})
        return RenderedTemplate(
            template_id=str(selected["id"]),
            template_version=int(selected.get("version", 1)),
            title=str(selected["title"]).format_map(values),
            body=str(selected["body"]).format_map(values),
            locale=normalized_locale,
        )
