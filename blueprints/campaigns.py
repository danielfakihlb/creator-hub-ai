from flask import Blueprint, render_template, request, redirect, url_for, flash

import config
import access
import database as db
import llm_client

bp = Blueprint("campaigns", __name__)

# The fallback task breakdown used when a real, OpenAI-generated plan
# isn't possible (no key connected, no objective given, or the model's
# reply couldn't be parsed -- see create() below, which tries
# llm_client.generate_campaign_plan() first). Steps whose agent isn't
# included in the user's plan are created but marked 'blocked' -- the
# planner must respect subscription restrictions, never silently perform
# them anyway. This also still applies when a real plan IS generated,
# since that plan is validated against the same agent slugs.
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

        # Try a real, objective-specific plan from OpenAI first. This is
        # the Campaign Planner's actual reasoning -- not just a fixed
        # template -- but it only runs when there's a key connected AND an
        # objective to reason about. Any failure (no key, no objective, or
        # a reply we can't parse) falls back to DEFAULT_WORKFLOW rather
        # than ever showing an empty or broken campaign.
        workflow = DEFAULT_WORKFLOW
        plan_message = "Campaign created. The Campaign Planner has broken it into agent tasks below."
        if objective and config.is_configured("llm_provider"):
            allowed_slugs = [slug for slug in config.AGENT_ORDER if slug != "campaign_planner"]
            profiles = db.list_brand_profiles(user["id"])
            brand_profile = dict(profiles[0]) if profiles else None
            ok, parsed_tasks, error = llm_client.generate_campaign_plan(
                name, objective, allowed_slugs, brand_profile=brand_profile
            )
            if ok:
                workflow = parsed_tasks
                plan_message = "Campaign created. The Campaign Planner wrote a custom task breakdown for this specific objective -- see below."
            else:
                plan_message = f"Campaign created with the standard task template -- the AI planner couldn't produce a custom plan this time ({error})."
        elif objective:
            plan_message = "Campaign created with the standard task template. Connect an OpenAI key (Account Settings -> Integrations) to get a custom plan tailored to this objective."

        previous_task_id = None
        for agent_slug, title in workflow:
            task_id = db.create_campaign_task(campaign_id, agent_slug, title, depends_on_task_id=previous_task_id)
            if plan_slug is None or not config.plan_includes_agent(plan_slug, agent_slug):
                db.update_campaign_task_status(task_id, "blocked", "Requires Full Studio -- not included in your current plan.")
            previous_task_id = task_id
        flash(plan_message, "info")
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
