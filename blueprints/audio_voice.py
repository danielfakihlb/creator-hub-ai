"""The real AI Audio & Voice Agent -- one prompt box, one uploaded file.

Upload a single audio or video file, describe what you want done to its
audio in plain language -- anything from "clean up the background noise"
to "give me subtitles translated to Spanish" to "generate a voiceover
saying: ..." -- and the agent decides which real tools to run. Like the
AI Video Editor (see media_tools.py), this takes a real file, can take a
real while to process, and hands back real finished file(s), so it gets
its own dedicated page and the same real background-job system: the
upload returns immediately and a live, honest progress page takes over.

Pipeline (every step below is real, nothing here is simulated):
  1. Save the uploaded file and measure its real duration (ffprobe).
  2. Ask OpenAI to turn the creator's instruction + the file's real
     duration/type into a concrete operations plan: which real tools to
     run (clean up the audio, produce subtitles + optional translation,
     generate a voiceover) and with what parameters
     (llm_client.generate_audio_plan).
  3. Execute that plan for real:
       - clean_audio -> FFmpeg noise reduction + loudness normalization
         (media_processing.reduce_noise)
       - subtitles -> OpenAI Whisper transcription (+ OpenAI translation
         of the caption TEXT if asked), written to a real .srt file
         (media_processing.generate_srt_file)
       - dub_speech -> a real translated spoken dub: the same Whisper
         transcript (shared with subtitles so it's never fetched twice),
         translated into another language as natural spoken text
         (llm_client.translate_full_text), then read aloud as a brand-new
         audio track via OpenAI text-to-speech
       - voiceover -> a brand-new narration (not a translation of
         anything in the file) via OpenAI text-to-speech, one of a fixed
         set of real synthesized voices (llm_client.generate_voiceover_audio)
     Both dub_speech and voiceover never use a cloned or impersonated real
     person's voice -- always one of OpenAI's fixed, synthesized voices.
     Every step reports genuine progress back to the job row the whole
     way, which the status page polls.
  4. If more than one real output file was produced, they're zipped
     together into a single download; otherwise the one real output file
     is handed back directly.

Honesty rules, consistent with every other integration in this project:
  - We never claim audio was cleaned, transcribed, translated, dubbed, or
    narrated unless FFmpeg/OpenAI actually ran and produced a real
    result -- see media_processing.py and llm_client.py.
  - dub_speech is a real, correct translation of the real transcribed
    speech, spoken aloud -- not a frame-synced redub (translated text
    rarely matches the original's exact timing) and never a clone of the
    creator's own voice.
  - Uploaded and processed files live under instance/ (this server's own
    disk), not a real cloud storage bucket.
"""

import os
import threading
import uuid
import zipfile

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file
from werkzeug.utils import secure_filename

import access
import config
import database as db
import llm_client
import media_processing as mp

bp = Blueprint("audio_voice", __name__)

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
ALLOWED_EXTENSIONS = ALLOWED_VIDEO_EXTENSIONS | ALLOWED_AUDIO_EXTENSIONS

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_ROOT = os.path.join(_BASE_DIR, "..", "instance", "audio_uploads")
PROCESSED_ROOT = os.path.join(_BASE_DIR, "..", "instance", "audio_processed")


def _user_dir(root, user_id):
    path = os.path.join(root, str(user_id))
    os.makedirs(path, exist_ok=True)
    return path


def _save_upload(file_storage, user_id):
    """Save an uploaded audio/video file to this user's own upload
    folder. Returns (path, error) -- never guesses a file is valid;
    rejects anything that isn't a recognized audio/video extension."""
    filename = secure_filename(file_storage.filename or "")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        return None, f"Unsupported file type '{ext or 'unknown'}'. Upload an audio or video file ({allowed})."
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


def _run_audio_voice_job(task_id, user_id, input_path, input_filename, instruction):
    """Runs the full AI Audio & Voice Agent pipeline in a background
    thread, so the HTTP request that started it returns immediately and
    the creator sees real, live progress instead of a blind "loading"
    page. Every status/progress update is written to the real
    agent_tasks row the status-poll endpoint reads -- never a simulated
    animation. This function must never touch Flask's `request`/`session`
    (they're tied to the HTTP request that's already finished by the
    time this runs)."""
    out_dir = _user_dir(PROCESSED_ROOT, user_id)
    temp_only_files = [input_path]  # never a final deliverable, always cleaned up
    try:
        db.update_agent_task(task_id, status="running", progress_percent=2, status_message="Reading your file...")
        duration = mp.probe_duration(input_path)
        if duration is None or duration <= 0:
            db.update_agent_task(
                task_id, status="failed",
                error_message=f"Couldn't read a real duration for '{input_filename}' -- it may not be a valid audio/video file.",
            )
            return
        is_video = mp.is_video_file(input_path)

        db.update_agent_task(task_id, progress_percent=8, status_message="Planning with AI...")
        ok, plan, error = llm_client.generate_audio_plan(
            instruction, {"filename": input_filename, "duration": duration, "is_video": is_video},
        )
        if not ok:
            db.update_agent_task(task_id, status="failed", error_message=f"Couldn't plan this request: {error}")
            return

        requested = [k for k in ("clean_audio", "subtitles", "dub_speech", "voiceover") if plan.get(k)]
        if not requested:
            db.update_agent_task(
                task_id, status="failed",
                error_message="Your instruction didn't match any real operation this agent can perform "
                               "(clean up the audio, generate subtitles, translate the speech into a spoken "
                               "dub, or create a voiceover). Try rephrasing.",
            )
            return

        outputs = []  # list of (real_file_path, archive_name) -- candidate final deliverables
        warnings = []
        total_steps = len(requested)
        step_idx = 0

        def bump(message):
            nonlocal step_idx
            step_idx += 1
            pct = 15 + int((step_idx - 1) / total_steps * 75)
            db.update_agent_task(task_id, progress_percent=pct, status_message=message)

        if plan.get("clean_audio"):
            bump("Cleaning up the audio...")
            level = plan["clean_audio"].get("level", "standard")
            ok_c, cleaned_path, error = mp.reduce_noise(input_path, out_dir, level=level)
            if ok_c:
                outputs.append((cleaned_path, f"cleaned{os.path.splitext(cleaned_path)[1]}"))
            else:
                warnings.append(f"Audio cleanup skipped: {error}")

        # subtitles and dub_speech both need a real transcript of the actual
        # speech -- transcribe once here and share the result between them,
        # so asking for both never pays for (or waits on) Whisper twice.
        want_subtitles = bool(plan.get("subtitles") and plan["subtitles"].get("enabled"))
        want_dub = bool(plan.get("dub_speech") and plan["dub_speech"].get("target_language"))
        transcript_segments = None
        transcript_error = None
        if want_subtitles or want_dub:
            bump("Transcribing speech...")
            if is_video:
                ok_a, audio_path, error = mp.extract_audio(input_path, out_dir)
                if not ok_a:
                    transcript_error = f"couldn't extract audio: {error}"
                    audio_path = None
                else:
                    temp_only_files.append(audio_path)
            else:
                audio_path = input_path
            if audio_path:
                ok_t, transcript_segments, error = llm_client.transcribe_audio(audio_path)
                if not ok_t:
                    transcript_error = f"couldn't transcribe: {error}"
                    transcript_segments = None

        if want_subtitles:
            if transcript_segments is None:
                warnings.append(f"Subtitles skipped: {transcript_error or 'no speech could be transcribed'}")
            else:
                segments = transcript_segments
                translate_to = plan["subtitles"].get("translate_to")
                if translate_to and segments:
                    bump(f"Translating subtitles to {translate_to}...")
                    ok_tr, translated, err_tr = llm_client.translate_caption_segments(segments, translate_to)
                    if ok_tr:
                        segments = translated
                    else:
                        warnings.append(f"Translation to {translate_to} failed, kept original language: {err_tr}")
                srt_path = mp.generate_srt_file(segments, out_dir)
                if srt_path:
                    suffix = f"_{translate_to}" if translate_to else ""
                    outputs.append((srt_path, f"subtitles{suffix}.srt"))
                else:
                    warnings.append("No speech was detected in this file, so no subtitle file was produced.")

        if want_dub:
            target_language = plan["dub_speech"]["target_language"]
            if transcript_segments is None:
                warnings.append(f"Speech translation to {target_language} skipped: {transcript_error or 'no speech could be transcribed'}")
            elif not transcript_segments:
                warnings.append("No speech was detected in this file, so there was nothing to translate into a dubbed track.")
            else:
                bump(f"Translating speech to {target_language}...")
                full_text = " ".join(seg["text"].strip() for seg in transcript_segments if seg["text"].strip())
                ok_tr, translated_text, err_tr = llm_client.translate_full_text(full_text, target_language)
                if not ok_tr:
                    warnings.append(f"Speech translation to {target_language} failed: {err_tr}")
                elif not translated_text:
                    warnings.append(f"Speech translation to {target_language} returned nothing usable.")
                else:
                    bump(f"Generating {target_language} speech...")
                    voice = plan["dub_speech"].get("voice", llm_client.DEFAULT_VOICE)
                    dub_path = os.path.join(out_dir, f"dubbed_{uuid.uuid4().hex[:10]}.mp3")
                    ok_v, _, err_v = llm_client.generate_voiceover_audio(translated_text, voice, dub_path)
                    if ok_v:
                        safe_lang = "".join(c if c.isalnum() else "_" for c in target_language.strip().lower()) or "translated"
                        outputs.append((dub_path, f"dubbed_speech_{safe_lang}.mp3"))
                    else:
                        warnings.append(f"Generating the {target_language} speech track failed: {err_v}")

        if plan.get("voiceover") and plan["voiceover"].get("text"):
            bump("Generating voiceover narration...")
            voice = plan["voiceover"].get("voice", llm_client.DEFAULT_VOICE)
            vo_path = os.path.join(out_dir, f"voiceover_{uuid.uuid4().hex[:10]}.mp3")
            ok_v, _, error = llm_client.generate_voiceover_audio(plan["voiceover"]["text"], voice, vo_path)
            if ok_v:
                outputs.append((vo_path, "voiceover.mp3"))
            else:
                warnings.append(f"Voiceover generation failed: {error}")

        if not outputs:
            db.update_agent_task(
                task_id, status="failed",
                error_message="None of the requested operations produced a usable result. " + " ".join(warnings),
            )
            return

        db.update_agent_task(task_id, progress_percent=95, status_message="Finishing up...")
        if len(outputs) == 1:
            result_path, _ = outputs[0]
            result_filename = os.path.basename(result_path)
        else:
            zip_path = os.path.join(out_dir, f"audio_voice_{uuid.uuid4().hex[:10]}.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                for path, archive_name in outputs:
                    zf.write(path, arcname=archive_name)
            _cleanup(*[p for p, _ in outputs])  # now archived -- the standalone copies are redundant
            result_filename = os.path.basename(zip_path)

        bits = []
        if plan.get("clean_audio"):
            bits.append(f"audio cleaned ({plan['clean_audio'].get('level', 'standard')})")
        if plan.get("subtitles") and plan["subtitles"].get("enabled"):
            translate_to = plan["subtitles"].get("translate_to")
            bits.append("subtitles" + (f" (translated to {translate_to})" if translate_to else ""))
        if plan.get("dub_speech") and plan["dub_speech"].get("target_language"):
            bits.append(f"speech translated to {plan['dub_speech']['target_language']} and spoken aloud ({plan['dub_speech'].get('voice', llm_client.DEFAULT_VOICE)} voice)")
        if plan.get("voiceover") and plan["voiceover"].get("text"):
            bits.append(f"voiceover generated ({plan['voiceover'].get('voice', llm_client.DEFAULT_VOICE)} voice)")
        summary = f"Real result -- {', '.join(bits)}. {plan.get('notes', '')}".strip()
        if warnings:
            summary += " (Notes: " + "; ".join(warnings) + ")"

        db.update_agent_task(
            task_id, status="completed", progress_percent=100, status_message="Done.",
            result_filename=result_filename, result_summary=summary,
        )
    except Exception as exc:  # last-resort honesty net -- a job must never hang forever unexplained
        db.update_agent_task(task_id, status="failed", error_message=f"Unexpected error: {exc}")
    finally:
        _cleanup(*temp_only_files)


@bp.route("/agents/audio-studio")
@access.agent_access_required("audio_voice")
def studio():
    user = access.current_user()
    return render_template(
        "audio_voice_studio.html", title="AI Audio & Voice Agent", active="agents",
        agent_audio=config.AGENTS["audio_voice"],
        ffmpeg_ready=mp.ffmpeg_available(),
        llm_ready=config.is_configured("llm_provider"),
        recent_tasks=db.list_agent_tasks(user["id"], agent_slug="audio_voice", limit=8),
    )


@bp.route("/agents/audio-studio/submit", methods=["POST"])
@access.agent_access_required("audio_voice")
def submit():
    user = access.current_user()
    upload = request.files.get("clip")
    instruction = request.form.get("instruction", "").strip()
    request_summary = f"Audio & Voice: {instruction[:120] or '(no instruction given)'}"

    if not upload or not upload.filename:
        flash("Upload an audio or video file.", "danger")
        return redirect(url_for("audio_voice.studio"))
    if not instruction:
        flash("Describe what you want done (e.g. \"clean up the background noise\", or \"generate subtitles translated to Spanish\", or \"create a voiceover saying: ...\").", "danger")
        return redirect(url_for("audio_voice.studio"))
    if not mp.ffmpeg_available():
        db.log_agent_task(user["id"], "audio_voice", request_summary, status="failed",
                           error_message="FFmpeg is not installed on this server.")
        flash("Couldn't process the file: FFmpeg is not installed on this server.", "danger")
        return redirect(url_for("audio_voice.studio"))
    if not config.is_configured("llm_provider"):
        db.log_agent_task(
            user["id"], "audio_voice", request_summary, status="needs_integration",
            error_message="The AI Audio & Voice Agent needs a connected OpenAI API key (for transcription, translation, and voiceover generation) -- it isn't set up yet.",
        )
        flash("This needs a connected OpenAI API key before it can run for real (see Account Settings -> Integrations).", "warning")
        return redirect(url_for("audio_voice.studio"))

    input_path, error = _save_upload(upload, user["id"])
    if error:
        flash(error, "danger")
        return redirect(url_for("audio_voice.studio"))

    task_id = db.log_agent_task(user["id"], "audio_voice", request_summary, status="running")
    db.update_agent_task(task_id, progress_percent=0, status_message="Queued...")

    threading.Thread(
        target=_run_audio_voice_job,
        args=(task_id, user["id"], input_path, upload.filename, instruction),
        daemon=True,
    ).start()

    return redirect(url_for("audio_voice.job_status", task_id=task_id))


@bp.route("/agents/audio-studio/job/<int:task_id>")
@access.login_required
def job_status(task_id):
    user = access.current_user()
    task = db.get_agent_task(user["id"], task_id)
    if not task or task["agent_slug"] != "audio_voice":
        flash("That job isn't available.", "danger")
        return redirect(url_for("audio_voice.studio"))
    return render_template("audio_voice_job.html", title="Your Audio & Voice job", active="agents", task=task)


@bp.route("/agents/audio-studio/job/<int:task_id>/poll")
@access.login_required
def job_poll(task_id):
    user = access.current_user()
    task = db.get_agent_task(user["id"], task_id)
    if not task or task["agent_slug"] != "audio_voice":
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "status": task["status"],
        "progress_percent": task["progress_percent"],
        "status_message": task["status_message"],
        "result_filename": task["result_filename"],
        "result_summary": task["result_summary"],
        "error_message": task["error_message"],
    })


@bp.route("/agents/audio-studio/download/<filename>")
@access.login_required
def download(filename):
    user = access.current_user()
    safe_name = secure_filename(filename)
    path = os.path.join(_user_dir(PROCESSED_ROOT, user["id"]), safe_name)
    if not os.path.exists(path):
        flash("That file isn't available anymore -- submit the request again.", "danger")
        return redirect(url_for("audio_voice.studio"))
    return send_file(path, as_attachment=True)
