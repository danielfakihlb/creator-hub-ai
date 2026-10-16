-- Creator Hub AI -- Postgres schema.
--
-- This used to be a SQLite schema (hence the comment that used to live
-- here about porting cleanly to Postgres "later" -- this is that). Column
-- types and constraints are kept as close to the original as possible so
-- nothing calling into database.py had to change.

CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    full_name       TEXT NOT NULL,
    is_admin        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
);

-- A user has at most one active subscription row at a time; history is kept
-- by inserting new rows rather than mutating, so upgrade/downgrade/cancel
-- are all auditable.
CREATE TABLE IF NOT EXISTS subscriptions (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES users(id),
    plan_slug           TEXT NOT NULL,              -- 'creator' | 'full_studio'
    status              TEXT NOT NULL,              -- 'active' | 'canceled' | 'expired' | 'payment_required'
    billing_period_start TEXT,
    billing_period_end   TEXT,
    payment_provider_ref TEXT,                       -- external subscription id once a provider is connected
    created_at          TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
    updated_at          TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions(user_id);

CREATE TABLE IF NOT EXISTS brand_profiles (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    name            TEXT NOT NULL,
    description     TEXT,
    tone_of_voice   TEXT,
    target_audience TEXT,
    primary_color   TEXT,
    secondary_color TEXT,
    font_family     TEXT,
    preferred_style TEXT,
    is_confirmed    INTEGER NOT NULL DEFAULT 1,   -- uncertain inferences must be 0 until approved
    created_at      TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
    updated_at      TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE TABLE IF NOT EXISTS projects (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    brand_profile_id INTEGER REFERENCES brand_profiles(id),
    name            TEXT NOT NULL,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'archived'
    created_at      TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
    updated_at      TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id);

CREATE TABLE IF NOT EXISTS media_assets (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    project_id      INTEGER REFERENCES projects(id),
    folder          TEXT NOT NULL DEFAULT 'Uploads',
    filename        TEXT NOT NULL,
    media_type      TEXT NOT NULL,          -- 'video' | 'image' | 'audio' | 'brand_asset'
    description     TEXT,
    tags            TEXT,                    -- comma-separated for simple search
    storage_path    TEXT,                    -- null until real object storage is connected
    created_at      TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE INDEX IF NOT EXISTS idx_media_user ON media_assets(user_id);
CREATE INDEX IF NOT EXISTS idx_media_project ON media_assets(project_id);

CREATE TABLE IF NOT EXISTS campaigns (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    project_id      INTEGER REFERENCES projects(id),
    name            TEXT NOT NULL,
    objective       TEXT,
    status          TEXT NOT NULL DEFAULT 'planning', -- 'planning' | 'in_progress' | 'awaiting_approval' | 'completed'
    start_date      TEXT,
    end_date        TEXT,
    created_at      TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE INDEX IF NOT EXISTS idx_campaigns_user ON campaigns(user_id);

-- Individual tasks the Campaign Planner assigns to specific agents.
CREATE TABLE IF NOT EXISTS campaign_tasks (
    id              SERIAL PRIMARY KEY,
    campaign_id     INTEGER NOT NULL REFERENCES campaigns(id),
    agent_slug      TEXT NOT NULL,
    title           TEXT NOT NULL,
    depends_on_task_id INTEGER REFERENCES campaign_tasks(id),
    status          TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'in_progress' | 'needs_approval' | 'done' | 'blocked' | 'error'
    output_summary  TEXT,
    created_at      TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
    updated_at      TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE INDEX IF NOT EXISTS idx_campaign_tasks_campaign ON campaign_tasks(campaign_id);

-- A generic record of one agent "run" (script generated, video job queued,
-- etc). Keeping this generic lets every agent log through the same table
-- instead of 11 bespoke ones, and lets the orchestrator move results
-- between agents by referencing a result_ref.
CREATE TABLE IF NOT EXISTS agent_tasks (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    project_id      INTEGER REFERENCES projects(id),
    campaign_task_id INTEGER REFERENCES campaign_tasks(id),
    agent_slug      TEXT NOT NULL,
    request_summary TEXT,
    status          TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'running' | 'completed' | 'failed' | 'needs_integration'
    result_summary  TEXT,
    error_message   TEXT,
    -- Live job tracking, used by long-running background jobs (the AI Video
    -- Editor) so the UI can show a real, honest progress bar and elapsed
    -- status instead of a blind "loading" page with no feedback.
    progress_percent INTEGER,
    status_message  TEXT,
    result_filename TEXT,
    created_at      TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
    updated_at      TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE INDEX IF NOT EXISTS idx_agent_tasks_user ON agent_tasks(user_id);

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id              SERIAL PRIMARY KEY,
    admin_user_id   INTEGER NOT NULL REFERENCES users(id),
    action          TEXT NOT NULL,
    details         TEXT,
    created_at      TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
);
