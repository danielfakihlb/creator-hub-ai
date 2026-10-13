from flask import Blueprint, render_template, request, redirect, url_for, flash

import config
import access
import database as db
import llm_client

bp = Blueprint("agents", __name__)

# Campaign Planner already has a dedicated, full-featured section of the app
# (the Campaign Workspace). Rather than duplicate that UI inside a generic
# "agent workspace" page, its directory card opens the real page directly.
# (Brand Memory and Media Library used to be here too, but they aren't
# agents -- see config.ACCOUNT_FEATURES -- so they're reached from the
# sidebar / the "Also included" section instead of /agents/<slug>.)
DEDICATED_PAGE_REDIRECTS = {
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
    # Caption/hashtag/calendar DRAFTING only needs the OpenAI key -- actual
    # posting to these platforms is a separate, much bigger project (each
    # platform's own developer app + review process; see the roadmap
    # Daniel asked to keep in the project notes) that isn't built yet.
    "social_media_manager": ["llm_provider"],
    # Ads Manager's creative/copy generation only needs the OpenAI key --
    # launching a real campaign or spending budget is a separate, much
    # bigger step (a connected ad-platform account + explicit per-launch
    # approval) that isn't built yet. Gating on "ads_platform" here would
    # have blocked the creative half of the agent for no honest reason.
    "ads_manager": ["llm_provider"],
    "performance_analyst": ["analytics"],
}


def _run_scriptwriter(user, request_summary):
    """Real processing for the Scriptwriter agent: an actual call to OpenAI.

    Uses the creator's first Brand Memory profile (if any) for tone/voice
    context. Returns (ok, result_text, error_message) -- never invents a
    script if the call fails.
    """
    profiles = db.list_brand_profiles(user["id"])
    brand_profile = dict(profiles[0]) if profiles else None
    return llm_client.generate_script(request_summary, brand_profile=brand_profile)


def _run_trend_researcher(user, request_summary):
    """Real processing for the Trend Researcher agent: an actual call to
    OpenAI, with a system prompt that forbids inventing fake real-time
    trends (see llm_client.TREND_RESEARCHER_SYSTEM_PROMPT) -- it builds on
    whatever the creator describes, or clearly labels its own ideas as
    strategic brainstorming rather than claiming live trend data it
    doesn't have.
    """
    profiles = db.list_brand_profiles(user["id"])
    brand_profile = dict(profiles[0]) if profiles else None
    return llm_client.generate_trend_research(request_summary, brand_profile=brand_profile)


def _run_ads_manager(user, request_summary):
    """Real processing for the Ads Manager agent: an actual call to OpenAI
    that writes ad concepts, headlines, and copy variations.

    Scope-critical: this only ever produces creative text. It never
    launches a campaign and never spends or estimates real budget --
    llm_client.ADS_MANAGER_SYSTEM_PROMPT forbids that, and the output
    always opens with a "Note on scope" line saying so explicitly.
    """
    profiles = db.list_brand_profiles(user["id"])
    brand_profile = dict(profiles[0]) if profiles else None
    return llm_client.generate_ads_creative(request_summary, brand_profile=brand_profile)


def _run_social_media_manager(user, request_summary):
    """Real processing for the Social Media Manager agent: an actual call
    to OpenAI that writes captions, hashtags, and a content calendar draft.

    Scope-critical: this only ever produces drafts. It never posts,
    schedules, or publishes to any real account -- that needs each
    platform's own connected API, which isn't built yet.
    llm_client.SOCIAL_MEDIA_MANAGER_SYSTEM_PROMPT forbids claiming
    otherwise, and the output always opens with a "Note on scope" line
    saying so explicitly.
    """
    profiles = db.list_brand_profiles(user["id"])
    brand_profile = dict(profiles[0]) if profiles else None
    return llm_client.generate_social_content(request_summary, brand_profile=brand_profile)


# Agents with real, working processing wired up. Every other agent still
# needs its backend built even once its integration key is connected --
# AGENT_INTEGRATIONS only tracks whether the *credential* is present, this
# tracks whether we actually call out and do something with it. Keeping
# these separate means a configured-but-unbuilt integration never gets
# silently marked "running" with nothing actually happening behind it.
AGENT_HANDLERS = {
    "scriptwriter": _run_scriptwriter,
    "trend_researcher": _run_trend_researcher,
    "ads_manager": _run_ads_manager,
    "social_media_manager": _run_social_media_manager,
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
    account_features = [config.ACCOUNT_FEATURES[s] for s in config.ACCOUNT_FEATURE_ORDER]
    return render_template(
        "agents_directory.html", title="AI Agents", active="agents",
        agent_rows=agent_rows, account_features=account_features,
    )


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
        elif integrations_ready and agent_slug in AGENT_HANDLERS:
            # The integration key is present AND real processing is built
            # for this agent -- actually call out and get a real result.
            # This can take a few seconds (it's a live API call), and it
            # never fakes success: whatever comes back is exactly what's
            # stored and shown below.
            ok, result_text, error_text = AGENT_HANDLERS[agent_slug](user, request_summary)
            if ok:
                db.log_agent_task(user["id"], agent_slug, request_summary, status="completed", result_summary=result_text)
                flash("Done -- see the result below.", "success")
            else:
                db.log_agent_task(user["id"], agent_slug, request_summary, status="failed", error_message=error_text)
                flash(f"The request couldn't be completed: {error_text}", "danger")
        elif integrations_ready:
            # Credential is connected, but this agent's real processing
            # isn't built yet -- stay honest rather than claim it ran.
            db.log_agent_task(
                user["id"], agent_slug, request_summary, status="needs_integration",
                error_message="The integration is connected, but this agent's real processing hasn't been built yet.",
            )
            flash("Request saved. This agent's integration is connected, but its real processing isn't built yet.", "warning")
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
