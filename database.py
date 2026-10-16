"""Postgres data access layer for Creator Hub AI.

This used to be a plain SQLite file on local disk. The problem with that
wasn't performance -- it's that Render's web service disk is NOT
persistent: every restart (a deploy, a crash, or -- on the Free plan --
just 15 minutes of no traffic) wiped it completely, taking every user,
project, and task with it. Moving to a real hosted Postgres database
fixes that: the database is now its own separate, persistent resource,
independent of the web service's disk.

Still deliberately raw SQL rather than an ORM -- it's dependency-light
(psycopg2 is the one new package this needed) and every query stays
explicit, which matters for a subscription-gated app where every row
returned has to respect a permission check upstream.

Every function below keeps its exact original name and arguments from
the SQLite version. Nothing that calls into this file -- any blueprint,
any route -- needed to change for this migration; only this file,
schema.sql, and the DATABASE_URL setting in config.py did.
"""

import hashlib
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras

import config

PASSWORD_RESET_TOKEN_TTL_SECONDS = 3600  # 1 hour


class ReferencedRowError(Exception):
    """Raised when a delete is blocked because another table still
    references that row (e.g. a project still points at the brand profile
    you're trying to remove). Postgres enforces this with a real foreign-key
    constraint -- this turns that low-level database error into something a
    blueprint can catch and show a clear message for, instead of it
    surfacing as a raw 500."""


def _now():
    """Current UTC time as a plain 'YYYY-MM-DD HH:MM:SS' string -- the same
    format this app has always stored timestamps in. Previously that came
    from SQLite's datetime('now'); here it's computed in Python and passed
    in as a normal parameter instead of relying on a database-specific SQL
    function, which also means this one function is the only new piece of
    timestamp logic that actually needed testing, and it's plain Python."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def init_db():
    if not config.DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. This app now needs a real Postgres "
            "database to run -- set the DATABASE_URL environment variable "
            "to the connection string for your Render Postgres instance."
        )
    with get_conn() as conn:
        with conn.cursor() as cur:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")) as f:
                cur.execute(f.read())
        conn.commit()


@contextmanager
def get_conn():
    conn = psycopg2.connect(config.DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()


def query(sql, params=(), one=False):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return (rows[0] if rows else None) if one else rows


def execute(sql, params=()):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            result = None
            if cur.description:  # an INSERT ... RETURNING id, etc.
                row = cur.fetchone()
                result = row[0] if row else None
            conn.commit()
            return result


# ---------------------------------------------------------------------
# Users & subscriptions
# ---------------------------------------------------------------------

def get_user_by_email(email):
    return query("SELECT * FROM users WHERE email = %s", (email,), one=True)


def get_user_by_id(user_id):
    return query("SELECT * FROM users WHERE id = %s", (user_id,), one=True)


def create_user(email, password_hash, full_name, is_admin=False):
    return execute(
        "INSERT INTO users (email, password_hash, full_name, is_admin) VALUES (%s, %s, %s, %s) RETURNING id",
        (email, password_hash, full_name, 1 if is_admin else 0),
    )


def update_user_password(user_id, password_hash):
    execute("UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, user_id))


# ---------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------

def create_password_reset_token(user_id):
    """Issue a real, high-entropy password-reset token (32 random bytes,
    URL-safe) for this user. Only a SHA-256 hash of it is ever stored --
    same reasoning as hashing passwords -- so a leaked database alone
    couldn't be used to reset anyone's password. The raw token is
    returned here ONCE, for the caller to put straight into the reset
    email/link; it is never persisted anywhere. Any reset tokens already
    issued to this user are invalidated first, so only the newest link
    actually works."""
    invalidate_password_resets_for_user(user_id)
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=PASSWORD_RESET_TOKEN_TTL_SECONDS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    execute(
        "INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
        (user_id, token_hash, expires_at),
    )
    return token


def get_valid_password_reset(token):
    """Look up a password-reset token by its hash. Returns the matching
    password_resets row (plus the owning user's email, for display) only
    if it hasn't already been used and hasn't expired -- otherwise None,
    so a caller never has to re-derive either check itself."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    row = query(
        "SELECT pr.*, u.email AS user_email FROM password_resets pr "
        "JOIN users u ON u.id = pr.user_id "
        "WHERE pr.token_hash = %s AND pr.used_at IS NULL",
        (token_hash,),
        one=True,
    )
    if not row or row["expires_at"] < _now():
        return None
    return row


def mark_password_reset_used(reset_id):
    execute("UPDATE password_resets SET used_at = %s WHERE id = %s", (_now(), reset_id))


def invalidate_password_resets_for_user(user_id):
    """Mark every still-valid reset token for this user as used. Called
    before issuing a new one (so only the latest emailed link works) and
    again right after a successful reset (so an older, already-emailed
    link can't be replayed afterward)."""
    execute(
        "UPDATE password_resets SET used_at = %s WHERE user_id = %s AND used_at IS NULL",
        (_now(), user_id),
    )


def get_active_subscription(user_id):
    return query(
        "SELECT * FROM subscriptions WHERE user_id = %s AND status = 'active' "
        "ORDER BY created_at DESC LIMIT 1",
        (user_id,),
        one=True,
    )


def set_subscription(user_id, plan_slug, status="active", payment_provider_ref=None):
    """Insert a new subscription row (history-preserving) and supersede the old one."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE subscriptions SET status = 'superseded' WHERE user_id = %s AND status = 'active'",
                (user_id,),
            )
            cur.execute(
                "INSERT INTO subscriptions (user_id, plan_slug, status, payment_provider_ref) "
                "VALUES (%s, %s, %s, %s)",
                (user_id, plan_slug, status, payment_provider_ref),
            )
        conn.commit()


def cancel_subscription(user_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE subscriptions SET status = 'canceled', updated_at = %s "
                "WHERE user_id = %s AND status = 'active'",
                (_now(), user_id),
            )
        conn.commit()


# ---------------------------------------------------------------------
# Brand profiles
# ---------------------------------------------------------------------

def list_brand_profiles(user_id):
    return query("SELECT * FROM brand_profiles WHERE user_id = %s ORDER BY created_at", (user_id,))


def create_brand_profile(user_id, **fields):
    cols = ["user_id"] + list(fields.keys())
    placeholders = ", ".join(["%s"] * len(cols))
    values = [user_id] + list(fields.values())
    return execute(
        f"INSERT INTO brand_profiles ({', '.join(cols)}) VALUES ({placeholders}) RETURNING id", values
    )


def delete_brand_profile(user_id, profile_id):
    try:
        execute("DELETE FROM brand_profiles WHERE id = %s AND user_id = %s", (profile_id, user_id))
    except psycopg2.IntegrityError:
        raise ReferencedRowError("This brand profile is still assigned to a project.")


# ---------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------

def list_projects(user_id):
    return query(
        "SELECT * FROM projects WHERE user_id = %s ORDER BY updated_at DESC", (user_id,)
    )


def get_project(user_id, project_id):
    return query(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s", (project_id, user_id), one=True
    )


def create_project(user_id, name, description="", brand_profile_id=None):
    return execute(
        "INSERT INTO projects (user_id, name, description, brand_profile_id) VALUES (%s, %s, %s, %s) RETURNING id",
        (user_id, name, description, brand_profile_id),
    )


# ---------------------------------------------------------------------
# Media library
# ---------------------------------------------------------------------

def list_media(user_id, search=None, folder=None, media_type=None):
    sql = "SELECT * FROM media_assets WHERE user_id = %s"
    params = [user_id]
    if search:
        sql += " AND (filename LIKE %s OR description LIKE %s OR tags LIKE %s)"
        like = f"%{search}%"
        params += [like, like, like]
    if folder:
        sql += " AND folder = %s"
        params.append(folder)
    if media_type:
        sql += " AND media_type = %s"
        params.append(media_type)
    sql += " ORDER BY created_at DESC"
    return query(sql, params)


def list_folders(user_id):
    rows = query("SELECT DISTINCT folder FROM media_assets WHERE user_id = %s ORDER BY folder", (user_id,))
    return [r["folder"] for r in rows]


def create_media_asset(user_id, filename, media_type, folder="Uploads", description="", tags="", project_id=None):
    return execute(
        "INSERT INTO media_assets (user_id, project_id, folder, filename, media_type, description, tags) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (user_id, project_id, folder, filename, media_type, description, tags),
    )


# ---------------------------------------------------------------------
# Campaigns & tasks
# ---------------------------------------------------------------------

def list_campaigns(user_id):
    return query("SELECT * FROM campaigns WHERE user_id = %s ORDER BY created_at DESC", (user_id,))


def get_campaign(user_id, campaign_id):
    return query(
        "SELECT * FROM campaigns WHERE id = %s AND user_id = %s", (campaign_id, user_id), one=True
    )


def create_campaign(user_id, name, objective="", project_id=None):
    return execute(
        "INSERT INTO campaigns (user_id, project_id, name, objective) VALUES (%s, %s, %s, %s) RETURNING id",
        (user_id, project_id, name, objective),
    )


def list_campaign_tasks(campaign_id):
    return query(
        "SELECT * FROM campaign_tasks WHERE campaign_id = %s ORDER BY id", (campaign_id,)
    )


def create_campaign_task(campaign_id, agent_slug, title, depends_on_task_id=None):
    return execute(
        "INSERT INTO campaign_tasks (campaign_id, agent_slug, title, depends_on_task_id) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (campaign_id, agent_slug, title, depends_on_task_id),
    )


def update_campaign_task_status(task_id, status, output_summary=None):
    execute(
        "UPDATE campaign_tasks SET status = %s, output_summary = COALESCE(%s, output_summary), "
        "updated_at = %s WHERE id = %s",
        (status, output_summary, _now(), task_id),
    )


# ---------------------------------------------------------------------
# Generic agent task log (used by every agent workspace)
# ---------------------------------------------------------------------

def log_agent_task(user_id, agent_slug, request_summary, status="needs_integration",
                    result_summary=None, error_message=None, project_id=None, campaign_task_id=None):
    return execute(
        "INSERT INTO agent_tasks (user_id, project_id, campaign_task_id, agent_slug, "
        "request_summary, status, result_summary, error_message) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id",
        (user_id, project_id, campaign_task_id, agent_slug, request_summary, status,
         result_summary, error_message),
    )


def list_agent_tasks(user_id, agent_slug=None, limit=20):
    sql = "SELECT * FROM agent_tasks WHERE user_id = %s"
    params = [user_id]
    if agent_slug:
        sql += " AND agent_slug = %s"
        params.append(agent_slug)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    return query(sql, params)


def update_agent_task(task_id, **fields):
    """Partial update of an in-flight (or finished) agent_tasks row -- used
    by background jobs (the AI Video Editor) to report real, live progress
    as it actually happens, not a simulated one. Only the fields passed are
    changed; e.g. update_agent_task(task_id, progress_percent=40,
    status_message="Transcribing clip 2 of 3...").
    """
    allowed = {"status", "progress_percent", "status_message", "result_filename",
               "result_summary", "error_message"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = %s" for k in fields) + ", updated_at = %s"
    execute(f"UPDATE agent_tasks SET {set_clause} WHERE id = %s", list(fields.values()) + [_now(), task_id])


def get_agent_task(user_id, task_id):
    """Fetch one task row, scoped to its owner -- a job's live status is
    only ever visible to the user who started it."""
    return query("SELECT * FROM agent_tasks WHERE id = %s AND user_id = %s", (task_id, user_id), one=True)


def recent_activity(user_id, limit=8):
    return query(
        "SELECT * FROM agent_tasks WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
        (user_id, limit),
    )


def seed_demo_account():
    """Create one demo account so the app can be explored immediately.

    This is clearly-labeled demo data for trying the product, not a claim
    about any real user or real content -- the login page states the
    credentials openly.
    """
    from werkzeug.security import generate_password_hash

    existing = get_user_by_email("demo@creatorhub.ai")
    if existing:
        return existing["id"]

    user_id = create_user(
        "demo@creatorhub.ai", generate_password_hash("demo12345"), "Demo Creator", is_admin=True
    )
    set_subscription(user_id, "full_studio", status="active")
    profile_id = create_brand_profile(
        user_id,
        name="AlphaLeb",
        description="Combat sports training & lifestyle content",
        tone_of_voice="Motivational, direct, no fluff",
        target_audience="Combat sports fans, 18-34",
        primary_color="#7c5cff",
        secondary_color="#13151b",
        font_family="Inter",
        preferred_style="Dark, cinematic, high-contrast",
    )
    project_id = create_project(user_id, "Fight Camp Vlog Series", "Weekly training vlog edits", profile_id)
    create_media_asset(user_id, "pad-work-session-04.mp4", "video", "Training Footage",
                        "Pad work session, Phuket gym", "training,padwork,phuket", project_id)
    create_media_asset(user_id, "sparring-round-2.mp4", "video", "Training Footage",
                        "Sparring round footage", "sparring,training", project_id)
    campaign_id = create_campaign(user_id, "14-Day Fight Camp Launch", "Build hype for upcoming fight", project_id)
    t1 = create_campaign_task(campaign_id, "trend_researcher", "Research fight-camp content angles")
    t2 = create_campaign_task(campaign_id, "scriptwriter", "Write Day 1-3 scripts", depends_on_task_id=t1)
    create_campaign_task(campaign_id, "video_editor", "Edit Day 1 highlight reel", depends_on_task_id=t2)
    update_campaign_task_status(t1, "done", "3 content angles identified from provided context")
    update_campaign_task_status(t2, "in_progress")
    log_agent_task(user_id, "trend_researcher", "Research fight-camp content angles",
                   status="completed", result_summary="3 content angles drafted from your inputs",
                   project_id=project_id, campaign_task_id=t1)
    return user_id
