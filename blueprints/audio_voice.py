"""Routes for the AI Audio & Voice Agent.

One upload box, one free-text instruction. A small LLM-driven planner
decides which real operations to run against the file:

  - clean_audio   -- FFmpeg-based noise reduction
  - subtitles     -- Whisper transcription -> .srt file (optionally
                      translated to another language, as on-screen text)
  - dub_speech    -- Whisper transcription -> full-text translation of
                      what was actually said -> OpenAI TTS of the
                      translation, producing a new spoken audio track.
                      This is a real translated dub: the words come from
                      transcribing the uploaded file, not from a typed
                      script. The voice is always one of OpenAI's fixed
                      synthesized voices -- never a clone of the
                      original speaker's voice -- and it is not
                      frame-perfectly lip-synced, since a translation
                      rarely matches the original's exact timing.
  - voiceover     -- OpenAI TTS of text the user typed/asked for directly

When both "subtitles" and "dub_speech" are requested in the same job,
the audio is transcribed with Whisper only once and the resulting
transcript is shared between the two operations, instead of paying for
and waiting on two separate transcription calls.

Every operation only runs if the planner's output says the user's
instruction actually asked for it, and every failure (no FFmpeg, no
OpenAI key, no speech detected, nothing to translate, etc.) is reported
honestly -- nothing here fabricates a result.
"""
import os
import threading
import uuid
import zipfile
from datetime import datetime, timezone

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, send_from_directory

import llm_client
import media_processing
from auth import login_required
from db import get_db

audio_voice_bp = Blueprint("audio_voice", __name__, url_prefix="/agents/audio-voice")

ALLOWED_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac",
    ".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv",
}


def _uploads_dir():
    path = os.path.join(current_app.instance_path, "audio_voice_uploads")
    os.makedirs(path, exist_ok=True)
    return path


def _outputs_dir():
    path = os.path.join(current_app.instance_path, "audio_voice_outputs")
    os.makedirs(path, exist_ok=True)
    return path


@audio_voice_bp.route("", methods=["GET"])
@login_required
def studio():
    db = get_db()
    ffmpeg_ready = media_processing.ffmpeg_available()
    llm_ready = llm_client.is_configured(session["user_id"])
    recent_tasks = db.execute(
        "SELECT * FROM agent_tasks WHERE user_id = ? AND agent_slug = 'audio_voice' "
        "ORDER BY created_at DESC LIMIT 5",
        (session["user_id"],),
    ).fetchall()
    return render_template(
        "audio_voice_studio.html",
        ffmpeg_ready=ffmpeg_ready,
        llm_ready=llm_ready,
        recent_tasks=recent_tasks,
    )


@audio_voice_bp.route("/submit", methods=["POST"])
@login_required
def submit():
    instruction = (request.form.get("instruction") or "").strip()
    clip = request.files.get("clip")

    if not instruction:
        flash("Describe what you want done to the file.", "error")
        return redirect(url_for("audio_voice.studio"))

    if not clip or not clip.filename:
        flash("Upload an audio or video file first.", "error")
        return redirect(url_for("audio_voice.studio"))

    ext = os.path.splitext(clip.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        flash(f"Unsupported file type: {ext or '(none)'}. Try one of: {', '.join(sorted(ALLOWED_EXTENSIONS))}", "error")
        return redirect(url_for("audio_voice.studio"))

    if not media_processing.ffmpeg_available():
        flash("FFmpeg isn't installed on this server, so no file can be processed yet.", "error")
        return redirect(url_for("audio_voice.studio"))

    if not llm_client.is_configured(session["user_id"]):
        flash("Connect your OpenAI API key in Account Settings first.", "error")
        return redirect(url_for("audio_voice.studio"))

    task_id = str(uuid.uuid4())
    upload_name = f"{task_id}{ext}"
    upload_path = os.path.join(_uploads_dir(), upload_name)
    clip.save(upload_path)

    db = get_db()
    db.execute(
        "INSERT INTO agent_tasks (id, user_id, agent_slug, status, request_summary, created_at) "
        "VALUES (?, ?, 'audio_voice', 'running', ?, ?)",
        (task_id, session["user_id"], instruction[:140], datetime.now(timezone.utc).isoformat()),
    )
    db.commit()

    app = current_app._get_current_object()
    user_id = session["user_id"]
    thread = threading.Thread(
        target=_run_job,
        args=(app, task_id, user_id, upload_path, instruction),
        daemon=True,
    )
    thread.start()

    return redirect(url_for("audio_voice.job_status", task_id=task_id))


@audio_voice_bp.route("/job/<task_id>", methods=["GET"])
@login_required
def job_status(task_id):
    db = get_db()
    task = db.execute(
        "SELECT * FROM agent_tasks WHERE id = ? AND user_id = ? AND agent_slug = 'audio_voice'",
        (task_id, session["user_id"]),
    ).fetchone()
    if not task:
        flash("That job wasn't found.", "error")
        return redirect(url_for("audio_voice.studio"))
    return render_template("audio_voice_job.html", task=task)


@audio_voice_bp.route("/download/<task_id>", methods=["GET"])
@login_required
def download(task_id):
    db = get_db()
    task = db.execute(
        "SELECT * FROM agent_tasks WHERE id = ? AND user_id = ? AND agent_slug = 'audio_voice' AND status = 'completed'",
        (task_id, session["user_id"]),
    ).fetchone()
    if not task or not task["result_path"]:
        flash("That file isn't available.", "error")
        return redirect(url_for("audio_voice.studio"))
    directory, filename = os.path.split(task["result_path"])
    return send_from_directory(directory, filename, as_attachment=True)


def _run_job(app, task_id, user_id, upload_path, instruction):
    with app.app_context():
        db = get_db()
        try:
            _process(db, task_id, user_id, upload_path, instruction)
        except Exception as exc:  # noqa: BLE001 -- last-resort honesty net
            db.execute(
                "UPDATE agent_tasks SET status = 'failed', error_message = ? WHERE id = ?",
                (f"Unexpected error: {exc}", task_id),
            )
            db.commit()


def _process(db, task_id, user_id, upload_path, instruction):
    is_video = media_processing.is_video_file(upload_path)

    ok, plan, plan_error = llm_client.generate_audio_plan(user_id, instruction, is_video=is_video)
    if not ok:
        db.execute(
            "UPDATE agent_tasks SET status = 'failed', error_message = ? WHERE id = ?",
            (plan_error, task_id),
        )
        db.commit()
        return

    requested = [k for k in ("clean_audio", "subtitles", "dub_speech", "voiceover") if plan.get(k)]
    if not requested:
        db.execute(
            "UPDATE agent_tasks SET status = 'failed', error_message = ? WHERE id = ?",
            (
                "Your instruction didn't match any real operation this agent can perform "
                "(clean up the audio, generate subtitles, translate the speech into a spoken "
                "dub, or create a voiceover). Try rephrasing.",
                task_id,
            ),
        )
        db.commit()
        return

    outputs_dir = _outputs_dir()
    produced_files = []
    bits = []

    # --- clean_audio ---
    if plan.get("clean_audio"):
        level = plan["clean_audio"].get("level", "medium")
        cleaned_path = os.path.join(outputs_dir, f"cleaned_{task_id}{os.path.splitext(upload_path)[1]}")
        ok, err = media_processing.reduce_noise(upload_path, cleaned_path, level=level)
        if not ok:
            db.execute(
                "UPDATE agent_tasks SET status = 'failed', error_message = ? WHERE id = ?",
                (f"Noise reduction failed: {err}", task_id),
            )
            db.commit()
            return
        produced_files.append(cleaned_path)
        bits.append(f"cleaned audio ({level} noise reduction)")

    # --- shared transcription for subtitles + dub_speech ---
    want_subtitles = bool(plan.get("subtitles"))
    want_dub = bool(plan.get("dub_speech"))
    transcript_segments = None
    transcript_error = None

    if want_subtitles or want_dub:
        ok, transcript_segments, transcript_error = llm_client.transcribe_audio(user_id, upload_path)
        if not ok:
            db.execute(
                "UPDATE agent_tasks SET status = 'failed', error_message = ? WHERE id = ?",
                (f"Transcription failed: {transcript_error}", task_id),
            )
            db.commit()
            return
        if not transcript_segments:
            db.execute(
                "UPDATE agent_tasks SET status = 'failed', error_message = ? WHERE id = ?",
                ("No speech was detected in this file, so subtitles/translation can't be generated.", task_id),
            )
            db.commit()
            return

    # --- subtitles ---
    if want_subtitles:
        sub_plan = plan["subtitles"]
        segments_for_srt = transcript_segments
        target_lang = sub_plan.get("translate_to")
        if target_lang:
            ok, translated_segments, err = llm_client.translate_caption_segments(
                user_id, transcript_segments, target_lang
            )
            if not ok:
                db.execute(
                    "UPDATE agent_tasks SET status = 'failed', error_message = ? WHERE id = ?",
                    (f"Subtitle translation failed: {err}", task_id),
                )
                db.commit()
                return
            segments_for_srt = translated_segments

        srt_path = os.path.join(outputs_dir, f"subtitles_{task_id}.srt")
        media_processing.generate_srt_file(segments_for_srt, srt_path)
        produced_files.append(srt_path)
        if target_lang:
            bits.append(f"subtitles translated to {target_lang}")
        else:
            bits.append("subtitles")

    # --- dub_speech: real speech-to-speech translation ---
    if want_dub:
        dub_plan = plan["dub_speech"]
        target_lang = dub_plan["target_language"]
        voice = dub_plan.get("voice", llm_client.DEFAULT_VOICE)

        full_transcript_text = " ".join(seg.get("text", "").strip() for seg in transcript_segments).strip()
        if not full_transcript_text:
            db.execute(
                "UPDATE agent_tasks SET status = 'failed', error_message = ? WHERE id = ?",
                ("There was no actual speech transcribed in this file to translate.", task_id),
            )
            db.commit()
            return

        ok, translated_text, err = llm_client.translate_full_text(full_transcript_text, target_lang)
        if not ok:
            db.execute(
                "UPDATE agent_tasks SET status = 'failed', error_message = ? WHERE id = ?",
                (f"Speech translation failed: {err}", task_id),
            )
            db.commit()
            return

        dubbed_path = os.path.join(outputs_dir, f"dubbed_{task_id}.mp3")
        ok, err = llm_client.generate_voiceover_audio(user_id, translated_text, voice, dubbed_path)
        if not ok:
            db.execute(
                "UPDATE agent_tasks SET status = 'failed', error_message = ? WHERE id = ?",
                (f"Dubbed voice generation failed: {err}", task_id),
            )
            db.commit()
            return

        safe_lang = "".join(c if c.isalnum() else "_" for c in target_lang.lower())
        archived_dub_path = os.path.join(outputs_dir, f"dubbed_speech_{safe_lang}_{task_id}.mp3")
        os.replace(dubbed_path, archived_dub_path)
        produced_files.append(archived_dub_path)
        bits.append(
            f"a real spoken translation into {target_lang} (transcribed from what was actually said, "
            f"then translated and voiced in a synthesized '{voice}' voice -- not a clone of the original speaker)"
        )

    # --- voiceover ---
    if plan.get("voiceover"):
        vo_plan = plan["voiceover"]
        text = vo_plan["text"]
        voice = vo_plan.get("voice", llm_client.DEFAULT_VOICE)
        vo_path = os.path.join(outputs_dir, f"voiceover_{task_id}.mp3")
        ok, err = llm_client.generate_voiceover_audio(user_id, text, voice, vo_path)
        if not ok:
            db.execute(
                "UPDATE agent_tasks SET status = 'failed', error_message = ? WHERE id = ?",
                (f"Voiceover generation failed: {err}", task_id),
            )
            db.commit()
            return
        produced_files.append(vo_path)
        bits.append(f"a voiceover ({voice} voice)")

    if not produced_files:
        db.execute(
            "UPDATE agent_tasks SET status = 'failed', error_message = ? WHERE id = ?",
            ("No output was produced -- nothing to deliver.", task_id),
        )
        db.commit()
        return

    if len(produced_files) == 1:
        final_path = produced_files[0]
    else:
        final_path = os.path.join(outputs_dir, f"result_{task_id}.zip")
        with zipfile.ZipFile(final_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in produced_files:
                zf.write(f, arcname=os.path.basename(f))

    summary = "Done: " + "; ".join(bits) + "."
    db.execute(
        "UPDATE agent_tasks SET status = 'completed', result_path = ?, result_summary = ? WHERE id = ?",
        (final_path, summary, task_id),
    )
    db.commit()
