"""Real local media processing via FFmpeg.

No external account or API key is needed for this -- FFmpeg is installed
directly on the server and we call it as a subprocess. This never
hardcodes a license key or claims a cloud video API is connected; it's
honest about exactly what it is: local, open-source, command-line
processing running on this box.

Design rules (same honesty pattern as llm_client.py):
  - We never fabricate a result. If FFmpeg isn't installed, or the command
    fails for any reason, we return a clear honest error -- never a fake
    "done" with no real file behind it.
  - Every public function returns (ok, output_path_or_None, error_message).
"""

import os
import shutil
import subprocess
import uuid

FFMPEG_TIMEOUT_SECONDS = 180

# Target resolution per platform. Width x height, portrait for
# Reels/TikTok/Shorts, square for feed posts, landscape for YouTube/wide.
PLATFORM_DIMENSIONS = {
    "reels_tiktok_shorts": (1080, 1920),
    "square_feed": (1080, 1080),
    "landscape_youtube": (1920, 1080),
}

# Color grading presets -> FFmpeg filter strings. These are modest,
# broadly-flattering adjustments -- an automatic "good enough" grade, not
# a claim of frame-perfect, shot-matched color correction the way a human
# colorist would do it.
COLOR_PRESETS = {
    "natural": "eq=contrast=1.05:brightness=0.01:saturation=1.05",
    "vibrant": "eq=contrast=1.12:brightness=0.02:saturation=1.35",
    "dark_cinematic": "eq=contrast=1.18:brightness=-0.04:saturation=0.9,curves=preset=darker",
    "documentary": "eq=contrast=1.02:brightness=0.0:saturation=0.95",
    "luxury": "eq=contrast=1.15:brightness=0.0:saturation=1.1,curves=preset=medium_contrast",
}


def ffmpeg_available():
    return shutil.which("ffmpeg") is not None


def _looks_like_video(path):
    return os.path.splitext(path)[1].lower() in {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}


def _run_ffmpeg(args, timeout=FFMPEG_TIMEOUT_SECONDS):
    """Run an ffmpeg command. Returns (ok, error_message_or_None)."""
    if not ffmpeg_available():
        return False, "FFmpeg is not installed on this server."
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", *args],
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


def trim_and_resize(input_path, output_dir, platform="reels_tiktok_shorts", start_seconds=None, end_seconds=None):
    """Video Editor: trim to a time range (optional) and resize/crop for a
    target platform's aspect ratio. Returns (ok, output_path, error).
    """
    if platform not in PLATFORM_DIMENSIONS:
        return False, None, f"Unknown platform '{platform}'."
    width, height = PLATFORM_DIMENSIONS[platform]
    output_path = os.path.join(output_dir, f"edited_{uuid.uuid4().hex[:10]}.mp4")

    args = []
    if start_seconds is not None:
        args += ["-ss", str(start_seconds)]
    args += ["-i", input_path]
    if end_seconds is not None and start_seconds is not None:
        duration = max(0.1, float(end_seconds) - float(start_seconds))
        args += ["-t", str(duration)]
    # Scale to cover the target box, then crop the overflow -- a standard
    # "fill and crop" approach so the output has no letterboxing bars.
    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    args += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", output_path]

    ok, error = _run_ffmpeg(args)
    if not ok:
        return False, None, error
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return False, None, "FFmpeg finished but produced no output file."
    return True, output_path, None


def apply_color_preset(input_path, output_dir, preset):
    """Color Grading: apply a named look to the whole clip."""
    if preset not in COLOR_PRESETS:
        return False, None, f"Unknown color preset '{preset}'."
    output_path = os.path.join(output_dir, f"graded_{uuid.uuid4().hex[:10]}.mp4")
    args = [
        "-i", input_path, "-vf", COLOR_PRESETS[preset],
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "copy",
        output_path,
    ]
    ok, error = _run_ffmpeg(args)
    if not ok:
        return False, None, error
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return False, None, "FFmpeg finished but produced no output file."
    return True, output_path, None


def reduce_noise(input_path, output_dir):
    """Audio & Voice: real FFT-based noise reduction on the audio track,
    using FFmpeg's afftdn filter. Works on a standalone audio file or a
    video file (keeps the video stream untouched, cleans the audio)."""
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
    used before sending audio to OpenAI for transcription/translation,
    since Whisper needs an audio file, not a full video."""
    output_path = os.path.join(output_dir, f"audio_{uuid.uuid4().hex[:10]}.mp3")
    args = ["-i", input_path, "-vn", "-acodec", "libmp3lame", "-q:a", "2", output_path]
    ok, error = _run_ffmpeg(args)
    if not ok:
        return False, None, error
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return False, None, "FFmpeg finished but produced no output file."
    return True, output_path, None
