"""The real Social Media Manager workspace: caption/hashtag/content-calendar
drafting (OpenAI, same as before) PLUS actually publishing straight to
Instagram, for real, through Upload-Post (see upload_post_client.py for why
that's the backend instead of Meta's own API directly).

This gets its own dedicated page instead of the generic text-box workspace
in agents.py, same reasoning as the AI Video Editor and AI Audio & Voice
Agent: publishing takes a real uploaded file and talks to a real external
service, which doesn't fit "type an instruction, read a text reply back".
"""

import os

from flask import Blueprint, render_template, request, redirect, url_for, flash

import access
import config
import database as db
import llm_client
import upload_post_client as up

bp = Blueprint("social_publishing", __name__)

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}


def _media_kind(filename):
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in ALLOWED_IMAGE_EXTENSIONS:
        return "photo"
    if ext in ALLOWED_VIDEO_EXTENSIONS:
        return "video"
    return None


@bp.route("/agents/social-media-manager")
@access.login_required
def studio():
    plan_slug = access.current_plan_slug()
    agent = config.AGENTS["social_media_manager"]
    if plan_slug is None or not config.plan_includes_agent(plan_slug, "social_media_manager"):
        return render_template(
            "upgrade_required.html", title=agent["name"], agent=agent,
            required_plan=config.cheapest_plan_for_agent("social_media_manager"),
            current_plan_slug=plan_slug,
        )

    user = access.current_user()
    agency_id = user["agency_id"]
    posting_ready = config.is_configured("social_posting")
    connected = up.connected_platforms(agency_id) if (agency_id and posting_ready) else set()
    recent_tasks = db.list_agent_tasks(user["id"], agent_slug="social_media_manager", limit=10)

    return render_template(
        "social_publishing.html", title=agent["name"], active="agents",
        agent=agent,
        llm_ready=config.is_configured("llm_provider"),
        posting_ready=posting_ready,
        instagram_connected="instagram" in connected,
        recent_tasks=recent_tasks,
    )


@bp.route("/agents/social-media-manager/draft", methods=["POST"])
@access.login_required
def draft():
    user = access.current_user()
    request_summary = request.form.get("request_summary", "").strip()
    if not request_summary:
        flash("Describe what you'd like drafted.", "danger")
        return redirect(url_for("social_publishing.studio"))

    if not config.is_configured("llm_provider"):
        db.log_agent_task(
            user["id"], "social_media_manager", request_summary, status="needs_integration",
            error_message="This agent needs a connected OpenAI key before it can draft for real.",
        )
        flash("Request saved. Connect an OpenAI key (Account Settings → Integrations) to generate real drafts.", "warning")
        return redirect(url_for("social_publishing.studio"))

    profiles = db.list_brand_profiles(user["id"])
    brand_profile = dict(profiles[0]) if profiles else None
    ok, result_text, error_text = llm_client.generate_social_content(request_summary, brand_profile=brand_profile)
    if ok:
        db.log_agent_task(user["id"], "social_media_manager", request_summary, status="completed", result_summary=result_text)
        flash("Done -- see the draft below.", "success")
    else:
        db.log_agent_task(user["id"], "social_media_manager", request_summary, status="failed", error_message=error_text)
        flash(f"The request couldn't be completed: {error_text}", "danger")
    return redirect(url_for("social_publishing.studio"))


@bp.route("/agents/social-media-manager/connect-instagram", methods=["POST"])
@access.admin_required
def connect_instagram():
    """Admin-only: starts the agency's own Instagram connection. Any
    teammate can draft or publish afterward -- this gate is about who
    manages the agency's shared connected account, same as branding."""
    user = access.current_user()
    if not user["agency_id"]:
        flash("No agency found for your account.", "danger")
        return redirect(url_for("social_publishing.studio"))

    agency = db.get_agency(user["agency_id"])
    redirect_url = url_for("social_publishing.studio", _external=True)
    ok, connect_url, error = up.get_connect_url(
        user["agency_id"], agency["name"], redirect_url, logo_url=agency.get("logo_url"),
    )
    if not ok:
        flash(f"Couldn't start the Instagram connection: {error}", "danger")
        return redirect(url_for("social_publishing.studio"))
    return redirect(connect_url)


@bp.route("/agents/social-media-manager/publish", methods=["POST"])
@access.login_required
def publish():
    user = access.current_user()
    agency_id = user["agency_id"]
    caption = request.form.get("caption", "").strip()
    file_storage = request.files.get("media")

    if not agency_id:
        flash("No agency found for your account.", "danger")
        return redirect(url_for("social_publishing.studio"))
    if not config.is_configured("social_posting"):
        flash("Instagram publishing isn't connected on this server yet.", "danger")
        return redirect(url_for("social_publishing.studio"))
    if "instagram" not in up.connected_platforms(agency_id):
        flash("Connect your agency's Instagram account first.", "warning")
        return redirect(url_for("social_publishing.studio"))
    if not file_storage or not file_storage.filename:
        flash("Attach a photo or video to publish.", "danger")
        return redirect(url_for("social_publishing.studio"))
    if not caption:
        flash("Write a caption for this post.", "danger")
        return redirect(url_for("social_publishing.studio"))

    media_kind = _media_kind(file_storage.filename)
    if media_kind is None:
        flash("Unsupported file type. Use a photo (.jpg/.png/.webp) or a video (.mp4/.mov/.webm).", "danger")
        return redirect(url_for("social_publishing.studio"))

    ok, result, error = up.publish_instagram(agency_id, caption, file_storage, media_kind)
    summary = f"Published to Instagram: {caption[:80]}"
    if ok:
        db.log_agent_task(
            user["id"], "social_media_manager", summary, status="completed",
            result_summary=("Posted to Instagram. " + result.get("url", "")).strip() if result.get("url") else "Posted to Instagram.",
        )
        flash("Posted to Instagram.", "success")
    else:
        db.log_agent_task(user["id"], "social_media_manager", summary, status="failed", error_message=error)
        flash(f"Instagram didn't accept this post: {error}", "danger")
    return redirect(url_for("social_publishing.studio"))
