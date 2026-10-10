"""SQLite data access layer for Creator Hub AI.

Deliberately raw sqlite3 + hand-written SQL rather than an ORM: it's
dependency-free (works in any Python 3 environment with no installs) and
every query is explicit, which matters for a subscription-gated app where
every row returned has to respect a permission check upstream.
"""

import sqlite3
import os
from contextlib import contextmanager

import config


def init_db():
    os.makedirs(os.path.dirname(config.DATABASE_PATH), exist_ok=True)
    with get_conn() as conn:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")) as f:
            conn.executescript(f.read())
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def query(sql, params=(), one=False):
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        return (rows[0] if rows else None) if one else rows


def execute(sql, params=()):
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


# ---------------------------------------------------------------------
# Users & subscriptions
# ---------------------------------------------------------------------

def get_user_by_email(email):
    return query("SELECT * FROM users WHERE email = ?", (email,), one=True)


def get_user_by_id(user_id):
    return query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)


def create_user(email, password_hash, full_name, is_admin=False):
    return execute(
        "INSERT INTO users (email, password_hash, full_name, is_admin) VALUES (?, ?, ?, ?)",
        (email, password_hash, full_name, 1 if is_admin else 0),
    )


def get_active_subscription(user_id):
    return query(
        "SELECT * FROM subscriptions WHERE user_id = ? AND status = 'active' "
        "ORDER BY created_at DESC LIMIT 1",
        (user_id,),
        one=True,
    )


def set_subscription(user_id, plan_slug, status="active", payment_provider_ref=None):
    """Insert a new subscription row (history-preserving) and supersede the old one."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE subscriptions SET status = 'superseded' WHERE user_id = ? AND status = 'active'",
            (user_id,),
        )
        conn.execute(
            "INSERT INTO subscriptions (user_id, plan_slug, status, payment_provider_ref) "
            "VALUES (?, ?, ?, ?)",
            (user_id, plan_slug, status, payment_provider_ref),
        )
        conn.commit()


def cancel_subscription(user_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE subscriptions SET status = 'canceled', updated_at = datetime('now') "
            "WHERE user_id = ? AND status = 'active'",
            (user_id,),
        )
        conn.commit()


# ---------------------------------------------------------------------
# Brand profiles
# ---------------------------------------------------------------------

def list_brand_profiles(user_id):
    return query("SELECT * FROM brand_profiles WHERE user_id = ? ORDER BY created_at", (user_id,))


def create_brand_profile(user_id, **fields):
    cols = ["user_id"] + list(fields.keys())
    placeholders = ", ".join(["?"] * len(cols))
    values = [user_id] + list(fields.values())
    return execute(f"INSERT INTO brand_profiles ({', '.join(cols)}) VALUES ({placeholders})", values)


def delete_brand_profile(user_id, profile_id):
    execute("DELETE FROM brand_profiles WHERE id = ? AND user_id = ?", (profile_id, user_id))


# ---------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------

def list_projects(user_id):
    return query(
        "SELECT * FROM projects WHERE user_id = ? ORDER BY updated_at DESC", (user_id,)
    )


def get_project(user_id, project_id):
    return query(
        "SELECT * FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id), one=True
    )


def create_project(user_id, name, description="", brand_profile_id=None):
    return execute(
        "INSERT INTO projects (user_id, name, description, brand_profile_id) VALUES (?, ?, ?, ?)",
        (user_id, name, description, brand_profile_id),
    )


# ---------------------------------------------------------------------
# Media library
# ---------------------------------------------------------------------

def list_media(user_id, search=None, folder=None, media_type=None):
    sql = "SELECT * FROM media_assets WHERE user_id = ?"
    params = [user_id]
    if search:
        sql += " AND (filename LIKE ? OR description LIKE ? OR tags LIKE ?)"
        like = f"%{search}%"
        params += [like, like, like]
    if folder:
        sql += " AND folder = ?"
        params.append(folder)
    if media_type:
        sql += " AND media_type = ?"
        params.append(media_type)
    sql += " ORDER BY created_at DESC"
    return query(sql, params)


def list_folders(user_id):
    rows = query("SELECT DISTINCT folder FROM media_assets WHERE user_id = ? ORDER BY folder", (user_id,))
    return [r["folder"] for r in rows]


def create_media_asset(user_id, filename, media_type, folder="Uploads", description="", tags="", project_id=None):
    return execute(
        "INSERT INTO media_assets (user_id, project_id, folder, filename, media_type, description, tags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, project_id, folder, filename, media_type, description, tags),
    )


# ---------------------------------------------------------------------
# Campaigns & tasks
# ---------------------------------------------------------------------

def list_campaigns(user_id):
    return query("SELECT * FROM campaigns WHERE user_id = ? ORDER BY created_at DESC", (user_id,))


def get_campaign(user_id, campaign_id):
    return query(
        "SELECT * FROM campaigns WHERE id = ? AND user_id = ?", (campaign_id, user_id), one=True
    )


def create_campaign(user_id, name, objective="", project_id=None):
    return execute(
        "INSERT INTO campaigns (user_id, project_id, name, objective) VALUES (?, ?, ?, ?)",
        (user_id, project_id, name, objective),
    )


def list_campaign_tasks(campaign_id):
    return query(
        "SELECT * FROM campaign_tasks WHERE campaign_id = ? ORDER BY id", (campaign_id,)
    )


def create_campaign_task(campaign_id, agent_slug, title, depends_on_task_id=None):
    return execute(
        "INSERT INTO campaign_tasks (campaign_id, agent_slug, title, depends_on_task_id) "
        "VALUES (?, ?, ?, ?)",
        (campaign_id, agent_slug, title, depends_on_task_id),
    )


def update_campaign_task_status(task_id, status, output_summary=None):
    execute(
        "UPDATE campaign_tasks SET status = ?, output_summary = COALESCE(?, output_summary), "
        "updated_at = datetime('now') WHERE id = ?",
        (status, output_summary, task_id),
    )


# ---------------------------------------------------------------------
# Generic agent task log (used by every agent workspace)
# ---------------------------------------------------------------------

def log_agent_task(user_id, agent_slug, request_summary, status="needs_integration",
                    result_summary=None, error_message=None, project_id=None, campaign_task_id=None):
    return execute(
        "INSERT INTO agent_tasks (user_id, project_id, campaign_task_id, agent_slug, "
        "request_summary, status, result_summary, error_message) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, project_id, campaign_task_id, agent_slug, request_summary, status,
         result_summary, error_message),
    )


def list_agent_tasks(user_id, agent_slug=None, limit=20):
    sql = "SELECT * FROM agent_tasks WHERE user_id = ?"
    params = [user_id]
    if agent_slug:
        sql += " AND agent_slug = ?"
        params.append(agent_slug)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return query(sql, params)


def recent_activity(user_id, limit=8):
    return query(
        "SELECT * FROM agent_tasks WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
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
