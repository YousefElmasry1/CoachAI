"""Validated public HTTP models for the implemented CoachAI API slice."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from domain_validation import MAX_DATABASE_INTEGER


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)
    correlation_id: str


class TaskResponse(BaseModel):
    task_id: int
    plan_id: int
    category_id: int | None = None
    title: str
    description: str | None = None
    priority: int
    estimated_minutes: int
    scheduled_start: str | None = None
    scheduled_end: str | None = None
    is_fixed_time: bool
    is_break: bool
    order_index: int
    status: str
    failure_reason: str | None = None
    actual_minutes: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    timer_accumulated_seconds: int
    timer_segment_started_at: datetime | None = None
    pause_count: int
    paused_at: datetime | None = None
    timer_total_paused_seconds: int
    timer_version: int
    version: int
    earliest_start: datetime | None = None
    deadline: datetime | None = None
    created_at: datetime
    updated_at: datetime
    sync_revision: int | None = None
    idempotent_replay: bool | None = None


class TaskListResponse(BaseModel):
    items: list[TaskResponse]
    limit: int
    offset: int
    next_offset: int | None


class TaskUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0, le=MAX_DATABASE_INTEGER)
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    priority: int | None = Field(default=None, ge=1, le=5)
    estimated_minutes: int | None = Field(default=None, ge=1, le=960)
    order_index: int | None = Field(default=None, ge=0, le=MAX_DATABASE_INTEGER)
    category_id: int | None = Field(default=None, ge=1, le=MAX_DATABASE_INTEGER)
    is_fixed_time: bool | None = None
    is_break: bool | None = None
    earliest_start: datetime | None = None
    deadline: datetime | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Task title cannot be blank.")
        return value

    @model_validator(mode="after")
    def require_change(self):
        if not (self.model_fields_set - {"expected_version"}):
            raise ValueError("At least one task field must be supplied.")
        return self

    def changes(self) -> dict:
        return self.model_dump(
            exclude={"expected_version"},
            exclude_unset=True,
        )
