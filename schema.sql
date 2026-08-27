-- ==========================================================
-- CoachAI – Graduation Project Database Schema
-- 10 Tables | Clean | Professional | Future-Ready
-- ==========================================================

PRAGMA foreign_keys = ON;

-- ==========================================================
-- 1. USERS
-- ==========================================================
CREATE TABLE users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================================
-- 2. CATEGORIES
-- ==========================================================
CREATE TABLE categories (
    category_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
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
    user_id INTEGER NOT NULL,
    plan_date DATE NOT NULL,
    raw_input TEXT NOT NULL,
    ai_summary TEXT,
    status TEXT NOT NULL DEFAULT 'active' 
        CHECK (status IN ('draft', 'active', 'completed')),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
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
    is_fixed_time INTEGER NOT NULL DEFAULT 0
        CHECK (is_fixed_time IN (0, 1)),
    is_break INTEGER NOT NULL DEFAULT 0
        CHECK (is_break IN (0, 1)),
    timer_accumulated_seconds INTEGER NOT NULL DEFAULT 0,
    timer_segment_started_at DATETIME,
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

-- ==========================================================
-- 5. USER PROFILES (Materialized Analytics Cache)
-- ==========================================================
CREATE TABLE user_profiles (
    profile_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE,
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
    user_id INTEGER NOT NULL,
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
    user_id       INTEGER NOT NULL UNIQUE,
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
    user_id       INTEGER NOT NULL,
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
    user_id         INTEGER NOT NULL,
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

-- User Profiles
CREATE INDEX idx_profiles_user_id ON user_profiles(user_id);

-- User Badges
CREATE INDEX idx_user_badges_user_id ON user_badges(user_id);

-- Google Calendar
CREATE INDEX IF NOT EXISTS idx_gcal_events_user_date ON google_calendar_events(user_id, event_date);
CREATE INDEX IF NOT EXISTS idx_gcal_selected_user ON google_selected_calendars(user_id);