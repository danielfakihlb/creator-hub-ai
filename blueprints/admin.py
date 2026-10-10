from flask import Blueprint, render_template

import config
import access
import database as db

bp = Blueprint("admin", __name__)


@bp.route("/admin")
@access.admin_required
def overview():
    users = db.query("SELECT * FROM users ORDER BY created_at DESC")
    users_with_plans = []
    for u in users:
        sub = db.get_active_subscription(u["id"])
        users_with_plans.append({"user": u, "subscription": sub})

    return render_template(
        "admin.html", title="Admin", active="admin",
        users_with_plans=users_with_plans,
        plans=config.PLANS, plan_order=config.PLAN_ORDER,
        agents=[config.AGENTS[s] for s in config.AGENT_ORDER],
        integrations=config.INTEGRATIONS,
        integrations_status={k: config.is_configured(k) for k in config.INTEGRATIONS},
    )
