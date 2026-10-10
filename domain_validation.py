"""Shared deterministic input limits for human and model-produced data."""

from __future__ import annotations

from backend_errors import ValidationError


MAX_TASK_TITLE_CHARS = 200
MAX_TASK_DESCRIPTION_CHARS = 4000
MAX_CATEGORY_NAME_CHARS = 80
MAX_TASK_MINUTES = 16 * 60
MAX_AI_TASKS = 50
MAX_AUDIO_BYTES = 15 * 1024 * 1024
MAX_AI_INPUT_CHARS = 12_000
MAX_AI_OUTPUT_TOKENS = 8_192
MAX_PLANNING_NOTES_CHARS = 1_000
MAX_REVIEW_REASON_CHARS = 500
MAX_CALENDAR_CONTEXT_EVENTS = 500
MAX_TRANSCRIPT_CHARS = 12_000


def validate_ai_input(raw_input: str) -> str:
    """Validate untrusted text before it is included in an LLM prompt."""

    cleaned = str(raw_input or "").strip()
    if not cleaned:
        raise ValidationError("Planning input is required.")
    if len(cleaned) > MAX_AI_INPUT_CHARS:
        raise ValidationError(
            f"Planning input cannot exceed {MAX_AI_INPUT_CHARS} characters."
        )
    return cleaned


def validate_task_fields(
    *, title: str, description: str | None, estimated_minutes: int
) -> tuple[str, str, int]:
    clean_title = str(title or "").strip()
    clean_description = str(description or "").strip()
    minutes = int(estimated_minutes)
    if not clean_title:
        raise ValidationError("Task title is required.")
    if len(clean_title) > MAX_TASK_TITLE_CHARS:
        raise ValidationError(f"Task title cannot exceed {MAX_TASK_TITLE_CHARS} characters.")
    if len(clean_description) > MAX_TASK_DESCRIPTION_CHARS:
        raise ValidationError(
            f"Task description cannot exceed {MAX_TASK_DESCRIPTION_CHARS} characters."
        )
    if minutes < 1 or minutes > MAX_TASK_MINUTES:
        raise ValidationError(f"Task duration must be between 1 and {MAX_TASK_MINUTES} minutes.")
    return clean_title, clean_description, minutes
