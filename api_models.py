"""Validated public HTTP models for the implemented CoachAI API slice."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from domain_validation import MAX_AI_INPUT_CHARS, MAX_DATABASE_INTEGER


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)
    correlation_id: str


class ProfileResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    timezone: str
    is_disabled: bool
    created_at: datetime
    updated_at: datetime
    sync_revision: int | None = None
    idempotent_replay: bool | None = None


class ProfileDisplayNameUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=100)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Display name cannot be blank.")
        return cleaned


class ProfileTimezoneUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = Field(min_length=1, max_length=100)


class ProfileTimezoneUpdateResponse(BaseModel):
    timezone: str
    local_schedules_cancelled: int = Field(ge=0)
    notification_reminders_queued: int = Field(ge=0)
    calendar_reconciliation_pending: bool
    calendar_operation_id: str
    sync_revision: int = Field(ge=0)
    idempotent_replay: bool | None = None


class NotificationPreferencesResponse(BaseModel):
    user_id: str
    tone: str
    default_reminder_lead_minutes: int | None
    quiet_hours_start: time | None
    quiet_hours_end: time | None
    daily_summary_enabled: bool
    coach_checkins_enabled: bool
    privacy_mode: str
    sound_enabled: bool
    vibration_enabled: bool
    multi_device_policy: str
    locale: str
    time_format: str
    timezone: str
    updated_at: datetime | None = None
    sync_revision: int | None = None
    idempotent_replay: bool | None = None


class NotificationPreferencesUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tone: Literal["neutral", "friendly", "coach"] | None = None
    default_reminder_lead_minutes: int | None = None
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    daily_summary_enabled: bool | None = None
    coach_checkins_enabled: bool | None = None
    privacy_mode: Literal["show_title", "hide_title", "generic"] | None = None
    sound_enabled: bool | None = None
    vibration_enabled: bool | None = None
    multi_device_policy: Literal["all", "primary", "selected"] | None = None
    locale: Literal["ar", "en"] | None = None
    time_format: Literal["12h", "24h"] | None = None

    @field_validator("default_reminder_lead_minutes")
    @classmethod
    def validate_lead(cls, value: int | None) -> int | None:
        if value not in {None, 0, 5, 10, 15, 30}:
            raise ValueError("Reminder lead must be 0, 5, 10, 15, or 30 minutes.")
        return value

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("At least one notification preference is required.")
        return self

    def changes(self) -> dict:
        return self.model_dump(exclude_unset=True)


class NotificationDeviceRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1, max_length=200)
    push_token: str = Field(min_length=1, max_length=4096)
    app_identifier: str = Field(min_length=1, max_length=200)
    app_version: str = Field(min_length=1, max_length=100)
    locale: Literal["ar", "en"] = "en"
    timezone: str = Field(min_length=1, max_length=100)


class NotificationDeviceResponse(BaseModel):
    device_id: str
    platform: Literal["android", "ios"]
    provider: Literal["fcm", "apns", "none"]
    app_identifier: str | None = None
    app_version: str | None = None
    locale: Literal["ar", "en"]
    timezone: str
    is_primary: bool
    is_enabled: bool
    last_acknowledged_schedule_revision: int = 0
    last_seen_at: datetime | None = None
    token_invalidated_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    idempotent_replay: bool | None = None


class NotificationDeviceInvalidationResponse(BaseModel):
    device_id: str
    invalidated: bool
    idempotent_replay: bool | None = None


class CalendarItemResponse(BaseModel):
    calendar_id: str
    name: str
    color: str = "#4285F4"
    is_primary: bool = False
    selected: bool = False


class CalendarConnectionResponse(BaseModel):
    provider: Literal["google"] = "google"
    connected: bool
    selected_calendars: list[CalendarItemResponse] = Field(default_factory=list)


class CalendarAuthorizationResponse(BaseModel):
    authorization_url: str
    expires_at: datetime


class CalendarAuthorizationCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=4096)
    state: str = Field(min_length=1, max_length=1024)


class CalendarSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    calendars: list[CalendarItemResponse] = Field(min_length=1, max_length=100)


class CalendarSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    range_start: date
    range_end: date


class TaskResponse(BaseModel):
    task_id: int
    plan_id: int
    category_id: int | None = None
    title: str
    description: str | None = None
    priority: int
    estimated_minutes: int
    scheduled_start: time | None = None
    scheduled_end: time | None = None
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


class HomeProgressResponse(BaseModel):
    completed: int = Field(ge=0)
    in_progress: int = Field(ge=0)
    pending: int = Field(ge=0)
    failed: int = Field(ge=0)
    total: int = Field(ge=0)


class TodayPlanResponse(BaseModel):
    plan_id: int
    plan_date: date
    status: str
    schedule_revision: int
    tasks: list[TaskResponse]
    tasks_truncated: bool
    progress: HomeProgressResponse


class HomeResponse(BaseModel):
    date: date
    display_name: str | None = None
    plan: TodayPlanResponse | None = None


class EnsureTodayPlanResponse(BaseModel):
    plan_id: int
    plan_date: date
    status: str
    schedule_revision: int = Field(ge=0)
    created: bool
    sync_revision: int = Field(ge=0)
    idempotent_replay: bool


class FuturePlanningCandidateResponse(BaseModel):
    task_id: int = Field(gt=0, le=MAX_DATABASE_INTEGER)
    plan_date: date
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    remaining_minutes: int = Field(ge=0, le=7 * 24 * 60)
    priority: int = Field(ge=1, le=5)
    earliest_start: datetime | None = None
    deadline: datetime | None = None


class FuturePlanningCandidatesResponse(BaseModel):
    planning_start: date
    planning_end: date
    tasks: list[FuturePlanningCandidateResponse] = Field(max_length=500)


class FuturePlanningSegmentResponse(BaseModel):
    scheduled_date: date
    scheduled_start: time
    scheduled_end: time
    segment_index: int = Field(ge=0)


class FuturePlanningOutcomeResponse(BaseModel):
    task_id: int | None = None
    title: str = Field(min_length=1, max_length=200)
    placed: bool
    reason: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1000)
    remaining_minutes: int = Field(ge=0, le=7 * 24 * 60)
    segments: list[FuturePlanningSegmentResponse] = Field(max_length=20)
    candidate_id: str | None = Field(default=None, max_length=200)
    is_draft: bool = False


class FuturePlanningChangeResponse(BaseModel):
    task_id: int | None = None
    title: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=100)
    old_segments: list[FuturePlanningSegmentResponse] = Field(max_length=20)
    new_segments: list[FuturePlanningSegmentResponse] = Field(max_length=20)
    explanation: str = Field(min_length=1, max_length=1000)
    candidate_id: str | None = Field(default=None, max_length=200)
    is_draft: bool = False


class FuturePlanningPreviewResponse(BaseModel):
    planning_start: date
    planning_end: date
    work_day_start: time
    outcomes: list[FuturePlanningOutcomeResponse] = Field(max_length=50)
    changes: list[FuturePlanningChangeResponse] = Field(max_length=50)
    placed_count: int = Field(ge=0, le=50)
    unplaced_count: int = Field(ge=0, le=50)


class FuturePlanningApplyCountsResponse(BaseModel):
    placed_count: int = Field(ge=0, le=50)
    unplaced_count: int = Field(ge=0, le=50)


class FuturePlanningApplyResponse(BaseModel):
    operation_id: str = Field(min_length=1, max_length=200)
    plan_ids: list[int] = Field(max_length=100)
    sync_revision: int = Field(ge=0)
    calendar_operation_id: str | None = Field(default=None, max_length=200)
    calendar_reconciliation_pending: bool
    preview: FuturePlanningApplyCountsResponse
    idempotent_replay: bool


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


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: int = Field(ge=1, le=MAX_DATABASE_INTEGER)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    estimated_minutes: int = Field(ge=1, le=960)
    priority: int = Field(default=3, ge=1, le=5)
    order_index: int = Field(default=0, ge=0, le=MAX_DATABASE_INTEGER)
    is_fixed_time: bool = False
    is_break: bool = False
    scheduled_start: str | None = Field(default=None, max_length=40)
    scheduled_end: str | None = Field(default=None, max_length=40)


class TaskCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0, le=MAX_DATABASE_INTEGER)
    failure_reason: str | None = Field(default=None, max_length=200)


class CoachRecommendationResponse(BaseModel):
    summary: str
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class CoachChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=MAX_AI_INPUT_CHARS)


class CoachChatResponse(BaseModel):
    reply: str


class VoiceTranscriptionResponse(BaseModel):
    transcript: str


class PlanningDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_input: str = Field(min_length=1, max_length=MAX_AI_INPUT_CHARS)


class PlanningApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_input: str = Field(min_length=1, max_length=MAX_AI_INPUT_CHARS)
    draft: dict[str, Any]


class SchedulePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: int = Field(ge=1, le=MAX_DATABASE_INTEGER)
    expected_revision: int = Field(ge=0, le=MAX_DATABASE_INTEGER)
    work_day_start: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class ScheduleMoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0, le=MAX_DATABASE_INTEGER)
    new_start: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
