"""Real local media processing via FFmpeg -- the actual execution engine
behind the AI Video Editor.

No external account or API key is needed for FFmpeg itself -- it's
installed directly on the server and run as a subprocess. OpenAI (Whisper
transcription + GPT edit planning, in llm_client.py) decides WHAT to do;
this file only does the real local video/audio WORK once that's decided.

Design rules (same honesty pattern as the rest of this project):
  - We never fabricate a result. If FFmpeg isn't installed, or a command
    fails for any reason, we return/report a clear, honest error -- never
    a fake "done" with no real file behind it.
  - Every public function returns (ok, output_path_or_None, error_message)
    unless noted otherwise.
  - Progress reporting is REAL: it comes from parsing FFmpeg's own
    `-progress` stream against the clip's actual measured duration, not a
    simulated animation -- see _run_ffmpeg_progress.
"""

import os
import shutil
import subprocess
import uuid

FFMPEG_TIMEOUT_SECONDS = 180
STEP_TIMEOUT_SECONDS = 600  # a heavier real step (transitions, a long clip) can take a while on a slow server

# Bundled directly in this project (static/fonts/) rather than relying on
# whatever fonts happen to be installed on the host server -- so caption/
# overlay text renders the same everywhere instead of silently failing on
# a server that doesn't have a particular font.
_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "fonts", "DejaVuSans-Bold.ttf")
_FONTS_DIR = os.path.dirname(_FONT_PATH)

# Target resolution per platform. Width x height, portrait for
# Reels/TikTok/Shorts, square for feed posts, landscape for YouTube/wide.
PLATFORM_DIMENSIONS = {
    "reels_tiktok_shorts": (1080, 1920),
    "square_feed": (1080, 1080),
    "landscape_youtube": (1920, 1080),
}

# Color grading presets -> FFmpeg filter strings. Modest, broadly-
# flattering adjustments -- an automatic "good enough" grade, not a claim
# of frame-perfect, shot-matched color correction a human colorist would do.
COLOR_PRESETS = {
    "natural": "eq=contrast=1.05:brightness=0.01:saturation=1.05",
    "vibrant": "eq=contrast=1.12:brightness=0.02:saturation=1.35",
    "dark_cinematic": "eq=contrast=1.18:brightness=-0.04:saturation=0.9,curves=preset=darker",
    "documentary": "eq=contrast=1.02:brightness=0.0:saturation=0.95",
    "luxury": "eq=contrast=1.15:brightness=0.0:saturation=1.1,curves=preset=medium_contrast",
}

# Real FFmpeg xfade transition styles this server's build supports,
# curated to a sensible subset for the AI to choose from.
TRANSITION_STYLES = {
    "none", "fade", "fadeblack", "wipeleft", "wiperight", "slideleft",
    "slideright", "circleopen", "dissolve", "smoothleft", "smoothright",
}

MIN_SPEED_FACTOR = 0.25
MAX_SPEED_FACTOR = 4.0


def ffmpeg_available():
    return shutil.which("ffmpeg") is not None


def _looks_like_video(path):
    return os.path.splitext(path)[1].lower() in {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}


def probe_duration(path):
    """Real duration of a media file in seconds, via ffprobe. Returns a
    float, or None if it can't be determined -- callers must treat None
    as "unknown", never guess a number."""
    if not shutil.which("ffprobe"):
        return None
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def _has_audio_stream(path):
    """Whether this file has a real audio track -- checked, not assumed,
    since silent raw footage is common and we don't want a crash when
    joining it with clips that do have sound."""
    if not shutil.which("ffprobe"):
        return True  # assume yes rather than silently drop audio elsewhere
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return True
    return bool(result.stdout.strip())


def _run_ffmpeg(args, timeout=FFMPEG_TIMEOUT_SECONDS):
    """Run a quick ffmpeg command with no live progress needed. Returns
    (ok, error_message_or_None)."""
    if not ffmpeg_available():
        return False, "FFmpeg is not installed on this server."
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "FFmpeg timed out while processing this file."
    except OSError as exc:
        return False, f"Couldn't run FFmpeg: {exc}"
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-8:])
        return False, f"FFmpeg reported an error:\n{tail}"
    return True, None


def _run_ffmpeg_progress(args, total_duration, on_progress=None, timeout=STEP_TIMEOUT_SECONDS):
    """Like _run_ffmpeg, but streams REAL progress while the command runs,
    by reading FFmpeg's own `-progress` output and comparing its advancing
    out_time against the real duration we expect this step to produce.
    on_progress, if given, is called with a float 0-100 as the process
    actually advances -- this is never a simulated animation. Returns
    (ok, error_message_or_None)."""
    if not ffmpeg_available():
        return False, "FFmpeg is not installed on this server."
    full_args = ["ffmpeg", "-y", "-loglevel", "error", *args, "-progress", "pipe:1", "-nostats"]
    try:
        proc = subprocess.Popen(full_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    except OSError as exc:
        return False, f"Couldn't run FFmpeg: {exc}"

    last_reported = -1
    try:
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_ms="):
                try:
                    out_time_s = int(line.split("=", 1)[1]) / 1_000_000
                    if total_duration and total_duration > 0:
                        pct = max(0.0, min(99.0, out_time_s / total_duration * 100))
                        if on_progress and int(pct) != last_reported:
                            last_reported = int(pct)
                            on_progress(pct)
                except (ValueError, IndexError):
                    pass
        try:
            stderr_output = proc.stderr.read()
        except Exception:
            stderr_output = ""
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return False, "FFmpeg timed out while processing this file."

    if proc.returncode != 0:
        tail = "\n".join((stderr_output or "").strip().splitlines()[-8:])
        return False, f"FFmpeg reported an error:\n{tail}"
    if on_progress:
        on_progress(100.0)
    return True, None


def _cleanup_paths(paths):
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


# ---------------------------------------------------------------------
# Captions: timeline remapping, SRT writing, drawtext escaping
# ---------------------------------------------------------------------

def remap_transcripts_to_timeline(segments, transcripts_by_clip):
    """Remap each clip's original transcript timestamps onto the NEW,
    assembled-output timeline (the order/cuts in `segments`), keeping only
    the parts that actually survive the cut. This is what makes burned-in
    captions line up with the real, edited video instead of the original
    raw footage. Returns a list of {"start","end","text"} on that new
    ("assembled", pre-speed-change) timeline."""
    entries = []
    offset = 0.0
    for seg in segments:
        clip_transcript = transcripts_by_clip.get(seg["clip_index"], [])
        for t in clip_transcript:
            overlap_start = max(t["start"], seg["start"])
            overlap_end = min(t["end"], seg["end"])
            if overlap_end > overlap_start:
                entries.append({
                    "start": offset + (overlap_start - seg["start"]),
                    "end": offset + (overlap_end - seg["start"]),
                    "text": t["text"],
                })
        offset += seg["end"] - seg["start"]
    return entries


def _srt_timestamp(seconds):
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _write_srt(caption_entries, output_dir):
    entries = [e for e in caption_entries if e["end"] > e["start"] and e["text"].strip()]
    if not entries:
        return None
    entries.sort(key=lambda e: e["start"])
    srt_path = os.path.join(output_dir, f"captions_{uuid.uuid4().hex[:10]}.srt")
    lines = []
    for i, e in enumerate(entries, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(e['start'])} --> {_srt_timestamp(e['end'])}")
        lines.append(e["text"].strip())
        lines.append("")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return srt_path


def _escape_for_filter_path(path):
    # ffmpeg's filtergraph option parser treats ':' and '\' specially even
    # inside a filename argument -- escape them so a path is never
    # misread as extra filter options.
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


# ---------------------------------------------------------------------
# Speed change
# ---------------------------------------------------------------------

def _atempo_chain(factor):
    """atempo's single-instance range is narrower than the overall speed
    range we allow, so chain multiple stages to reach any factor in
    [MIN_SPEED_FACTOR, MAX_SPEED_FACTOR] safely."""
    factor = max(MIN_SPEED_FACTOR, min(MAX_SPEED_FACTOR, factor))
    stages = []
    remaining = factor
    while remaining > 2.0:
        stages.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        stages.append(0.5)
        remaining /= 0.5
    stages.append(remaining)
    return ",".join(f"atempo={s:.4f}" for s in stages)


def _change_speed(input_path, output_dir, factor, on_progress=None):
    factor = max(MIN_SPEED_FACTOR, min(MAX_SPEED_FACTOR, factor))
    output_path = os.path.join(output_dir, f"_speed_{uuid.uuid4().hex[:10]}.mp4")
    pts_multiplier = 1.0 / factor
    args = [
        "-i", input_path,
        "-vf", f"setpts={pts_multiplier:.6f}*PTS",
        "-af", _atempo_chain(factor),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac",
        output_path,
    ]
    orig_duration = probe_duration(input_path) or 0
    expected_duration = orig_duration / factor if orig_duration else None
    ok, error = _run_ffmpeg_progress(args, expected_duration, on_progress)
    if not ok:
        return False, None, error
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return False, None, "FFmpeg finished but produced no output file."
    return True, output_path, None


# ---------------------------------------------------------------------
# Cutting, normalizing, and (optionally transition-blended) concatenation
# ---------------------------------------------------------------------

def _normalize_segment(source_path, seg, width, height, output_dir, work_id, index):
    """Cut one chosen time range out of one source clip, and re-encode it
    to identical codec/fps/resolution/audio format so every segment can be
    joined cleanly afterwards -- regardless of what the raw footage
    originally looked like."""
    duration = max(0.1, seg["end"] - seg["start"])
    seg_out = os.path.join(output_dir, f"_seg_{work_id}_{index}.mp4")
    has_audio = _has_audio_stream(source_path)
    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps=30"
    args = ["-ss", str(seg["start"]), "-i", source_path]
    if not has_audio:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    args += ["-t", str(duration), "-vf", vf, "-map", "0:v:0"]
    args += ["-map", "0:a:0?"] if has_audio else ["-map", "1:a:0"]
    args += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", "-r", "30",
        seg_out,
    ]
    ok, error = _run_ffmpeg(args, timeout=STEP_TIMEOUT_SECONDS)
    if not ok or not os.path.exists(seg_out) or os.path.getsize(seg_out) == 0:
        return None, error or "FFmpeg produced no output."
    return seg_out, None


def _concat_plain(segment_paths, output_dir, work_id):
    """Fast path: plain hard cuts, stream-copy concat (no re-encode)."""
    concat_list_path = os.path.join(output_dir, f"_concat_{work_id}.txt")
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for p in segment_paths:
            f.write(f"file '{p}'\n")
    output_path = os.path.join(output_dir, f"_rough_{work_id}.mp4")
    ok, error = _run_ffmpeg(
        ["-f", "concat", "-safe", "0", "-i", concat_list_path, "-c", "copy", output_path],
        timeout=STEP_TIMEOUT_SECONDS,
    )
    _cleanup_paths([concat_list_path])
    if not ok or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return None, error or "FFmpeg produced no output."
    return output_path, None


def _concat_with_transitions(segment_paths, segment_durations, style, requested_duration,
                              output_dir, work_id, on_progress=None):
    """Real path: cross-fade (or wipe/dissolve/etc.) transitions between
    cuts, using FFmpeg's own xfade (video) + acrossfade (audio) filters --
    not a fake dissolve, an actual blended re-encode."""
    style = style if style in TRANSITION_STYLES and style != "none" else "fade"
    n = len(segment_paths)
    output_path = os.path.join(output_dir, f"_rough_{work_id}.mp4")

    args = []
    for p in segment_paths:
        args += ["-i", p]

    filter_parts = []
    v_label, a_label = "0:v", "0:a"
    running_duration = segment_durations[0]
    for i in range(1, n):
        t_i = max(0.1, min(requested_duration, 0.4 * segment_durations[i - 1], 0.4 * segment_durations[i]))
        offset = max(0.0, running_duration - t_i)
        out_v, out_a = f"v{i}", f"a{i}"
        filter_parts.append(f"[{v_label}][{i}:v]xfade=transition={style}:duration={t_i:.3f}:offset={offset:.3f}[{out_v}]")
        filter_parts.append(f"[{a_label}][{i}:a]acrossfade=d={t_i:.3f}[{out_a}]")
        v_label, a_label = out_v, out_a
        running_duration = running_duration + segment_durations[i] - t_i

    args += [
        "-filter_complex", ";".join(filter_parts),
        "-map", f"[{v_label}]", "-map", f"[{a_label}]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac",
        output_path,
    ]
    ok, error = _run_ffmpeg_progress(args, running_duration, on_progress, timeout=STEP_TIMEOUT_SECONDS)
    if not ok or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return None, error or "FFmpeg produced no output."
    return output_path, None


# ---------------------------------------------------------------------
# The real, enforced execution pipeline for a prompt-driven edit plan
# ---------------------------------------------------------------------

def execute_edit_plan(clip_paths, plan, output_dir, platform="reels_tiktok_shorts",
                       caption_entries=None, on_progress=None):
    """Runs the actual edit described by `plan` (see
    llm_client.generate_edit_plan for its shape): cut & reassemble clips
    (with transitions if asked) -> speed change (if asked) -> color grade
    (if asked) -> burn in captions (if asked; `caption_entries` is the
    final, already-translated-if-needed list the caller computed from the
    real transcripts) -> text/quote overlay (if asked). Every step is
    genuine FFmpeg work. on_progress(percent, message), if given, is
    called with real, measured progress as each step actually runs.

    Returns (ok, output_path, error, notes) -- notes is a list of
    non-fatal warnings (e.g. a cosmetic step that failed) so one minor
    step's failure never throws away an otherwise-real, finished edit.
    """
    if platform not in PLATFORM_DIMENSIONS:
        return False, None, f"Unknown platform '{platform}'.", []
    width, height = PLATFORM_DIMENSIONS[platform]
    segments = plan["trim_and_reorder"]["segments"]
    if not segments:
        return False, None, "The edit plan had no usable segments to assemble.", []

    notes = []
    work_id = uuid.uuid4().hex[:10]
    temp_paths = []

    def report(step_base, step_weight, total_weight, message):
        def _cb(pct):
            if on_progress:
                on_progress(step_base + (pct / 100.0) * (step_weight / total_weight * 100.0), message)
        return _cb

    # Work out which optional steps are active, to weight progress honestly.
    transition = plan["trim_and_reorder"].get("transition")
    has_transition = bool(transition and transition.get("style") not in (None, "none") and len(segments) > 1)
    speed_plan = plan.get("change_speed")
    color_plan = plan.get("color_grade")
    captions_plan = plan.get("captions")
    want_captions = bool(captions_plan and captions_plan.get("enabled") and caption_entries is not None)
    overlay_plan = plan.get("text_overlay")

    step_weights = [("cut", 35 if (has_transition or len(segments) > 1) else 12)]
    if speed_plan:
        step_weights.append(("speed", 15))
    if color_plan:
        step_weights.append(("color", 18))
    if want_captions:
        step_weights.append(("captions", 18))
    if overlay_plan:
        step_weights.append(("overlay", 12))
    total_weight = sum(w for _, w in step_weights)
    base = 0.0

    try:
        # --- Step: cut, normalize, (optionally) transition-blend, concat ---
        step_weight = dict(step_weights)["cut"]
        if on_progress:
            on_progress(base, "Cutting your clips...")
        seg_paths = []
        seg_durations = []
        for i, seg in enumerate(segments):
            if on_progress:
                on_progress(base + (i / max(1, len(segments))) * (step_weight / total_weight * 100 * 0.6),
                            f"Cutting clip {i + 1} of {len(segments)}...")
            seg_path, error = _normalize_segment(clip_paths[seg["clip_index"]], seg, width, height, output_dir, work_id, i)
            if seg_path is None:
                _cleanup_paths(temp_paths)
                return False, None, f"Couldn't cut segment {i + 1} of {len(segments)}: {error}", notes
            temp_paths.append(seg_path)
            seg_paths.append(seg_path)
            seg_durations.append(seg["end"] - seg["start"])

        if len(seg_paths) == 1:
            rough_cut_path = seg_paths[0]
        elif has_transition:
            rough_cut_path, error = _concat_with_transitions(
                seg_paths, seg_durations, transition.get("style", "fade"),
                float(transition.get("duration", 0.4)), output_dir, work_id,
                on_progress=report(base + step_weight / total_weight * 100 * 0.6,
                                    step_weight * 0.4, total_weight, "Blending transitions between your clips..."),
            )
            if rough_cut_path is None:
                _cleanup_paths(temp_paths)
                return False, None, f"Couldn't join the clips with transitions: {error}", notes
        else:
            rough_cut_path, error = _concat_plain(seg_paths, output_dir, work_id)
            if rough_cut_path is None:
                _cleanup_paths(temp_paths)
                return False, None, f"Couldn't join the cut segments together: {error}", notes
        if rough_cut_path not in temp_paths:
            temp_paths.append(rough_cut_path)
        current_path = rough_cut_path
        base += step_weight / total_weight * 100
        current_duration = probe_duration(current_path) or sum(seg_durations)
        speed_factor = 1.0

        # --- Step: speed change ---
        if speed_plan:
            step_weight = dict(step_weights)["speed"]
            speed_factor = max(MIN_SPEED_FACTOR, min(MAX_SPEED_FACTOR, float(speed_plan.get("factor", 1.0))))
            ok_speed, sped_path, error = _change_speed(
                current_path, output_dir, speed_factor,
                on_progress=report(base, step_weight, total_weight, f"Adjusting speed ({speed_factor:.2f}x)..."),
            )
            if not ok_speed:
                notes.append(f"Speed change skipped: {error}")
            else:
                temp_paths.append(sped_path)
                current_path = sped_path
                current_duration = probe_duration(current_path) or (current_duration / speed_factor)
            base += step_weight / total_weight * 100

        # --- Step: color grade ---
        if color_plan:
            step_weight = dict(step_weights)["color"]
            preset = color_plan.get("preset", "natural")
            if preset not in COLOR_PRESETS:
                notes.append(f"Color grade skipped: unknown preset '{preset}'.")
            else:
                graded_path = os.path.join(output_dir, f"_graded_{work_id}.mp4")
                ok, error = _run_ffmpeg_progress(
                    ["-i", current_path, "-vf", COLOR_PRESETS[preset],
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "copy", graded_path],
                    current_duration, report(base, step_weight, total_weight, f"Applying '{preset}' color grade..."),
                )
                if ok and os.path.exists(graded_path) and os.path.getsize(graded_path) > 0:
                    temp_paths.append(graded_path)
                    current_path = graded_path
                else:
                    notes.append(f"Color grade skipped: {error}")
            base += step_weight / total_weight * 100

        # --- Step: burn in captions (already translated by the caller, if asked) ---
        if want_captions:
            step_weight = dict(step_weights)["captions"]
            rescaled = [
                {"start": e["start"] / speed_factor, "end": e["end"] / speed_factor, "text": e["text"]}
                for e in caption_entries
            ]
            srt_path = _write_srt(rescaled, output_dir)
            if srt_path:
                temp_paths.append(srt_path)
                captioned_path = os.path.join(output_dir, f"_captioned_{work_id}.mp4")
                style = "FontName=DejaVu Sans,FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=70"
                subtitles_filter = (
                    f"subtitles={_escape_for_filter_path(srt_path)}:"
                    f"fontsdir={_escape_for_filter_path(_FONTS_DIR)}:force_style='{style}'"
                )
                ok, error = _run_ffmpeg_progress(
                    ["-i", current_path, "-vf", subtitles_filter,
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "copy", captioned_path],
                    current_duration, report(base, step_weight, total_weight, "Burning in captions..."),
                )
                if ok and os.path.exists(captioned_path) and os.path.getsize(captioned_path) > 0:
                    temp_paths.append(captioned_path)
                    current_path = captioned_path
                else:
                    notes.append(f"Captions skipped: {error}")
            base += step_weight / total_weight * 100

        # --- Step: text/quote overlay ---
        if overlay_plan and overlay_plan.get("text"):
            step_weight = dict(step_weights)["overlay"]
            q_start = overlay_plan.get("start", 0.0) / speed_factor
            q_end = overlay_plan.get("end", q_start + 3.0) / speed_factor
            quote_txt_path = os.path.join(output_dir, f"_overlay_{work_id}.txt")
            with open(quote_txt_path, "w", encoding="utf-8") as f:
                f.write(overlay_plan["text"])
            temp_paths.append(quote_txt_path)
            overlaid_path = os.path.join(output_dir, f"_overlaid_{work_id}.mp4")
            drawtext_filter = (
                f"drawtext=fontfile={_escape_for_filter_path(_FONT_PATH)}:"
                f"textfile={_escape_for_filter_path(quote_txt_path)}:"
                "fontsize=52:fontcolor=white:line_spacing=6:"
                "box=1:boxcolor=black@0.55:boxborderw=24:"
                "x=(w-text_w)/2:y=h*0.12:"
                f"enable='between(t,{max(0.0, q_start):.3f},{max(q_start + 0.1, q_end):.3f})'"
            )
            ok, error = _run_ffmpeg_progress(
                ["-i", current_path, "-vf", drawtext_filter,
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "copy", overlaid_path],
                current_duration, report(base, step_weight, total_weight, "Adding text overlay..."),
            )
            if ok and os.path.exists(overlaid_path) and os.path.getsize(overlaid_path) > 0:
                temp_paths.append(overlaid_path)
                current_path = overlaid_path
            else:
                notes.append(f"Text overlay skipped: {error}")
            base += step_weight / total_weight * 100

        final_path = os.path.join(output_dir, f"edit_{work_id}.mp4")
        if current_path == final_path:
            pass
        elif current_path in temp_paths:
            shutil.copyfile(current_path, final_path)
        else:
            shutil.move(current_path, final_path)
        temp_paths = [p for p in temp_paths if p != final_path]
        _cleanup_paths(temp_paths)
        if on_progress:
            on_progress(100.0, "Done.")
        return True, final_path, None, notes
    except Exception as exc:  # last-resort honesty net -- never leave a job stuck silently
        _cleanup_paths(temp_paths)
        return False, None, f"Unexpected error while assembling the edit: {exc}", notes


# ---------------------------------------------------------------------
# Standalone utilities also used for transcription prep / future agents
# ---------------------------------------------------------------------

def reduce_noise(input_path, output_dir):
    """Audio & Voice (future): real FFT-based noise reduction on the audio
    track, using FFmpeg's afftdn filter. Works on a standalone audio file
    or a video file (keeps the video stream untouched, cleans the audio)."""
    ext = os.path.splitext(input_path)[1] or ".mp3"
    output_path = os.path.join(output_dir, f"denoised_{uuid.uuid4().hex[:10]}{ext}")
    args = ["-i", input_path, "-af", "afftdn=nf=-25"]
    if _looks_like_video(input_path):
        args += ["-c:v", "copy"]
    args += [output_path]
    ok, error = _run_ffmpeg(args)
    if not ok:
        return False, None, error
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return False, None, "FFmpeg finished but produced no output file."
    return True, output_path, None


def extract_audio(input_path, output_dir):
    """Pull the audio track out of a video file as a standalone .mp3 --
    used before sending audio to OpenAI for transcription."""
    output_path = os.path.join(output_dir, f"audio_{uuid.uuid4().hex[:10]}.mp3")
    args = ["-i", input_path, "-vn", "-acodec", "libmp3lame", "-q:a", "2", output_path]
    ok, error = _run_ffmpeg(args)
    if not ok:
        return False, None, error
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return False, None, "FFmpeg finished but produced no output file."
    return True, output_path, None
