"""Video Editor & Color Grading -- real local processing via FFmpeg.

These two agents don't fit the generic "describe what you want in a text
box" workspace every other agent uses (see agents.py) -- they take an
actual video file and hand back an actual processed video file, so they
get their own dedicated page, the same way Campaign Planner gets its own
dedicated workspace instead of the generic one.

Honesty rules, consistent with every other integration in this project:
  - We never claim a video was edited/graded unless FFmpeg actually ran
    and produced a real output file -- see media_processing.py.
  - Uploaded and processed files live under instance/ (this server's own
    disk), not a real cloud storage bucket -- there's no claim of
    permanent, redundant, or cross-device storage here.
"""

import os
import uuid

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, session, send_file,
)
from werkzeug.utils import secure_filename

import access
import config
import database as db
import media_processing as mp

bp = Blueprint("media_tools", __name__)

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_ROOT = os.path.join(_BASE_DIR, "..", "instance", "media_uploads")
PROCESSED_ROOT = os.path.join(_BASE_DIR, "..", "instance", "media_processed")

PLATFORM_LABELS = {
    "reels_tiktok_shorts": "Reels / TikTok / Shorts (1080x1920, vertical)",
    "square_feed": "Square feed post (1080x1080)",
    "landscape_youtube": "YouTube / landscape (1920x1080)",
}

COLOR_PRESET_LABELS = {
    "natural": "Natural -- subtle contrast & color lift",
    "vibrant": "Vibrant -- punchy, saturated",
    "dark_cinematic": "Dark & cinematic -- moodier, deeper shadows",
    "documentary": "Documentary -- neutral, true-to-life",
    "luxury": "Luxury -- polished, medium-contrast",
}


def _user_dir(root, user_id):
    path = os.path.join(root, str(user_id))
    os.makedirs(path, exist_ok=True)
    return path


def _save_upload(file_storage, user_id):
    """Save an uploaded video to this user's own upload folder. Returns
    (path, error) -- never guesses a file is valid; rejects anything that
    isn't a recognized video extension."""
    filename = secure_filename(file_storage.filename or "")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_VIDEO_EXTENSIONS))
        return None, f"Unsupported file type '{ext or 'unknown'}'. Upload a video file ({allowed})."
    dest_dir = _user_dir(UPLOAD_ROOT, user_id)
    dest_path = os.path.join(dest_dir, f"{uuid.uuid4().hex[:10]}{ext}")
    file_storage.save(dest_path)
    return dest_path, None


def _cleanup(*paths):
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


@bp.route("/agents/video-studio")
@access.agent_access_required("video_editor")
def studio():
    user = access.current_user()
    return render_template(
        "video_studio.html", title="Video Studio", active="agents",
        agent_video=config.AGENTS["video_editor"],
        agent_color=config.AGENTS["color_grading"],
        ffmpeg_ready=mp.ffmpeg_available(),
        platform_labels=PLATFORM_LABELS,
        color_preset_labels=COLOR_PRESET_LABELS,
        recent_video_tasks=db.list_agent_tasks(user["id"], agent_slug="video_editor", limit=5),
        recent_color_tasks=db.list_agent_tasks(user["id"], agent_slug="color_grading", limit=5),
        last_result=session.pop("media_tools_last_result", None),
    )


@bp.route("/agents/video-studio/edit", methods=["POST"])
@access.agent_access_required("video_editor")
def edit_video():
    user = access.current_user()
    upload = request.files.get("video_file")
    platform = request.form.get("platform", "reels_tiktok_shorts")
    start_raw = request.form.get("start_seconds", "").strip()
    end_raw = request.form.get("end_seconds", "").strip()
    platform_label = PLATFORM_LABELS.get(platform, platform)
    request_summary = f"Edit '{upload.filename if upload else '?'}' for {platform_label}"

    if not upload or not upload.filename:
        flash("Choose a video file to upload.", "danger")
        return redirect(url_for("media_tools.studio"))

    if not mp.ffmpeg_available():
        db.log_agent_task(user["id"], "video_editor", request_summary, status="failed",
                           error_message="FFmpeg is not installed on this server.")
        flash("Couldn't process the video: FFmpeg is not installed on this server.", "danger")
        return redirect(url_for("media_tools.studio"))

    input_path, error = _save_upload(upload, user["id"])
    if error:
        db.log_agent_task(user["id"], "video_editor", request_summary, status="failed", error_message=error)
        flash(error, "danger")
        return redirect(url_for("media_tools.studio"))

    try:
        start_seconds = float(start_raw) if start_raw else None
        end_seconds = float(end_raw) if end_raw else None
    except ValueError:
        _cleanup(input_path)
        flash("Start/end seconds must be numbers.", "danger")
        return redirect(url_for("media_tools.studio"))

    out_dir = _user_dir(PROCESSED_ROOT, user["id"])
    ok, output_path, proc_error = mp.trim_and_resize(
        input_path, out_dir, platform=platform, start_seconds=start_seconds, end_seconds=end_seconds
    )
    _cleanup(input_path)

    if not ok:
        db.log_agent_task(user["id"], "video_editor", request_summary, status="failed", error_message=proc_error)
        flash(f"Couldn't process the video: {proc_error}", "danger")
        return redirect(url_for("media_tools.studio"))

    duration_note = f" ({start_raw or '0'}s-{end_raw}s)" if end_raw else ""
    result_summary = f"Produced a real {platform_label} clip{duration_note}. Ready to download."
    db.log_agent_task(user["id"], "video_editor", request_summary, status="completed", result_summary=result_summary)
    session["media_tools_last_result"] = {
        "label": "Your edited video is ready",
        "filename": os.path.basename(output_path),
        "detail": result_summary,
    }
    flash("Done -- your edited video is ready below.", "success")
    return redirect(url_for("media_tools.studio"))


@bp.route("/agents/video-studio/grade", methods=["POST"])
@access.agent_access_required("color_grading")
def grade_video():
    user = access.current_user()
    upload = request.files.get("video_file")
    preset = request.form.get("preset", "natural")
    preset_label = COLOR_PRESET_LABELS.get(preset, preset)
    request_summary = f"Color grade '{upload.filename if upload else '?'}' -- {preset_label}"

    if not upload or not upload.filename:
        flash("Choose a video file to upload.", "danger")
        return redirect(url_for("media_tools.studio"))

    if not mp.ffmpeg_available():
        db.log_agent_task(user["id"], "color_grading", request_summary, status="failed",
                           error_message="FFmpeg is not installed on this server.")
        flash("Couldn't process the video: FFmpeg is not installed on this server.", "danger")
        return redirect(url_for("media_tools.studio"))

    input_path, error = _save_upload(upload, user["id"])
    if error:
        db.log_agent_task(user["id"], "color_grading", request_summary, status="failed", error_message=error)
        flash(error, "danger")
        return redirect(url_for("media_tools.studio"))

    out_dir = _user_dir(PROCESSED_ROOT, user["id"])
    ok, output_path, proc_error = mp.apply_color_preset(input_path, out_dir, preset)
    _cleanup(input_path)

    if not ok:
        db.log_agent_task(user["id"], "color_grading", request_summary, status="failed", error_message=proc_error)
        flash(f"Couldn't process the video: {proc_error}", "danger")
        return redirect(url_for("media_tools.studio"))

    result_summary = f"Applied the '{preset_label}' grade for real. Ready to download."
    db.log_agent_task(user["id"], "color_grading", request_summary, status="completed", result_summary=result_summary)
    session["media_tools_last_result"] = {
        "label": "Your color-graded video is ready",
        "filename": os.path.basename(output_path),
        "detail": result_summary,
    }
    flash("Done -- your color-graded video is ready below.", "success")
    return redirect(url_for("media_tools.studio"))


@bp.route("/agents/video-studio/download/<filename>")
@access.login_required
def download(filename):
    user = access.current_user()
    safe_name = secure_filename(filename)
    path = os.path.join(_user_dir(PROCESSED_ROOT, user["id"]), safe_name)
    if not os.path.exists(path):
        flash("That file isn't available anymore -- process the video again.", "danger")
        return redirect(url_for("media_tools.studio"))
    return send_file(path, as_attachment=True)
