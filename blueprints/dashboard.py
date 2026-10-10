from flask import Blueprint, render_template

import config
import access
import database as db

bp = Blueprint("dashboard", __name__)


@bp.route("/dashboard")
@access.login_required
def home():
    user = access.current_user()
    plan_slug = access.current_plan_slug()
    available_agents = config.agents_for_plan(plan_slug) if plan_slug else []
    locked_agents = [a for a in config.AGENTS.values() if a["slug"] not in [x["slug"] for x in available_agents]]

    return render_template(
        "dashboard.html",
        title="Dashboard",
        active="dashboard",
        projects=db.list_projects(user["id"])[:5],
        campaigns=db.list_campaigns(user["id"])[:4],
        activity=db.recent_activity(user["id"]),
        available_agents=available_agents[:6],
        locked_agents=locked_agents[:3],
        subscription=db.get_active_subscription(user["id"]),
    )
