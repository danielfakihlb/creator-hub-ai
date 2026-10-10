from flask import Blueprint, render_template, request, redirect, url_for, flash

import config
import access
import database as db

bp = Blueprint("agents", __name__)

# These three agents already have a dedicated, full-featured section of the
# app (per spec sections: Brand Memory -> Brand Settings, Media Library ->
# Media Library page, Campaign Planner -> Campaign Workspace). Rather than
# duplicate that UI inside a generic "agent workspace" page, the agent
# directory card opens the real page directly.
DEDICATED_PAGE_REDIRECTS = {
    "brand_memory": "brand.settings",
    "media_library": "media.library",
    "campaign_planner": "campaigns.list_view",
}

# Maps an agent to the integration key(s) (config.INTEGRATIONS) its real
# processing depends on, so the workspace can state honestly what's wired
# up vs. still needs a connected service.
AGENT_INTEGRATIONS = {
    "video_editor": ["video_processing"],
    "color_grading": ["video_processing"],
    "scriptwriter": ["llm_provider"],
    "audio_voice": ["speech_audio"],
    "trend_researcher": ["llm_provider"],
    "social_media_manager": ["social_instagram", "social_tiktok", "social_youtube"],
    "ads_manager": ["ads_platform"],
    "performance_analyst": ["analytics"],
}


@bp.route("/agents")
@access.login_required
def directory():
    plan_slug = access.current_plan_slug()
    agent_rows = []
    for slug in config.AGENT_ORDER:
        agent = config.AGENTS[slug]
        agent_rows.append({
            "agent": agent,
            "unlocked": plan_slug is not None and config.plan_includes_agent(plan_slug, slug),
            "required_plan": config.cheapest_plan_for_agent(slug),
        })
    return render_template("agents_directory.html", title="AI Agents", active="agents", agent_rows=agent_rows)


@bp.route("/agents/<agent_slug>", methods=["GET", "POST"])
@access.login_required
def detail(agent_slug):
    # Permission check happens below (needs agent_slug at call time), then
    # we optionally redirect to a dedicated page before building a generic
    # workspace.
    plan_slug = access.current_plan_slug()
    agent = config.AGENTS.get(agent_slug)
    if agent is None:
        return redirect(url_for("agents.directory"))

    if plan_slug is None or not config.plan_includes_agent(plan_slug, agent_slug):
        return render_template(
            "upgrade_required.html",
            title=agent["name"],
            agent=agent,
            required_plan=config.cheapest_plan_for_agent(agent_slug),
            current_plan_slug=plan_slug,
        )

    if agent_slug in DEDICATED_PAGE_REDIRECTS:
        return redirect(url_for(DEDICATED_PAGE_REDIRECTS[agent_slug]))

    user = access.current_user()
    integration_keys = AGENT_INTEGRATIONS.get(agent_slug, [])
    integrations_ready = all(config.is_configured(k) for k in integration_keys) if integration_keys else True

    if request.method == "POST":
        request_summary = request.form.get("request_summary", "").strip()
        if not request_summary:
            flash("Describe what you'd like this agent to do.", "danger")
        else:
            if integrations_ready:
                # Real processing would be dispatched to the connected
                # service here. None is connected in this build, so this
                # branch is unreachable until you add real credentials --
                # it is left in place as the integration point.
                db.log_agent_task(user["id"], agent_slug, request_summary, status="running")
                flash("Task queued.", "info")
            else:
                db.log_agent_task(
                    user["id"], agent_slug, request_summary, status="needs_integration",
                    error_message="This agent needs a connected integration before it can produce a real result.",
                )
                flash("Request saved. This agent needs an integration connected (see below) before it can run for real.", "warning")
        return redirect(url_for("agents.detail", agent_slug=agent_slug))

    recent_tasks = db.list_agent_tasks(user["id"], agent_slug=agent_slug, limit=10)
    missing_integrations = [config.INTEGRATIONS[k]["label"] for k in integration_keys if not config.is_configured(k)]

    return render_template(
        "agent_detail.html",
        title=agent["name"],
        active="agents",
        agent=agent,
        integrations_ready=integrations_ready,
        missing_integrations=missing_integrations,
        recent_tasks=recent_tasks,
    )
