from flask import Blueprint, render_template, request, redirect, url_for, flash

import config
import access
import database as db

bp = Blueprint("campaigns", __name__)

# The default task breakdown the Campaign Planner proposes for a new
# campaign (spec section 2, Agent 9 example workflow). Steps whose agent
# isn't included in the user's plan are created but marked 'blocked' --
# the planner must respect subscription restrictions, never silently
# perform them anyway.
DEFAULT_WORKFLOW = [
    ("trend_researcher", "Research content angles & trends for this campaign"),
    ("scriptwriter", "Write scripts for each planned post"),
    ("video_editor", "Edit raw footage into short-form content"),
    ("color_grading", "Apply consistent color grade"),
    ("audio_voice", "Clean audio and generate subtitles"),
    ("social_media_manager", "Prepare captions & publishing schedule"),
    ("ads_manager", "Draft advertising variations (optional)"),
    ("performance_analyst", "Review results once content is live"),
]


@bp.route("/campaigns")
@access.login_required
def list_view():
    user = access.current_user()
    return render_template(
        "campaigns_list.html", title="Campaigns", active="campaigns",
        campaigns=db.list_campaigns(user["id"]),
    )


@bp.route("/campaigns/new", methods=["GET", "POST"])
@access.login_required
def create():
    user = access.current_user()
    plan_slug = access.current_plan_slug()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        objective = request.form.get("objective", "").strip()
        if not name:
            flash("Give the campaign a name.", "danger")
            return redirect(url_for("campaigns.create"))

        campaign_id = db.create_campaign(user["id"], name, objective)
        previous_task_id = None
        for agent_slug, title in DEFAULT_WORKFLOW:
            task_id = db.create_campaign_task(campaign_id, agent_slug, title, depends_on_task_id=previous_task_id)
            if plan_slug is None or not config.plan_includes_agent(plan_slug, agent_slug):
                db.update_campaign_task_status(task_id, "blocked", "Requires Full Studio -- not included in your current plan.")
            previous_task_id = task_id
        flash("Campaign created. The Campaign Planner has broken it into agent tasks below.", "info")
        return redirect(url_for("campaigns.detail", campaign_id=campaign_id))

    return render_template("campaign_new.html", title="New campaign", active="campaigns")


@bp.route("/campaigns/<int:campaign_id>")
@access.login_required
def detail(campaign_id):
    user = access.current_user()
    campaign = db.get_campaign(user["id"], campaign_id)
    if not campaign:
        flash("Campaign not found.", "danger")
        return redirect(url_for("campaigns.list_view"))
    tasks = db.list_campaign_tasks(campaign_id)
    return render_template(
        "campaign_detail.html", title=campaign["name"], active="campaigns",
        campaign=campaign, tasks=tasks, agents=config.AGENTS,
    )


@bp.route("/campaigns/<int:campaign_id>/tasks/<int:task_id>/approve", methods=["POST"])
@access.login_required
def approve_task(campaign_id, task_id):
    user = access.current_user()
    campaign = db.get_campaign(user["id"], campaign_id)
    if not campaign:
        flash("Campaign not found.", "danger")
        return redirect(url_for("campaigns.list_view"))
    db.update_campaign_task_status(task_id, "done", "Approved by you.")
    flash("Task approved.", "info")
    return redirect(url_for("campaigns.detail", campaign_id=campaign_id))
