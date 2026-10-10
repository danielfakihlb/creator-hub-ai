"""Authentication & subscription-permission helpers.

This is the one place that decides "can this user do this" so the rule in
the spec -- enforce subscription permissions on the backend, never just in
the UI -- has a single, auditable implementation instead of being repeated
(and potentially forgotten) in every route.
"""

from functools import wraps
from flask import session, redirect, url_for, g, abort, render_template, request

import config
import database as db


def current_user():
    if "user_id" not in session:
        return None
    if not hasattr(g, "_current_user"):
        g._current_user = db.get_user_by_id(session["user_id"])
    return g._current_user


def current_plan_slug():
    """The authoritative plan slug for the logged-in user, or None.

    Falls back to no access (None) rather than guessing, if a user somehow
    has no subscription row -- fail closed, not open.
    """
    user = current_user()
    if not user:
        return None
    sub = db.get_active_subscription(user["id"])
    if not sub:
        return None
    return sub["plan_slug"]


def current_subscription():
    user = current_user()
    if not user:
        return None
    return db.get_active_subscription(user["id"])


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("auth.login", next=request.path))
        if not user["is_admin"]:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def agent_access_required(agent_slug):
    """Block access server-side to any agent the user's plan doesn't include.

    This is the backend enforcement the spec requires: even if someone
    crafts the URL directly, a Creator-plan user cannot reach a
    Full-Studio-only agent's logic -- they only ever see the upgrade
    screen, rendered here, not the agent workspace.
    """
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if current_user() is None:
                return redirect(url_for("auth.login", next=request.path))
            plan_slug = current_plan_slug()
            agent = config.AGENTS.get(agent_slug)
            if agent is None:
                abort(404)
            if plan_slug is None or not config.plan_includes_agent(plan_slug, agent_slug):
                return render_template(
                    "upgrade_required.html",
                    agent=agent,
                    required_plan=config.cheapest_plan_for_agent(agent_slug),
                    current_plan_slug=plan_slug,
                )
            return view(*args, **kwargs)
        return wrapped
    return decorator
