-- Creator Hub AI -- Postgres schema.
--
-- This used to be a SQLite schema (hence the comment that used to live
-- here about porting cleanly to Postgres "later" -- this is that). Column
-- types and constraints are kept as close to the original as possible so
-- nothing calling into database.py had to change.

-- Agencies: Creator Hub AI is sold to social media/marketing agencies as an
-- internal tool for their own team, not as a broad consumer app. Every user
-- belongs to exactly one agency; an agency's team members share one pool of
-- brand profiles, projects, media, campaigns, and agent history, completely
-- walled off from every other agency's data (database.py enforces this by
-- always scoping through the acting user's agency_id). "is_active" is the
-- on/off switch a platform admin uses to suspend an agency (e.g. non-payment)
-- without deleting anything. logo_url/name let an agency show its own brand
-- inside the app instead of "Creator Hub AI" -- see base_app.html.
CREATE TABLE IF NOT EXISTS agencies (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    logo_url        TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
);

-- The agency's own stable username on Upload-Post (upload-post.com), the
-- unified posting API Creator Hub AI publishes through -- one Upload-Post
-- "profile" per agency, so each agency connects and posts to its own
-- Instagram account, completely separate from every other agency's, even
-- though every agency shares Daniel's one Upload-Post API key. Null until
-- the agency's first connect attempt creates it (see upload_post_client.py).
ALTER TABLE agencies ADD COLUMN IF NOT EXISTS upload_post_username TEXT;

CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    full_name       TEXT NOT NULL,
    is_admin        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
);

-- Added via ALTER rather than above, since CREATE TABLE IF NOT EXISTS is a
-- no-op on a database that already has a users table (every environment
-- this app has ever been deployed to). agency_id is nullable at the column
-- level -- not every historical row has one -- but database.py never
-- creates a new user without it; seed_demo_account() backfills the one
-- pre-existing demo account the first time this runs against it.
-- is_admin's meaning narrows here: it now means "admin of their own
-- agency" (can edit its branding, invite teammates) -- a separate
-- is_platform_owner flag is the actual cross-agency superadmin, which only
-- Daniel's own account should ever have.
ALTER TABLE users ADD COLUMN IF NOT EXISTS agency_id INTEGER REFERENCES agencies(id);
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_platform_owner INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_users_agency ON users(agency_id);

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

-- agency_id here is a convenience denormalization (it's derivable by
-- joining through user_id -> users.agency_id), kept so billing/reporting
-- queries can filter by agency directly without a join. database.py always
-- sets it from the owning user's agency_id when it creates a row.
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS agency_id INTEGER REFERENCES agencies(id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_agency ON subscriptions(agency_id);

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

-- Brand profiles are the main thing an agency's whole team shares (one
-- client's brand voice/colors, usable by every teammate's projects), so
-- this is a real scoping column, not just denormalization.
ALTER TABLE brand_profiles ADD COLUMN IF NOT EXISTS agency_id INTEGER REFERENCES agencies(id);
CREATE INDEX IF NOT EXISTS idx_brand_profiles_agency ON brand_profiles(agency_id);

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

-- Projects are shared across an agency's team the same way brand profiles
-- are -- a teammate should see projects their colleagues started, not just
-- their own.
ALTER TABLE projects ADD COLUMN IF NOT EXISTS agency_id INTEGER REFERENCES agencies(id);
CREATE INDEX IF NOT EXISTS idx_projects_agency ON projects(agency_id);

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

-- The whole media library is a shared agency asset (uploaded footage,
-- brand logos, etc that any teammate should be able to reuse).
ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS agency_id INTEGER REFERENCES agencies(id);
CREATE INDEX IF NOT EXISTS idx_media_agency ON media_assets(agency_id);

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

-- Shared across the agency's team like projects and media.
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS agency_id INTEGER REFERENCES agencies(id);
CREATE INDEX IF NOT EXISTS idx_campaigns_agency ON campaigns(agency_id);

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

-- So the agency admin/platform-owner views can show "everything our team's
-- agents have done" without joining through user_id for every row.
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS agency_id INTEGER REFERENCES agencies(id);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_agency ON agent_tasks(agency_id);

-- Password-reset links. Only a SHA-256 hash of the real token is ever
-- stored (same reasoning as hashing passwords) -- if this table ever
-- leaked, the hashes alone couldn't be used to reset anyone's password.
CREATE TABLE IF NOT EXISTS password_resets (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    token_hash      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    used_at         TEXT,
    created_at      TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE INDEX IF NOT EXISTS idx_password_resets_token_hash ON password_resets(token_hash);
CREATE INDEX IF NOT EXISTS idx_password_resets_user ON password_resets(user_id);

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id              SERIAL PRIMARY KEY,
    admin_user_id   INTEGER NOT NULL REFERENCES users(id),
    action          TEXT NOT NULL,
    details         TEXT,
    created_at      TEXT NOT NULL DEFAULT (TO_CHAR(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
);
