"""The real AI Video Editor -- one unified, prompt-driven agent.

Upload one or more raw clips, describe what you want done in plain
language -- anything from "just add captions" to "translate the captions
to Spanish" to a full cut, color-graded, transitioned, quote-overlaid
edit -- and the agent decides which real tools to run. This doesn't fit
the generic "describe what you want in a text box, wait, read the reply"
workspace every text-only agent uses (see agents.py): it takes real video
file(s), can take a real while to actually process, and hands back a real
finished video file, so it gets its own dedicated page AND its own
background-job system (this used to block the HTTP request until FFmpeg
finished, which on a slow server left the creator staring at a blank
"loading" page with no idea whether it was still working, how far along
it was, or whether it had failed silently -- now the upload returns
immediately and a real, honest, live-updating progress page takes over).

Pipeline (every step below is real, nothing here is simulated):
  1. Save each uploaded clip and measure its real duration (ffprobe).
  2. Transcribe each clip's actual speech with real timestamps (OpenAI
     Whisper), so the editor knows what's really being said and when.
  3. Ask OpenAI to turn the creator's instruction + those real
     transcripts/durations into a concrete operations plan: which real
     tools to run (cut/reorder with transitions, speed change, color
     grade, captions + optional translation, text overlay) and with what
     parameters (llm_client.generate_edit_plan).
  4. Execute that plan for real with FFmpeg (media_processing.execute_edit_plan),
     reporting genuine, measured progress back to the job row the whole
     way, which the status page polls.

Honesty rules, consistent with every other integration in this project:
  - We never claim a video was edited/assembled unless FFmpeg (and OpenAI,
    for transcription/planning) actually ran and produced a real output --
    see media_processing.py and llm_client.py.
  - Progress shown to the creator is real, measured progress (parsed from
    FFmpeg's own `-progress` stream against each step's real expected
    duration), never a fake animation.
  - Uploaded and processed files live under instance/ (this server's own
    disk), not a real cloud storage bucket -- there's no claim of
    permanent, redundant, or cross-device storage here.
"""

import os
import threading
import uuid

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file
from werkzeug.utils import secure_filename

import access
import config
import database as db
import llm_client
import media_processing as mp

bp = Blueprint("media_tools", __name__)

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
MAX_REEL_CLIPS = 5

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_ROOT = os.path.join(_BASE_DIR, "..", "instance", "media_uploads")
PROCESSED_ROOT = os.path.join(_BASE_DIR, "..", "instance", "media_processed")

PLATFORM_LABELS = {
    "reels_tiktok_shorts": "Reels / TikTok / Shorts (1080x1920, vertical)",
    "square_feed": "Square feed post (1080x1080)",
    "landscape_youtube": "YouTube / landscape (1920x1080)",
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


def _run_video_editor_job(task_id, user_id, clip_paths, clip_filenames, instruction, platform):
    """Runs the full AI Video Editor pipeline in a background thread, so
    the HTTP request that started it returns immediately and the creator
    sees real, live progress instead of a blind "loading" page. Every
    status/progress update is written to the real agent_tasks row the
    status-poll endpoint reads -- never a simulated animation. This
    function must never touch Flask's `request`/`session` (they're tied
    to the HTTP request that's already finished by the time this runs).
    """
    out_dir = _user_dir(PROCESSED_ROOT, user_id)
    temp_files = list(clip_paths)
    try:
        db.update_agent_task(task_id, status="running", progress_percent=1, status_message="Measuring your clips...")
        clips_info = []
        for i, path in enumerate(clip_paths):
            duration = mp.probe_duration(path)
            if duration is None or duration <= 0:
                db.update_agent_task(
                    task_id, status="failed",
                    error_message=f"Couldn't read a real duration for '{clip_filenames[i]}' -- it may not be a valid video file.",
                )
                return
            clips_info.append({"index": i, "filename": clip_filenames[i], "duration": duration, "transcript": None})

        n = len(clip_paths)
        for i, path in enumerate(clip_paths):
            db.update_agent_task(task_id, progress_percent=5 + int((i / n) * 30),
                                  status_message=f"Transcribing clip {i + 1} of {n}...")
            ok, audio_path, error = mp.extract_audio(path, out_dir)
            if not ok:
                db.update_agent_task(task_id, status="failed", error_message=f"Couldn't process '{clip_filenames[i]}': {error}")
                return
            temp_files.append(audio_path)
            ok, segments, error = llm_client.transcribe_audio(audio_path)
            if not ok:
                db.update_agent_task(task_id, status="failed", error_message=f"Couldn't transcribe '{clip_filenames[i]}': {error}")
                return
            clips_info[i]["transcript"] = segments

        db.update_agent_task(task_id, progress_percent=40, status_message="Planning the edit with AI...")
        ok, plan, error = llm_client.generate_edit_plan(instruction, clips_info)
        if not ok:
            db.update_agent_task(task_id, status="failed", error_message=f"Couldn't plan the edit: {error}")
            return

        warnings = []
        caption_entries = None
        if plan.get("captions") and plan["captions"].get("enabled"):
            transcripts_by_clip = {c["index"]: c["transcript"] for c in clips_info}
            caption_entries = mp.remap_transcripts_to_timeline(plan["trim_and_reorder"]["segments"], transcripts_by_clip)
            translate_to = plan["captions"].get("translate_to")
            if translate_to and caption_entries:
                db.update_agent_task(task_id, progress_percent=48, status_message=f"Translating captions to {translate_to}...")
                ok_t, translated, err_t = llm_client.translate_caption_segments(caption_entries, translate_to)
                if ok_t:
                    caption_entries = translated
                else:
                    warnings.append(f"Caption translation to {translate_to} failed, kept original language: {err_t}")

        def on_progress(pct, message):
            overall = 50 + (pct / 100.0) * 50
            db.update_agent_task(task_id, progress_percent=int(overall), status_message=message)

        db.update_agent_task(task_id, progress_percent=50, status_message="Assembling your video...")
        ok, output_path, error, notes = mp.execute_edit_plan(
            clip_paths, plan, out_dir, platform=platform,
            caption_entries=caption_entries, on_progress=on_progress,
        )
        if not ok:
            db.update_agent_task(task_id, status="failed", error_message=f"Couldn't assemble the edit: {error}")
            return

        all_notes = warnings + (notes or [])
        bits = []
        if len(plan["trim_and_reorder"]["segments"]) > 1 or plan["trim_and_reorder"].get("transition"):
            bits.append(f"{len(plan['trim_and_reorder']['segments'])} cut(s)")
            if plan["trim_and_reorder"].get("transition"):
                bits.append(f"{plan['trim_and_reorder']['transition']['style']} transitions")
        if plan.get("change_speed"):
            bits.append(f"speed x{plan['change_speed']['factor']:.2f}")
        if plan.get("color_grade"):
            bits.append(f"{plan['color_grade']['preset']} color grade")
        if caption_entries:
            translate_to = plan["captions"].get("translate_to")
            bits.append("captions" + (f" (translated to {translate_to})" if translate_to else ""))
        if plan.get("text_overlay"):
            bits.append("text overlay")
        summary = f"Real edit applied -- {', '.join(bits) if bits else 'clip(s) processed as uploaded'}. {plan.get('notes', '')}".strip()
        if all_notes:
            summary += " (Notes: " + "; ".join(all_notes) + ")"

        db.update_agent_task(
            task_id, status="completed", progress_percent=100, status_message="Done.",
            result_filename=os.path.basename(output_path), result_summary=summary,
        )
    except Exception as exc:  # last-resort honesty net -- a job must never hang forever unexplained
        db.update_agent_task(task_id, status="failed", error_message=f"Unexpected error: {exc}")
    finally:
        _cleanup(*temp_files)


@bp.route("/agents/video-studio")
@access.agent_access_required("video_editor")
def studio():
    user = access.current_user()
    return render_template(
        "video_studio.html", title="AI Video Editor", active="agents",
        agent_video=config.AGENTS["video_editor"],
        ffmpeg_ready=mp.ffmpeg_available(),
        llm_ready=config.is_configured("llm_provider"),
        max_reel_clips=MAX_REEL_CLIPS,
        platform_labels=PLATFORM_LABELS,
        recent_tasks=db.list_agent_tasks(user["id"], agent_slug="video_editor", limit=8),
    )


@bp.route("/agents/video-studio/submit", methods=["POST"])
@access.agent_access_required("video_editor")
def submit():
    user = access.current_user()
    uploads = [f for f in request.files.getlist("clips") if f and f.filename]
    instruction = request.form.get("instruction", "").strip()
    platform = request.form.get("platform", "reels_tiktok_shorts")
    request_summary = f"AI edit {len(uploads)} clip(s): {instruction[:120] or '(no instruction given)'}"

    if not uploads:
        flash("Upload at least one raw clip.", "danger")
        return redirect(url_for("media_tools.studio"))
    if len(uploads) > MAX_REEL_CLIPS:
        flash(f"Upload at most {MAX_REEL_CLIPS} clips at a time.", "danger")
        return redirect(url_for("media_tools.studio"))
    if not instruction:
        flash("Describe what you want done (e.g. \"just add captions\", or \"cut these into a Reel with a quote and dark cinematic color\").", "danger")
        return redirect(url_for("media_tools.studio"))
    if not mp.ffmpeg_available():
        db.log_agent_task(user["id"], "video_editor", request_summary, status="failed",
                           error_message="FFmpeg is not installed on this server.")
        flash("Couldn't process the clips: FFmpeg is not installed on this server.", "danger")
        return redirect(url_for("media_tools.studio"))
    if not config.is_configured("llm_provider"):
        db.log_agent_task(
            user["id"], "video_editor", request_summary, status="needs_integration",
            error_message="The AI Video Editor needs a connected OpenAI API key (for transcription and edit planning) -- it isn't set up yet.",
        )
        flash("This needs a connected OpenAI API key before it can plan and transcribe a real edit (see Account Settings -> Integrations).", "warning")
        return redirect(url_for("media_tools.studio"))

    clip_paths = []
    clip_filenames = []
    for upload in uploads:
        path, error = _save_upload(upload, user["id"])
        if error:
            _cleanup(*clip_paths)
            flash(error, "danger")
            return redirect(url_for("media_tools.studio"))
        clip_paths.append(path)
        clip_filenames.append(upload.filename)

    task_id = db.log_agent_task(user["id"], "video_editor", request_summary, status="running")
    db.update_agent_task(task_id, progress_percent=0, status_message="Queued...")

    threading.Thread(
        target=_run_video_editor_job,
        args=(task_id, user["id"], clip_paths, clip_filenames, instruction, platform),
        daemon=True,
    ).start()

    return redirect(url_for("media_tools.job_status", task_id=task_id))


@bp.route("/agents/video-studio/job/<int:task_id>")
@access.login_required
def job_status(task_id):
    user = access.current_user()
    task = db.get_agent_task(user["id"], task_id)
    if not task or task["agent_slug"] != "video_editor":
        flash("That job isn't available.", "danger")
        return redirect(url_for("media_tools.studio"))
    return render_template("video_studio_job.html", title="Your AI Video Editor job", active="agents", task=task)


@bp.route("/agents/video-studio/job/<int:task_id>/poll")
@access.login_required
def job_poll(task_id):
    user = access.current_user()
    task = db.get_agent_task(user["id"], task_id)
    if not task or task["agent_slug"] != "video_editor":
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "status": task["status"],
        "progress_percent": task["progress_percent"],
        "status_message": task["status_message"],
        "result_filename": task["result_filename"],
        "result_summary": task["result_summary"],
        "error_message": task["error_message"],
    })


@bp.route("/agents/video-studio/download/<filename>")
@access.login_required
def download(filename):
    user = access.current_user()
    safe_name = secure_filename(filename)
    path = os.path.join(_user_dir(PROCESSED_ROOT, user["id"]), safe_name)
    if not os.path.exists(path):
        flash("That file isn't available anymore -- submit the edit again.", "danger")
        return redirect(url_for("media_tools.studio"))
    return send_file(path, as_attachment=True)
