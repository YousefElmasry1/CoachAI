"""Frozen SQLAlchemy snapshot for Alembic revision 20261008_0001.

This module is migration history, not the live application model. Do not edit it
when the application schema changes; create a new Alembic revision instead.
"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    Time,
    UniqueConstraint,
    func,
)

metadata = MetaData()

users = Table(
    "users", metadata,
    Column("user_id", String, primary_key=True),
    Column("email", String, nullable=False, unique=True),
    Column("display_name", String, nullable=False),
    Column("timezone", String, nullable=False, server_default="UTC"),
    Column("password_hash", Text),
    Column("is_disabled", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    CheckConstraint("is_disabled IN (0, 1)", name="ck_users_disabled"),
)
Index("idx_users_email", users.c.email)

categories = Table(
    "categories", metadata,
    Column("category_id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("name", String, nullable=False),
    Column("color", String, nullable=False, server_default="#3B82F6"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("user_id", "name"),
)
Index("idx_categories_user_id", categories.c.user_id)

plans = Table(
    "plans", metadata,
    Column("plan_id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("plan_date", Date, nullable=False),
    Column("raw_input", Text, nullable=False),
    Column("ai_summary", Text),
    Column("status", String, nullable=False, server_default="active"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("schedule_revision", Integer, nullable=False, server_default="0"),
    UniqueConstraint("user_id", "plan_date"),
    CheckConstraint("status IN ('draft','active','completed')", name="ck_plans_status"),
)
Index("idx_plans_user_id", plans.c.user_id)
Index("idx_plans_date", plans.c.plan_date)
Index("idx_plans_user_date", plans.c.user_id, plans.c.plan_date)

tasks = Table(
    "tasks", metadata,
    Column("task_id", Integer, primary_key=True, autoincrement=True),
    Column("plan_id", Integer, ForeignKey("plans.plan_id", ondelete="CASCADE"), nullable=False),
    Column("category_id", Integer, ForeignKey("categories.category_id", ondelete="SET NULL")),
    Column("title", String, nullable=False),
    Column("description", Text),
    Column("priority", Integer, nullable=False, server_default="3"),
    Column("estimated_minutes", Integer, nullable=False, server_default="30"),
    Column("scheduled_start", Time),
    Column("scheduled_end", Time),
    Column("is_fixed_time", Integer, nullable=False, server_default="0"),
    Column("is_break", Integer, nullable=False, server_default="0"),
    Column("order_index", Integer, nullable=False, server_default="0"),
    Column("status", String, nullable=False, server_default="pending"),
    Column("failure_reason", String),
    Column("actual_minutes", Integer),
    Column("started_at", DateTime),
    Column("completed_at", DateTime),
    Column("timer_accumulated_seconds", Integer, nullable=False, server_default="0"),
    Column("timer_segment_started_at", DateTime),
    Column("pause_count", Integer, nullable=False, server_default="0"),
    Column("paused_at", DateTime),
    Column("timer_total_paused_seconds", Integer, nullable=False, server_default="0"),
    Column("timer_version", Integer, nullable=False, server_default="0"),
    Column("version", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("google_event_id", String),
    Column("google_exported_at", DateTime),
    Column("earliest_start", DateTime),
    Column("deadline", DateTime),
    CheckConstraint("priority BETWEEN 1 AND 5", name="ck_tasks_priority"),
    CheckConstraint("estimated_minutes >= 0", name="ck_tasks_est_minutes"),
    CheckConstraint("status IN ('pending','in_progress','completed','failed')", name="ck_tasks_status"),
    CheckConstraint(
        "failure_reason IN ('Harder than expected','Distracted','Tired',"
        "'Unexpected event','Changed priorities','Ran out of time')",
        name="ck_tasks_failure_reason",
    ),
    CheckConstraint("actual_minutes >= 0", name="ck_tasks_actual_minutes"),
    CheckConstraint("is_fixed_time IN (0, 1)", name="ck_tasks_is_fixed_time"),
    CheckConstraint("is_break IN (0, 1)", name="ck_tasks_is_break"),
    CheckConstraint("version >= 0", name="ck_tasks_version"),
)
Index("idx_tasks_plan_id", tasks.c.plan_id)
Index("idx_tasks_category_id", tasks.c.category_id)
Index("idx_tasks_status", tasks.c.status)
Index("idx_tasks_plan_status", tasks.c.plan_id, tasks.c.status)

task_segments = Table(
    "task_segments", metadata,
    Column("segment_id", Integer, primary_key=True, autoincrement=True),
    Column("task_id", Integer, ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False),
    Column("segment_index", Integer, nullable=False),
    Column("scheduled_start", Time, nullable=False),
    Column("scheduled_end", Time, nullable=False),
    Column("scheduled_date", Date),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("google_event_id", String),
    Column("google_calendar_id", String),
    Column("calendar_provenance", String, nullable=False, server_default="legacy_unknown"),
    Column("calendar_sync_status", String, nullable=False, server_default="not_synced"),
    Column("calendar_sync_error", Text),
    UniqueConstraint("task_id", "segment_index"),
    CheckConstraint("segment_index >= 0", name="ck_task_segments_index"),
    CheckConstraint("scheduled_end > scheduled_start", name="ck_task_segments_same_day"),
    CheckConstraint(
        "calendar_provenance IN ('imported','coachai_created','legacy_unknown')",
        name="ck_task_segments_provenance",
    ),
)
Index("idx_task_segments_task_id", task_segments.c.task_id)
Index("idx_task_segments_date", task_segments.c.scheduled_date)
Index("idx_task_segments_google_event", task_segments.c.google_event_id)

schedule_operations = Table(
    "schedule_operations", metadata,
    Column("operation_id", String, primary_key=True),
    Column("plan_id", Integer, ForeignKey("plans.plan_id", ondelete="CASCADE"), nullable=False),
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("base_revision", Integer, nullable=False),
    Column("result_revision", Integer, nullable=False),
    Column("before_state", Text, nullable=False),
    Column("after_state", Text, nullable=False),
    Column("status", String, nullable=False, server_default="applied"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("undone_at", DateTime),
    Column("calendar_reconciled_at", DateTime),
    Column("calendar_reconciliation_error", Text),
    CheckConstraint("status IN ('applied','undone')", name="ck_schedule_operations_status"),
)
Index("idx_schedule_operations_plan", schedule_operations.c.plan_id, schedule_operations.c.created_at)
Index("idx_schedule_operations_user", schedule_operations.c.user_id)

# Notification foundation. These tables intentionally contain no provider-
# specific Firebase/APNs fields beyond an opaque device token and provider
# name. Business policy and delivery remain separate layers.
notification_preferences = Table(
    "notification_preferences", metadata,
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True),
    Column("tone", String, nullable=False, server_default="neutral"),
    Column("default_reminder_lead_minutes", Integer, server_default="10"),
    Column("quiet_hours_start", Time),
    Column("quiet_hours_end", Time),
    Column("daily_summary_enabled", Integer, nullable=False, server_default="0"),
    Column("coach_checkins_enabled", Integer, nullable=False, server_default="0"),
    Column("privacy_mode", String, nullable=False, server_default="show_title"),
    Column("sound_enabled", Integer, nullable=False, server_default="1"),
    Column("vibration_enabled", Integer, nullable=False, server_default="1"),
    Column("multi_device_policy", String, nullable=False, server_default="primary"),
    Column("locale", String, nullable=False, server_default="en"),
    Column("time_format", String, nullable=False, server_default="12h"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    CheckConstraint("tone IN ('neutral','friendly','coach')", name="ck_notification_preferences_tone"),
    CheckConstraint(
        "default_reminder_lead_minutes IS NULL OR default_reminder_lead_minutes IN (0,5,10,15,30)",
        name="ck_notification_preferences_lead",
    ),
    CheckConstraint("daily_summary_enabled IN (0,1)", name="ck_notification_preferences_daily"),
    CheckConstraint("coach_checkins_enabled IN (0,1)", name="ck_notification_preferences_checkins"),
    CheckConstraint("privacy_mode IN ('show_title','hide_title','generic')", name="ck_notification_preferences_privacy"),
    CheckConstraint("sound_enabled IN (0,1)", name="ck_notification_preferences_sound"),
    CheckConstraint("vibration_enabled IN (0,1)", name="ck_notification_preferences_vibration"),
    CheckConstraint("multi_device_policy IN ('all','primary','selected')", name="ck_notification_preferences_devices"),
    CheckConstraint("locale IN ('ar','en')", name="ck_notification_preferences_locale"),
    CheckConstraint("time_format IN ('12h','24h')", name="ck_notification_preferences_time_format"),
)

notification_type_preferences = Table(
    "notification_type_preferences", metadata,
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("notification_type", String, nullable=False),
    Column("enabled", Integer, nullable=False, server_default="1"),
    Column("preferred_channel", String, nullable=False, server_default="in_app"),
    Column("lead_minutes", Integer),
    Column("cooldown_seconds", Integer, nullable=False, server_default="0"),
    Column("interruption_level", String, nullable=False, server_default="passive"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("user_id", "notification_type"),
    CheckConstraint("enabled IN (0,1)", name="ck_notification_type_preferences_enabled"),
    CheckConstraint("preferred_channel IN ('in_app','local','push')", name="ck_notification_type_preferences_channel"),
    CheckConstraint("lead_minutes IS NULL OR lead_minutes IN (0,5,10,15,30)", name="ck_notification_type_preferences_lead"),
    CheckConstraint("cooldown_seconds >= 0", name="ck_notification_type_preferences_cooldown"),
    CheckConstraint("interruption_level IN ('passive','active','time_sensitive')", name="ck_notification_type_preferences_interruption"),
)
Index("idx_notification_type_preferences_user", notification_type_preferences.c.user_id)

notification_devices = Table(
    "notification_devices", metadata,
    Column("device_id", String, primary_key=True),
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("platform", String, nullable=False),
    Column("provider", String, nullable=False),
    Column("push_token", Text),
    Column("app_identifier", String),
    Column("provider_metadata", Text, nullable=False, server_default="{}"),
    Column("app_version", String),
    Column("locale", String, nullable=False, server_default="en"),
    Column("timezone", String, nullable=False, server_default="UTC"),
    Column("is_primary", Integer, nullable=False, server_default="0"),
    Column("is_enabled", Integer, nullable=False, server_default="1"),
    Column("last_seen_at", DateTime),
    Column("token_invalidated_at", DateTime),
    Column("last_acknowledged_schedule_revision", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("provider", "push_token"),
    CheckConstraint("platform IN ('ios','android')", name="ck_notification_devices_platform"),
    CheckConstraint("provider IN ('apns','fcm','none')", name="ck_notification_devices_provider"),
    CheckConstraint("locale IN ('ar','en')", name="ck_notification_devices_locale"),
    CheckConstraint("is_primary IN (0,1)", name="ck_notification_devices_primary"),
    CheckConstraint("is_enabled IN (0,1)", name="ck_notification_devices_enabled"),
    CheckConstraint("last_acknowledged_schedule_revision >= 0", name="ck_notification_devices_revision"),
)
Index("idx_notification_devices_user", notification_devices.c.user_id)
Index("idx_notification_devices_token", notification_devices.c.push_token)

notification_outbox = Table(
    "notification_outbox", metadata,
    Column("sequence_id", Integer, primary_key=True, autoincrement=True),
    Column("outbox_id", String, nullable=False, unique=True),
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("event_type", String, nullable=False),
    Column("task_id", Integer),
    Column("segment_id", Integer),
    Column("scheduled_date", Date),
    Column("segment_index", Integer),
    Column("schedule_revision", Integer),
    Column("schedule_operation_id", String),
    Column("event_payload", Text, nullable=False),
    Column("idempotency_key", String, nullable=False),
    Column("status", String, nullable=False, server_default="queued"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("available_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("locked_at", DateTime),
    Column("locked_by", String),
    Column("expires_at", DateTime),
    Column("processed_at", DateTime),
    Column("last_error", Text),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("user_id", "idempotency_key"),
    CheckConstraint("status IN ('queued','processing','processed','failed','cancelled','expired')", name="ck_notification_outbox_status"),
    CheckConstraint("attempts >= 0", name="ck_notification_outbox_attempts"),
    CheckConstraint("segment_index IS NULL OR segment_index >= 0", name="ck_notification_outbox_segment_index"),
)
Index("idx_notification_outbox_ready", notification_outbox.c.status, notification_outbox.c.available_at)
Index("idx_notification_outbox_lease", notification_outbox.c.status, notification_outbox.c.locked_at)
Index("idx_notification_outbox_user", notification_outbox.c.user_id, notification_outbox.c.created_at)

notifications = Table(
    "notifications", metadata,
    Column("notification_id", String, primary_key=True),
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("notification_type", String, nullable=False),
    Column("urgency", String, nullable=False),
    Column("interruption_level", String, nullable=False),
    Column("task_id", Integer),
    Column("segment_id", Integer),
    Column("scheduled_date", Date),
    Column("segment_index", Integer),
    Column("schedule_revision", Integer),
    Column("schedule_operation_id", String),
    Column("title", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("locale", String, nullable=False),
    Column("template_id", String, nullable=False),
    Column("template_version", Integer, nullable=False),
    Column("scheduled_for", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
    Column("status", String, nullable=False, server_default="active"),
    Column("dedupe_key", String, nullable=False),
    Column("suppression_reason", String),
    Column("action_metadata", Text, nullable=False, server_default="{}"),
    Column("read_at", DateTime),
    Column("acted_at", DateTime),
    Column("sync_revision", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("user_id", "dedupe_key"),
    CheckConstraint("urgency IN ('critical','high','normal','low')", name="ck_notifications_urgency"),
    CheckConstraint("interruption_level IN ('passive','active','time_sensitive')", name="ck_notifications_interruption"),
    CheckConstraint("locale IN ('ar','en')", name="ck_notifications_locale"),
    CheckConstraint("template_version >= 1", name="ck_notifications_template_version"),
    CheckConstraint("status IN ('active','suppressed','expired','cancelled')", name="ck_notifications_status"),
    CheckConstraint("segment_index IS NULL OR segment_index >= 0", name="ck_notifications_segment_index"),
    CheckConstraint("sync_revision >= 0", name="ck_notifications_sync_revision"),
)
Index("idx_notifications_user_created", notifications.c.user_id, notifications.c.created_at)
Index("idx_notifications_user_unread", notifications.c.user_id, notifications.c.read_at)
Index("idx_notifications_task", notifications.c.user_id, notifications.c.task_id)
Index("idx_notifications_sync", notifications.c.user_id, notifications.c.sync_revision)

notification_deliveries = Table(
    "notification_deliveries", metadata,
    Column("delivery_id", String, primary_key=True),
    Column("notification_id", String, ForeignKey("notifications.notification_id", ondelete="CASCADE"), nullable=False),
    Column("channel", String, nullable=False),
    Column("device_id", String, ForeignKey("notification_devices.device_id", ondelete="SET NULL")),
    Column("target_key", String, nullable=False, server_default="account"),
    Column("provider_message_id", String),
    Column("provider", String),
    Column("status", String, nullable=False, server_default="queued"),
    Column("queued_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("sent_at", DateTime),
    Column("delivered_at", DateTime),
    Column("read_at", DateTime),
    Column("acted_at", DateTime),
    Column("cancelled_at", DateTime),
    Column("retry_count", Integer, nullable=False, server_default="0"),
    Column("next_attempt_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("last_attempt_at", DateTime),
    Column("accepted_at", DateTime),
    Column("locked_at", DateTime),
    Column("locked_by", String),
    Column("error_classification", String),
    Column("last_error", Text),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("notification_id", "channel", "target_key"),
    CheckConstraint("channel IN ('in_app','local','push')", name="ck_notification_deliveries_channel"),
    CheckConstraint("status IN ('queued','sent','delivered','failed','cancelled')", name="ck_notification_deliveries_status"),
    CheckConstraint("retry_count >= 0", name="ck_notification_deliveries_retries"),
)
Index("idx_notification_deliveries_notification", notification_deliveries.c.notification_id)
Index("idx_notification_deliveries_device", notification_deliveries.c.device_id)
Index("idx_notification_deliveries_push_ready", notification_deliveries.c.channel, notification_deliveries.c.status, notification_deliveries.c.next_attempt_at)
Index("idx_notification_deliveries_lease", notification_deliveries.c.channel, notification_deliveries.c.locked_at)

notification_local_schedules = Table(
    "notification_local_schedules", metadata,
    Column("local_schedule_id", String, primary_key=True),
    Column("notification_id", String, ForeignKey("notifications.notification_id", ondelete="CASCADE"), nullable=False),
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("device_id", String, ForeignKey("notification_devices.device_id", ondelete="SET NULL")),
    Column("target_key", String, nullable=False, server_default="account"),
    Column("task_id", Integer),
    Column("segment_id", Integer),
    Column("segment_index", Integer),
    Column("schedule_revision", Integer),
    Column("schedule_operation_id", String),
    Column("trigger_type", String, nullable=False, server_default="time"),
    Column("scheduled_fire_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
    Column("status", String, nullable=False, server_default="scheduled"),
    Column("payload", Text, nullable=False, server_default="{}"),
    Column("locale", String, nullable=False),
    Column("timezone", String, nullable=False),
    Column("cancelled_at", DateTime),
    Column("cancellation_reason", String),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("notification_id", "target_key"),
    CheckConstraint("status IN ('scheduled','cancelled','expired','fired')", name="ck_local_schedule_status"),
    CheckConstraint("locale IN ('ar','en')", name="ck_local_schedule_locale"),
    CheckConstraint("segment_index IS NULL OR segment_index >= 0", name="ck_local_schedule_segment_index"),
)
Index("idx_local_schedules_user_fire", notification_local_schedules.c.user_id, notification_local_schedules.c.status, notification_local_schedules.c.scheduled_fire_at)
Index("idx_local_schedules_task", notification_local_schedules.c.user_id, notification_local_schedules.c.task_id, notification_local_schedules.c.status)

notification_sync_state = Table(
    "notification_sync_state", metadata,
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True),
    Column("sync_revision", Integer, nullable=False, server_default="0"),
    Column("updated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    CheckConstraint("sync_revision >= 0", name="ck_notification_sync_revision"),
)

task_notification_state = Table(
    "task_notification_state", metadata,
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("task_id", Integer, ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False),
    Column("state", String, nullable=False, server_default="SAFE"),
    Column("last_evaluated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("last_transition_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("user_id", "task_id"),
    CheckConstraint("state IN ('SAFE','AT_RISK','CRITICAL','PASSED')", name="ck_task_notification_state"),
)
Index("idx_task_notification_state_user", task_notification_state.c.user_id)

user_profiles = Table(
    "user_profiles", metadata,
    Column("profile_id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, unique=True),
    Column("completion_rate", Float, nullable=False, server_default="0.0"),
    Column("productivity_score", Float, nullable=False, server_default="0.0"),
    Column("best_productivity_hour", Integer),
    Column("avg_delay_minutes", Float, nullable=False, server_default="0.0"),
    Column("main_failure_reason", String),
    Column("favorite_category_id", Integer, ForeignKey("categories.category_id", ondelete="SET NULL")),
    Column("current_streak", Integer, nullable=False, server_default="0"),
    Column("longest_streak", Integer, nullable=False, server_default="0"),
    Column("total_completed", Integer, nullable=False, server_default="0"),
    Column("total_failed", Integer, nullable=False, server_default="0"),
    Column("total_tasks", Integer, nullable=False, server_default="0"),
    Column("last_updated", DateTime, nullable=False, server_default=func.current_timestamp()),
    CheckConstraint("completion_rate BETWEEN 0 AND 1", name="ck_profiles_completion_rate"),
    CheckConstraint("productivity_score BETWEEN 0 AND 100", name="ck_profiles_productivity_score"),
    CheckConstraint("best_productivity_hour BETWEEN 0 AND 23", name="ck_profiles_hour"),
    CheckConstraint("avg_delay_minutes >= 0", name="ck_profiles_avg_delay"),
    CheckConstraint("current_streak >= 0", name="ck_profiles_current_streak"),
    CheckConstraint("longest_streak >= 0", name="ck_profiles_longest_streak"),
    CheckConstraint("total_completed >= 0", name="ck_profiles_total_completed"),
    CheckConstraint("total_failed >= 0", name="ck_profiles_total_failed"),
    CheckConstraint("total_tasks >= 0", name="ck_profiles_total_tasks"),
)
Index("idx_profiles_user_id", user_profiles.c.user_id)

badges = Table(
    "badges", metadata,
    Column("badge_id", Integer, primary_key=True, autoincrement=True),
    Column("name", String, nullable=False, unique=True),
    Column("description", String, nullable=False),
    Column("icon", String),
    Column("requirement_type", String, nullable=False),
    Column("requirement_value", Integer, nullable=False),
    CheckConstraint("requirement_type IN ('streak','count','rate')", name="ck_badges_req_type"),
    CheckConstraint("requirement_value >= 0", name="ck_badges_req_value"),
)

user_badges = Table(
    "user_badges", metadata,
    Column("user_badge_id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("badge_id", Integer, ForeignKey("badges.badge_id", ondelete="CASCADE"), nullable=False),
    Column("earned_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("user_id", "badge_id"),
)
Index("idx_user_badges_user_id", user_badges.c.user_id)

google_oauth_tokens = Table(
    "google_oauth_tokens", metadata,
    Column("token_id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, unique=True),
    Column("access_token", Text, nullable=False),
    Column("refresh_token", Text, nullable=False),
    Column("token_expiry", DateTime, nullable=False),
    Column("scopes", String, nullable=False, server_default=""),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
)

google_selected_calendars = Table(
    "google_selected_calendars", metadata,
    Column("selection_id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("calendar_id", String, nullable=False),
    Column("calendar_name", String, nullable=False, server_default=""),
    Column("color", String, nullable=False, server_default="#4285F4"),
    Column("is_primary", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("user_id", "calendar_id"),
)
Index("idx_gcal_selected_user", google_selected_calendars.c.user_id)

google_calendar_events = Table(
    "google_calendar_events", metadata,
    Column("event_id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("google_event_id", String, nullable=False),
    Column("title", String, nullable=False, server_default=""),
    Column("start_time", Time, nullable=False),
    Column("end_time", Time, nullable=False),
    Column("event_date", Date, nullable=False),
    Column("calendar_id", String, nullable=False),
    Column("last_synced_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("user_id", "google_event_id", "event_date"),
)
Index("idx_gcal_events_user_date", google_calendar_events.c.user_id, google_calendar_events.c.event_date)

# Mobile-safe command replay and application-data synchronization.  These are
# deliberately separate from notification sync: application changes include
# plans/tasks/segments/settings/calendar state and deletion tombstones.
idempotency_records = Table(
    "idempotency_records", metadata,
    Column("record_id", String, primary_key=True),
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("operation", String, nullable=False),
    Column("idempotency_key", String, nullable=False),
    Column("request_hash", String, nullable=False),
    Column("response_json", Text),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("completed_at", DateTime),
    UniqueConstraint("user_id", "operation", "idempotency_key"),
)
Index("idx_idempotency_user_created", idempotency_records.c.user_id, idempotency_records.c.created_at)

application_sync_state = Table(
    "application_sync_state", metadata,
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True),
    Column("sync_revision", Integer, nullable=False, server_default="0"),
    Column("minimum_available_revision", Integer, nullable=False, server_default="0"),
    Column("updated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    CheckConstraint("sync_revision >= 0", name="ck_app_sync_revision"),
)

application_changes = Table(
    "application_changes", metadata,
    Column("sequence_id", Integer, primary_key=True, autoincrement=True),
    Column("change_id", String, nullable=False, unique=True),
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("sync_revision", Integer, nullable=False),
    Column("entity_type", String, nullable=False),
    Column("entity_id", String, nullable=False),
    Column("change_type", String, nullable=False),
    Column("entity_version", Integer),
    Column("payload", Text, nullable=False, server_default="{}"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("user_id", "sync_revision"),
    CheckConstraint("change_type IN ('upsert','delete')", name="ck_app_changes_type"),
)
Index("idx_app_changes_user_revision", application_changes.c.user_id, application_changes.c.sync_revision)

oauth_states = Table(
    "oauth_states", metadata,
    Column("state_hash", String, primary_key=True),
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("provider", String, nullable=False),
    Column("redirect_uri", Text, nullable=False),
    Column("code_verifier", Text),
    Column("expires_at", DateTime, nullable=False),
    Column("used_at", DateTime),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
)
Index("idx_oauth_states_user_expiry", oauth_states.c.user_id, oauth_states.c.expires_at)

calendar_sync_state = Table(
    "calendar_sync_state", metadata,
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("calendar_id", String, nullable=False),
    Column("range_start", Date, nullable=False),
    Column("range_end", Date, nullable=False),
    Column("sync_token", Text),
    Column("status", String, nullable=False, server_default="pending"),
    Column("last_error", Text),
    Column("last_success_at", DateTime),
    Column("updated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("user_id", "calendar_id"),
    CheckConstraint("status IN ('pending','running','fresh','error')", name="ck_calendar_sync_state_status"),
)

calendar_operations = Table(
    "calendar_operations", metadata,
    Column("operation_id", String, primary_key=True),
    Column("user_id", String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
    Column("operation_type", String, nullable=False),
    Column("idempotency_key", String, nullable=False),
    Column("payload", Text, nullable=False),
    Column("status", String, nullable=False, server_default="queued"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("available_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("locked_at", DateTime),
    Column("locked_by", String),
    Column("last_error", Text),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("completed_at", DateTime),
    UniqueConstraint("user_id", "operation_type", "idempotency_key"),
    CheckConstraint("status IN ('queued','processing','completed','failed','cancelled')", name="ck_calendar_operations_status"),
)
Index("idx_calendar_operations_ready", calendar_operations.c.status, calendar_operations.c.available_at)
