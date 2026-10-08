-- ==========================================================
-- CoachAI – Graduation Project Database Schema
-- Core scheduling, analytics, Calendar, and notification foundation
-- ==========================================================

PRAGMA foreign_keys = ON;

-- ==========================================================
-- 1. USERS  (Firebase Auth handles identity — user_id is the Firebase uid)
-- ==========================================================
CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    password_hash TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================================
-- 2. CATEGORIES
-- ==========================================================
CREATE TABLE categories (
    category_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT '#3B82F6',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    UNIQUE(user_id, name)
);

-- ==========================================================
-- 3. PLANS (Daily Plan Container)
-- ==========================================================
CREATE TABLE plans (
    plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    plan_date DATE NOT NULL,
    raw_input TEXT NOT NULL,
    ai_summary TEXT,
    status TEXT NOT NULL DEFAULT 'active' 
        CHECK (status IN ('draft', 'active', 'completed')),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    schedule_revision INTEGER NOT NULL DEFAULT 0,
    
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    UNIQUE(user_id, plan_date)
);

-- ==========================================================
-- 4. TASKS (Plan Tasks + Completion Data)
-- ==========================================================
CREATE TABLE tasks (
    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    category_id INTEGER,
    title TEXT NOT NULL,
    description TEXT,
    priority INTEGER NOT NULL DEFAULT 3 
        CHECK (priority BETWEEN 1 AND 5),
    estimated_minutes INTEGER NOT NULL DEFAULT 30 
        CHECK (estimated_minutes >= 0),
    scheduled_start TIME,
    scheduled_end TIME,
    earliest_start DATETIME,
    deadline DATETIME,
    is_fixed_time INTEGER NOT NULL DEFAULT 0
        CHECK (is_fixed_time IN (0, 1)),
    is_break INTEGER NOT NULL DEFAULT 0
        CHECK (is_break IN (0, 1)),
    timer_accumulated_seconds INTEGER NOT NULL DEFAULT 0,
    timer_segment_started_at DATETIME,
    pause_count INTEGER NOT NULL DEFAULT 0,
    paused_at DATETIME,
    timer_total_paused_seconds INTEGER NOT NULL DEFAULT 0,
    timer_version INTEGER NOT NULL DEFAULT 0,
    order_index INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending' 
        CHECK (status IN ('pending', 'in_progress', 'completed', 'failed')), 
    failure_reason TEXT
        CHECK (failure_reason IN (
            'Harder than expected', 
            'Distracted', 
            'Tired', 
            'Unexpected event', 
            'Changed priorities',
            'Ran out of time'
        )),
    actual_minutes INTEGER 
        CHECK (actual_minutes >= 0),
    started_at DATETIME,
    completed_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (plan_id) REFERENCES plans(plan_id) ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(category_id) ON DELETE SET NULL
);

-- Segments are the scheduling source of truth.  The task-level start/end
-- columns above remain a compatibility projection only.
CREATE TABLE task_segments (
    segment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    segment_index INTEGER NOT NULL CHECK (segment_index >= 0),
    scheduled_start TIME NOT NULL,
    scheduled_end TIME NOT NULL,
    scheduled_date DATE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    google_event_id TEXT,
    google_calendar_id TEXT,
    calendar_provenance TEXT NOT NULL DEFAULT 'legacy_unknown'
        CHECK (calendar_provenance IN ('imported','coachai_created','legacy_unknown')),
    calendar_sync_status TEXT NOT NULL DEFAULT 'not_synced',
    calendar_sync_error TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
    UNIQUE(task_id, segment_index),
    CHECK (scheduled_end > scheduled_start)
);

CREATE TABLE schedule_operations (
    operation_id TEXT PRIMARY KEY,
    plan_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    base_revision INTEGER NOT NULL,
    result_revision INTEGER NOT NULL,
    before_state TEXT NOT NULL,
    after_state TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'applied' CHECK (status IN ('applied','undone')),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    undone_at DATETIME,
    calendar_reconciled_at DATETIME,
    calendar_reconciliation_error TEXT,
    FOREIGN KEY (plan_id) REFERENCES plans(plan_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- Notification foundation. Provider delivery is intentionally outside the
-- scheduling transaction; the durable outbox is the hand-off boundary.
CREATE TABLE notification_preferences (
    user_id TEXT PRIMARY KEY,
    tone TEXT NOT NULL DEFAULT 'neutral'
        CHECK (tone IN ('neutral','friendly','coach')),
    default_reminder_lead_minutes INTEGER DEFAULT 10
        CHECK (default_reminder_lead_minutes IS NULL OR default_reminder_lead_minutes IN (0,5,10,15,30)),
    quiet_hours_start TIME,
    quiet_hours_end TIME,
    daily_summary_enabled INTEGER NOT NULL DEFAULT 0 CHECK (daily_summary_enabled IN (0,1)),
    coach_checkins_enabled INTEGER NOT NULL DEFAULT 0 CHECK (coach_checkins_enabled IN (0,1)),
    privacy_mode TEXT NOT NULL DEFAULT 'show_title'
        CHECK (privacy_mode IN ('show_title','hide_title','generic')),
    sound_enabled INTEGER NOT NULL DEFAULT 1 CHECK (sound_enabled IN (0,1)),
    vibration_enabled INTEGER NOT NULL DEFAULT 1 CHECK (vibration_enabled IN (0,1)),
    multi_device_policy TEXT NOT NULL DEFAULT 'primary'
        CHECK (multi_device_policy IN ('all','primary','selected')),
    locale TEXT NOT NULL DEFAULT 'en' CHECK (locale IN ('ar','en')),
    time_format TEXT NOT NULL DEFAULT '12h' CHECK (time_format IN ('12h','24h')),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE notification_type_preferences (
    user_id TEXT NOT NULL,
    notification_type TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    preferred_channel TEXT NOT NULL DEFAULT 'in_app'
        CHECK (preferred_channel IN ('in_app','local','push')),
    lead_minutes INTEGER
        CHECK (lead_minutes IS NULL OR lead_minutes IN (0,5,10,15,30)),
    cooldown_seconds INTEGER NOT NULL DEFAULT 0 CHECK (cooldown_seconds >= 0),
    interruption_level TEXT NOT NULL DEFAULT 'passive'
        CHECK (interruption_level IN ('passive','active','time_sensitive')),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    UNIQUE(user_id, notification_type)
);

CREATE TABLE notification_devices (
    device_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    platform TEXT NOT NULL CHECK (platform IN ('ios','android')),
    provider TEXT NOT NULL CHECK (provider IN ('apns','fcm','none')),
    push_token TEXT,
    app_identifier TEXT,
    provider_metadata TEXT NOT NULL DEFAULT '{}',
    app_version TEXT,
    locale TEXT NOT NULL DEFAULT 'en' CHECK (locale IN ('ar','en')),
    timezone TEXT NOT NULL DEFAULT 'UTC',
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0,1)),
    is_enabled INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0,1)),
    last_seen_at DATETIME,
    token_invalidated_at DATETIME,
    last_acknowledged_schedule_revision INTEGER NOT NULL DEFAULT 0
        CHECK (last_acknowledged_schedule_revision >= 0),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    UNIQUE(provider, push_token)
);

CREATE TABLE notification_outbox (
    sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    outbox_id TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    task_id INTEGER,
    segment_id INTEGER,
    scheduled_date DATE,
    segment_index INTEGER CHECK (segment_index IS NULL OR segment_index >= 0),
    schedule_revision INTEGER,
    schedule_operation_id TEXT,
    event_payload TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','processing','processed','failed','cancelled','expired')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    locked_at DATETIME,
    locked_by TEXT,
    expires_at DATETIME,
    processed_at DATETIME,
    last_error TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    UNIQUE(user_id, idempotency_key)
);

CREATE TABLE notifications (
    notification_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    notification_type TEXT NOT NULL,
    urgency TEXT NOT NULL CHECK (urgency IN ('critical','high','normal','low')),
    interruption_level TEXT NOT NULL
        CHECK (interruption_level IN ('passive','active','time_sensitive')),
    task_id INTEGER,
    segment_id INTEGER,
    scheduled_date DATE,
    segment_index INTEGER CHECK (segment_index IS NULL OR segment_index >= 0),
    schedule_revision INTEGER,
    schedule_operation_id TEXT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    locale TEXT NOT NULL CHECK (locale IN ('ar','en')),
    template_id TEXT NOT NULL,
    template_version INTEGER NOT NULL CHECK (template_version >= 1),
    scheduled_for DATETIME NOT NULL,
    expires_at DATETIME NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','suppressed','expired','cancelled')),
    dedupe_key TEXT NOT NULL,
    suppression_reason TEXT,
    action_metadata TEXT NOT NULL DEFAULT '{}',
    read_at DATETIME,
    acted_at DATETIME,
    sync_revision INTEGER NOT NULL DEFAULT 0 CHECK (sync_revision >= 0),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    UNIQUE(user_id, dedupe_key)
);

CREATE TABLE notification_deliveries (
    delivery_id TEXT PRIMARY KEY,
    notification_id TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('in_app','local','push')),
    device_id TEXT,
    target_key TEXT NOT NULL DEFAULT 'account',
    provider_message_id TEXT,
    provider TEXT,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','sent','delivered','failed','cancelled')),
    queued_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at DATETIME,
    delivered_at DATETIME,
    read_at DATETIME,
    acted_at DATETIME,
    cancelled_at DATETIME,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    next_attempt_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_attempt_at DATETIME,
    accepted_at DATETIME,
    locked_at DATETIME,
    locked_by TEXT,
    error_classification TEXT,
    last_error TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (notification_id) REFERENCES notifications(notification_id) ON DELETE CASCADE,
    FOREIGN KEY (device_id) REFERENCES notification_devices(device_id) ON DELETE SET NULL,
    UNIQUE(notification_id, channel, target_key)
);

CREATE TABLE notification_sync_state (
    user_id TEXT PRIMARY KEY,
    sync_revision INTEGER NOT NULL DEFAULT 0 CHECK (sync_revision >= 0),
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE notification_local_schedules (
    local_schedule_id TEXT PRIMARY KEY,
    notification_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    device_id TEXT,
    target_key TEXT NOT NULL DEFAULT 'account',
    task_id INTEGER,
    segment_id INTEGER,
    segment_index INTEGER CHECK (segment_index IS NULL OR segment_index >= 0),
    schedule_revision INTEGER,
    schedule_operation_id TEXT,
    trigger_type TEXT NOT NULL DEFAULT 'time',
    scheduled_fire_at DATETIME NOT NULL,
    expires_at DATETIME NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled','cancelled','expired','fired')),
    payload TEXT NOT NULL DEFAULT '{}',
    locale TEXT NOT NULL CHECK (locale IN ('ar','en')),
    timezone TEXT NOT NULL,
    cancelled_at DATETIME,
    cancellation_reason TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (notification_id) REFERENCES notifications(notification_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (device_id) REFERENCES notification_devices(device_id) ON DELETE SET NULL,
    UNIQUE(notification_id, target_key)
);

CREATE TABLE task_notification_state (
    user_id TEXT NOT NULL,
    task_id INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'SAFE'
        CHECK (state IN ('SAFE','AT_RISK','CRITICAL','PASSED')),
    last_evaluated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_transition_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
    UNIQUE(user_id, task_id)
);

-- ==========================================================
-- 5. USER PROFILES (Materialized Analytics Cache)
-- ==========================================================
CREATE TABLE user_profiles (
    profile_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL UNIQUE,
    completion_rate REAL NOT NULL DEFAULT 0.0 
        CHECK (completion_rate BETWEEN 0 AND 1),
    productivity_score REAL NOT NULL DEFAULT 0.0 
        CHECK (productivity_score BETWEEN 0 AND 100),
    best_productivity_hour INTEGER 
        CHECK (best_productivity_hour BETWEEN 0 AND 23),
    avg_delay_minutes REAL NOT NULL DEFAULT 0.0 
        CHECK (avg_delay_minutes >= 0),
    main_failure_reason TEXT,
    favorite_category_id INTEGER,
    current_streak INTEGER NOT NULL DEFAULT 0 
        CHECK (current_streak >= 0),
    longest_streak INTEGER NOT NULL DEFAULT 0 
        CHECK (longest_streak >= 0),
    total_completed INTEGER NOT NULL DEFAULT 0 
        CHECK (total_completed >= 0),
    total_failed INTEGER NOT NULL DEFAULT 0 
        CHECK (total_failed >= 0),
    total_tasks INTEGER NOT NULL DEFAULT 0 
        CHECK (total_tasks >= 0),
    last_updated DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (favorite_category_id) REFERENCES categories(category_id) ON DELETE SET NULL
);

-- ==========================================================
-- 6. BADGES (Achievement Definitions)
-- ==========================================================
CREATE TABLE badges (
    badge_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    icon TEXT,
    requirement_type TEXT NOT NULL 
        CHECK (requirement_type IN ('streak', 'count', 'rate')),
    requirement_value INTEGER NOT NULL 
        CHECK (requirement_value >= 0)
);

-- ==========================================================
-- 7. USER BADGES (Earned Achievements)
-- ==========================================================
CREATE TABLE user_badges (
    user_badge_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    badge_id INTEGER NOT NULL,
    earned_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (badge_id) REFERENCES badges(badge_id) ON DELETE CASCADE,
    UNIQUE(user_id, badge_id)
);

-- ==========================================================
-- 8. GOOGLE OAUTH TOKENS
-- ==========================================================
CREATE TABLE IF NOT EXISTS google_oauth_tokens (
    token_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL UNIQUE,
    access_token  TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    token_expiry  DATETIME NOT NULL,
    scopes        TEXT NOT NULL DEFAULT '',
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- ==========================================================
-- 9. GOOGLE SELECTED CALENDARS
-- ==========================================================
CREATE TABLE IF NOT EXISTS google_selected_calendars (
    selection_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    calendar_id   TEXT NOT NULL,
    calendar_name TEXT NOT NULL DEFAULT '',
    color         TEXT NOT NULL DEFAULT '#4285F4',
    is_primary    INTEGER NOT NULL DEFAULT 0,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    UNIQUE(user_id, calendar_id)
);

-- ==========================================================
-- 10. GOOGLE CALENDAR EVENTS (synced fixed-time blocks)
-- ==========================================================
CREATE TABLE IF NOT EXISTS google_calendar_events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    google_event_id TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    start_time      TIME NOT NULL,
    end_time        TIME NOT NULL,
    event_date      DATE NOT NULL,
    calendar_id     TEXT NOT NULL,
    last_synced_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    UNIQUE(user_id, google_event_id, event_date)
);

-- ==========================================================
-- INDEXES
-- ==========================================================

-- Users
CREATE INDEX idx_users_email ON users(email);

-- Categories
CREATE INDEX idx_categories_user_id ON categories(user_id);

-- Plans
CREATE INDEX idx_plans_user_id ON plans(user_id);
CREATE INDEX idx_plans_date ON plans(plan_date);
CREATE INDEX idx_plans_user_date ON plans(user_id, plan_date);

-- Tasks
CREATE INDEX idx_tasks_plan_id ON tasks(plan_id);
CREATE INDEX idx_tasks_category_id ON tasks(category_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_plan_status ON tasks(plan_id, status);
CREATE INDEX idx_task_segments_task_id ON task_segments(task_id);
CREATE INDEX idx_task_segments_date ON task_segments(scheduled_date);
CREATE INDEX idx_task_segments_google_event ON task_segments(google_event_id);
CREATE TRIGGER trg_task_segments_valid_time_insert
BEFORE INSERT ON task_segments
WHEN time(NEW.scheduled_start) IS NULL
  OR time(NEW.scheduled_end) IS NULL
  OR time(NEW.scheduled_start) >= time(NEW.scheduled_end)
  OR time(NEW.scheduled_end) > '23:59:59'
BEGIN
    SELECT RAISE(ABORT, 'invalid same-day task segment');
END;
CREATE TRIGGER trg_task_segments_valid_time_update
BEFORE UPDATE OF scheduled_start, scheduled_end ON task_segments
WHEN time(NEW.scheduled_start) IS NULL
  OR time(NEW.scheduled_end) IS NULL
  OR time(NEW.scheduled_start) >= time(NEW.scheduled_end)
  OR time(NEW.scheduled_end) > '23:59:59'
BEGIN
    SELECT RAISE(ABORT, 'invalid same-day task segment');
END;
CREATE INDEX idx_schedule_operations_plan ON schedule_operations(plan_id, created_at);
CREATE INDEX idx_schedule_operations_user ON schedule_operations(user_id);
CREATE INDEX idx_notification_type_preferences_user ON notification_type_preferences(user_id);
CREATE INDEX idx_notification_devices_user ON notification_devices(user_id);
CREATE INDEX idx_notification_devices_token ON notification_devices(push_token);
CREATE INDEX idx_notification_outbox_ready ON notification_outbox(status, available_at);
CREATE INDEX idx_notification_outbox_lease ON notification_outbox(status, locked_at);
CREATE INDEX idx_notification_outbox_user ON notification_outbox(user_id, created_at);
CREATE INDEX idx_notifications_user_created ON notifications(user_id, created_at);
CREATE INDEX idx_notifications_user_unread ON notifications(user_id, read_at);
CREATE INDEX idx_notifications_task ON notifications(user_id, task_id);
CREATE INDEX idx_notification_deliveries_notification ON notification_deliveries(notification_id);
CREATE INDEX idx_notification_deliveries_device ON notification_deliveries(device_id);
CREATE INDEX idx_notification_deliveries_push_ready ON notification_deliveries(channel, status, next_attempt_at);
CREATE INDEX idx_notification_deliveries_lease ON notification_deliveries(channel, locked_at);
CREATE INDEX idx_notifications_sync ON notifications(user_id, sync_revision);
CREATE INDEX idx_local_schedules_user_fire ON notification_local_schedules(user_id, status, scheduled_fire_at);
CREATE INDEX idx_local_schedules_task ON notification_local_schedules(user_id, task_id, status);
CREATE INDEX idx_task_notification_state_user ON task_notification_state(user_id);

-- User Profiles
CREATE INDEX idx_profiles_user_id ON user_profiles(user_id);

-- User Badges
CREATE INDEX idx_user_badges_user_id ON user_badges(user_id);

-- Google Calendar
CREATE INDEX IF NOT EXISTS idx_gcal_events_user_date ON google_calendar_events(user_id, event_date);
CREATE INDEX IF NOT EXISTS idx_gcal_selected_user ON google_selected_calendars(user_id);
