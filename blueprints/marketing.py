from flask import Blueprint, render_template

import config

bp = Blueprint("marketing", __name__)


@bp.route("/")
def landing():
    return render_template(
        "landing.html",
        title="Your Entire Creative Team. One AI Platform.",
        agents=[config.AGENTS[s] for s in config.AGENT_ORDER],
        plans=config.PLANS,
        plan_order=config.PLAN_ORDER,
    )
