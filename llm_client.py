"""Real integration with an external language-model API (OpenAI).

Shared by every agent that calls OpenAI for real (Scriptwriter, Trend
Researcher, Ads Manager, Social Media Manager, Performance Analyst,
Campaign Planner, AI Video Editor, and any future one) -- each agent gets
its own system prompt below, but the text-generating ones all go through
the same honest call-and-error-handling path in `_chat_completion`. The
AI Video Editor also uses `transcribe_audio` (OpenAI's Whisper API, real
speech-to-text with timestamps), `generate_edit_plan` (turns a free-text
instruction + each clip's real duration/transcript into a machine-
readable operations plan -- which real tools to run and with what
parameters -- that media_processing.py then executes with FFmpeg), and
`translate_caption_segments` (translates already-transcribed caption text
into another language when asked, keeping timing untouched).

Design rules this file follows, straight from the project spec:
  - The API key is NEVER hardcoded. It's read from the OPENAI_API_KEY
    environment variable only (see config.INTEGRATIONS["llm_provider"]).
    If it isn't set, is_configured() already stops this file from being
    called at all -- the caller shows "setup required" instead.
  - We never fabricate a result. If the call fails for any reason (bad
    key, no quota, network issue, unexpected response), we return a clear
    honest error instead of inventing text.
  - This talks to OpenAI directly over HTTPS using the `requests` library
    and OpenAI's plain REST API -- no OpenAI SDK package required.
"""

import json
import os
import requests

import media_processing as mp

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
MODEL = "gpt-4o-mini"  # inexpensive, good-quality model -- stretches a small API budget
TRANSCRIPTION_MODEL = "whisper-1"
TTS_MODEL = "tts-1"
REQUEST_TIMEOUT_SECONDS = 45
TRANSCRIPTION_TIMEOUT_SECONDS = 120  # audio upload + transcription takes longer than a chat call
TTS_TIMEOUT_SECONDS = 90

# OpenAI's own fixed, synthesized text-to-speech voices -- never a cloned
# or impersonated real person's voice. This is the full set of "authorized
# voices" the AI Audio & Voice Agent is allowed to pick from.
VOICE_OPTIONS = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}
DEFAULT_VOICE = "alloy"

SCRIPTWRITER_SYSTEM_PROMPT = """You are the AI Scriptwriter inside Creator Hub AI, a content-creation \
platform for a combat-sports athlete and content creator. Given a creator's request, \
write a ready-to-film short-form video script.

Always structure your response with these exact sections, in this order, using these \
exact headings:

Hook:
Script:
Scene-by-scene breakdown:
Suggested visuals:
Voiceover / dialogue:
On-screen text:
Call to action:

Keep the tone energetic and direct unless the brand voice below says otherwise. Be \
concrete and filmable -- a real editor should be able to shoot this without asking \
follow-up questions. Do not pad with generic filler."""

TREND_RESEARCHER_SYSTEM_PROMPT = """You are the AI Trend Researcher & Content Strategist inside \
Creator Hub AI, a platform for a combat-sports athlete and content creator.

CRITICAL HONESTY RULE: you do NOT have live internet access, and you cannot see real-time \
view counts, trending sounds, hashtag volume, or what is actually popular on any platform \
right now. You must never state a specific "trend," statistic, hashtag, or sound as \
currently real or trending unless the creator explicitly told you about it themselves. \
If the creator describes something they've personally observed (a trend, a competitor's \
video, an audience reaction), treat that as true and build on it. If they ask you to find \
or suggest trends in general without giving you specifics, make that limitation explicit \
in your "Note on data" section below rather than inventing fake current trends.

Structure your response with these exact section headings, in this order:

Note on data:
(One sentence: whether this response is based on what the creator told you, or is strategic brainstorming because no live trend data is connected.)

Niche angles:
Content format ideas:
Competitor / collaboration angles:
Suggested next step:

Keep suggestions concrete and specific to combat sports / fitness content unless the \
creator's request says otherwise. Do not pad with generic filler."""

ADS_MANAGER_SYSTEM_PROMPT = """You are the AI Ads Manager inside Creator Hub AI, a platform for a \
combat-sports athlete and content creator. Given a creator's request, do two things together: \
(1) produce ad creative -- concepts, headlines, hooks, and copy variations -- and (2) teach the \
creator how to plan and spend their ad budget sensibly, so they aren't guessing or spending \
money randomly: which platform to start on, which campaign objective to pick, a sensible test \
budget and how long to test before scaling, and how to split spend across audiences.

CRITICAL SCOPE RULE: you do not have access to any ad account, you cannot launch a campaign, \
and you cannot see or spend any real budget. You must never state or imply a real \
click-through rate, cost-per-result, cost-per-click, or any other performance number as if \
it's actual data from this creator's account -- you have no access to that. When you give \
budget, bidding, or platform advice, it must be framed as general best-practice guidance \
(industry rules of thumb for someone in this situation), never as a guarantee or as this \
creator's real account data. If the creator asks you to actually launch, schedule, or spend on \
a campaign, say plainly in your "Note on scope" section that this requires a connected \
ad-platform account with explicit approval, which isn't set up yet.

Structure your response with these exact section headings, in this order:

Note on scope:
(One sentence: this is creative + budget/platform planning guidance only -- no campaign is \
launched, no real budget is spent, and no real performance numbers are claimed.)

Campaign objective & audience:
Recommended platform & budget approach:
(Give a concrete plan: which platform(s) to start on and why, which campaign objective to \
choose, a sensible starting daily or weekly test budget and how long to test before scaling, \
and how to split spend across cold vs. warm/retargeting audiences if relevant. This is general \
best practice, not account-specific data.)
Ad concepts (headlines & hooks):
Ad copy variations:
Creative brief notes:
Suggested next step:

Keep everything concrete, platform-appropriate (Meta/TikTok/YouTube ads unless told otherwise), \
and specific to combat sports / fitness / coaching offers unless the creator's request says \
otherwise. Do not pad with generic filler."""

SOCIAL_MEDIA_MANAGER_SYSTEM_PROMPT = """You are the AI Social Media Manager inside Creator Hub AI, \
a platform for a combat-sports athlete and content creator. Given a creator's request, write \
captions, hashtags, titles, and a short content calendar draft they can post themselves.

CRITICAL SCOPE RULE: you do not have access to any connected Instagram, TikTok, or YouTube \
account, and you cannot publish, schedule, or post anything for real -- you only write drafts \
for the creator to copy and post manually. You must never claim or imply that something has \
been posted, scheduled, or published. If the creator asks you to actually post or schedule \
something, say plainly in your "Note on scope" section that real publishing requires a \
connected platform account, which isn't set up yet.

Structure your response with these exact section headings, in this order:

Note on scope:
(One sentence: these are drafts to post yourself -- nothing is actually published or scheduled.)

Content calendar draft:
(A short day-by-day or post-by-post plan matching what the creator asked for.)
Captions & hashtags:
Platform-specific notes:
(Point out real formatting differences that matter -- e.g. caption length limits, hashtag \
conventions, where a hook needs to land in the first seconds -- for whichever platforms the \
creator mentioned, defaulting to Instagram/TikTok/YouTube Shorts if they didn't specify.)
Suggested next step:

Keep everything concrete and specific to combat sports / fitness / coaching content unless the \
creator's request says otherwise. Do not pad with generic filler."""

PERFORMANCE_ANALYST_SYSTEM_PROMPT = """You are the AI Performance Analyst inside Creator Hub AI, \
a platform for a combat-sports athlete and content creator. The creator will paste in real \
numbers from their own posts (views, likes, comments, shares, watch time, follower counts, \
etc.) and you analyze what's working and why.

CRITICAL HONESTY RULE: you have no live connection to Instagram, TikTok, YouTube, or any \
analytics platform, and no access to this creator's account. You can only analyze numbers the \
creator actually pastes into their request. You must never invent, estimate, or assume a \
number they didn't give you -- no made-up view counts, engagement rates, or benchmarks \
presented as if they were this creator's real data. If the creator asks for analysis but \
hasn't given you any numbers, say so plainly in your "Note on data" section and ask what to \
analyze instead of inventing a report. General industry benchmarks are fine ONLY if clearly \
labeled as general knowledge, never as this creator's data.

Structure your response with these exact section headings, in this order:

Note on data:
(One sentence: this analysis is based only on the numbers the creator provided -- nothing is \
pulled from a live account, and nothing is invented. If no real numbers were given, say that \
here instead of analyzing.)

What's working:
What's underperforming:
Format & hook comparison:
(Compare the posts/formats the creator gave numbers for, if more than one. If only one data \
point was given, say there's not enough to compare yet.)
Suggested next step:

Keep everything concrete and tied directly to the numbers given. Do not pad with generic \
filler, and do not fabricate any figure."""


CAMPAIGN_PLANNER_SYSTEM_PROMPT = """You are the AI Campaign Planner inside Creator Hub AI, a \
content platform for a combat-sports athlete and content creator. Given a campaign's name and \
objective, break it into an ordered sequence of tasks, each one assigned to exactly one of this \
platform's specialist agents.

You may ONLY use these agent slugs, exactly as spelled, one per task:
{agent_slug_list}

CRITICAL FORMAT RULE: reply with nothing except the plan itself, in this exact format and \
nothing else -- no extra commentary before or after it:

Plan summary: <one sentence describing the overall approach for this specific campaign>
<agent_slug> | <short, concrete task title for that agent, specific to this campaign>
<agent_slug> | <short, concrete task title for that agent, specific to this campaign>
(repeat for 3 to 8 tasks total, in the order they should happen)

CRITICAL SCOPE RULE: you are not launching, running, or executing anything here -- you are only \
proposing which agents should do what, in what order, for this specific campaign. Pick only \
agents that are actually relevant to this campaign's objective; don't force in an agent that \
doesn't fit it. Keep every task title concrete and specific to the campaign name/objective \
given -- never generic filler, and never a task title that doesn't match what that agent \
actually does."""


VIDEO_EDITOR_SYSTEM_PROMPT = """You are the editing brain behind the AI Video Editor inside \
Creator Hub AI, a platform for a combat-sports athlete and content creator. The creator uploads \
one or more raw footage clips and describes, in plain language, whatever they want done to them -- \
anything from "just add captions" to "translate the captions to Spanish" to a full cut-and-reorder \
edit with transitions, color grading, and a text overlay. You do not generate any video yourself: \
you only decide WHICH of the real tools below should run, and with what parameters, by replying \
with a precise, machine-readable OPERATIONS PLAN that a real video-processing pipeline will \
execute exactly as you specify. Only include an operation the creator's instruction actually \
calls for -- never add a step they didn't ask for.

WHAT YOU ARE GIVEN: for each uploaded clip, its index (0-based), its real duration in seconds, \
and a transcript of what is actually said in it (a list of [start-end] spoken segments, or a note \
that no speech was detected).

THE REAL TOOLS AVAILABLE (each is its own optional key in your reply; omit a key entirely, or set \
it to null, if the creator didn't ask for that operation):

- trim_and_reorder: cut specific time ranges out of specific clips and join them in a specific \
order. Omit this (or set it to null) if the creator wants the clip(s) used in full, untouched, in \
upload order -- e.g. "just add captions to this" needs no cutting at all.
  - "segments": ordered list of {"clip_index", "start", "end"}. Every clip_index must be one you \
were actually given, and start/end must fall strictly within that clip's real duration.
  - "transition": optional {"style", "duration"} applied BETWEEN every adjacent pair of segments. \
"style" must be one of: none, fade, fadeblack, wipeleft, wiperight, slideleft, slideright, \
circleopen, dissolve, smoothleft, smoothright. "duration" is seconds (e.g. 0.3-1.0). Only include \
this if the creator asked for transitions/cuts-with-style between clips; a plain hard cut needs no \
transition object at all.
- change_speed: {"factor"} -- a number where 2.0 is twice as fast (slow-motion is below 1.0, e.g. \
0.5 for half speed). Only include this if the creator explicitly asked for a speed change, \
slow-motion, or a timelapse/sped-up effect.
- color_grade: {"preset"} -- one of: natural, vibrant, dark_cinematic, documentary, luxury. Only \
include this if the creator asked for a color grade, a "look," or a cinematic/vibrant/moody style.
- captions: {"enabled": true, "translate_to": null or a language name like "Spanish"} -- burns in \
real captions transcribed from the speech. Set "translate_to" only if the creator explicitly asked \
for the captions/subtitles in another language; otherwise leave it null (captions stay in the \
original spoken language).
- text_overlay: {"text", "start", "end"} -- an on-screen text/quote overlay. If the creator gave \
you their own exact words (e.g. in quotation marks, or clearly dictated), use that text VERBATIM -- \
never rewrite it. If they asked for a motivational quote/message/on-screen text but gave no exact \
words, write a short original one yourself that fits the footage and their instruction. Omit this \
key entirely if they didn't ask for any on-screen text.

CRITICAL HONESTY RULES:
- Never invent spoken words -- only reference what the given transcript actually says.
- You have no ability to see the footage itself -- you cannot judge framing, lighting, or visual \
quality. Base cutting decisions only on the transcript content, clip durations, and the creator's \
instruction.
- You do not currently have real music-beat detection. When the creator asks to "cut to the \
sound/beat," approximate this honestly by cutting on natural pauses and sentence boundaries in \
the speech transcript (a reasonable stand-in for musical rhythm), not by claiming to have \
detected an actual beat.
- Real voice dubbing/translation of the spoken audio itself isn't built yet -- only caption/text \
translation is real. If the creator asks for dubbed/translated speech audio, do not attempt it; \
just proceed with whatever other operations they also asked for.

REPLY FORMAT -- CRITICAL: reply with ONLY valid JSON, nothing before or after it, no markdown code \
fences, matching exactly this shape (every top-level key is optional except "notes"):

{
  "trim_and_reorder": {"segments": [{"clip_index": 0, "start": 0.0, "end": 4.2}], "transition": null} or null,
  "change_speed": {"factor": 1.5} or null,
  "color_grade": {"preset": "dark_cinematic"} or null,
  "captions": {"enabled": true, "translate_to": null} or null,
  "text_overlay": {"text": "Exact or AI-written words", "start": 0.0, "end": 3.0} or null,
  "notes": "One sentence explaining what you're doing and why."
}

If trim_and_reorder is included, choose segments so the finished edit feels like a real short-form \
piece (roughly 15-90 seconds total) unless the creator's instruction clearly asks for something \
longer or shorter."""


AUDIO_VOICE_SYSTEM_PROMPT = """You are the planning brain behind the AI Audio & Voice Agent inside \
Creator Hub AI, a platform for a combat-sports athlete and content creator. The creator uploads a \
single audio or video file and describes, in plain language, what they want done to its audio -- \
anything from "clean up the background noise" to "give me subtitles translated to Spanish" to \
"generate a voiceover saying: ...". You do not process any audio yourself: you only decide WHICH of \
the real tools below should run, and with what parameters, by replying with a precise, \
machine-readable OPERATIONS PLAN that a real audio-processing pipeline will execute exactly as you \
specify. Only include an operation the creator's instruction actually calls for -- never add a step \
they didn't ask for.

WHAT YOU ARE GIVEN: the uploaded file's name, its real duration in seconds, and whether it's a video \
file or an audio-only file.

THE REAL TOOLS AVAILABLE (each is its own optional key in your reply; omit a key entirely, or set it \
to null, if the creator didn't ask for that operation):

- clean_audio: {"enabled": true, "level"} -- real noise reduction and loudness cleanup on the audio \
track (a highpass filter, FFT-based noise reduction, and loudness normalization). "level" must be one \
of: light, standard, heavy -- pick based on how much the creator says the audio needs (background \
noise, hiss, echo, inconsistent volume = standard or heavy; "just a little cleanup" = light). Only \
include this if the creator asked to clean up, denoise, or improve the clarity of the audio/dialogue.
- subtitles: {"enabled": true, "translate_to": null or a language name like "Spanish"} -- transcribes \
the real speech and produces a standalone subtitle (.srt) file. Set "translate_to" only if the \
creator explicitly asked for the subtitles in another language; otherwise leave it null (subtitles \
stay in the original spoken language). Only include this if the creator asked for subtitles, \
captions, or a transcript file.
- dub_speech: {"enabled": true, "target_language", "voice"} -- a REAL translated spoken dub: \
transcribes the actual speech in the file, translates what was really said into another language, \
and generates a brand-new audio track speaking that translation aloud in a synthesized voice. This is \
a genuine translation of the file's ACTUAL spoken content -- not text the creator writes from \
scratch (that's the "voiceover" tool below). Use this when the creator wants the real speech \
translated into another language and spoken aloud -- e.g. "translate what I'm saying into Spanish \
and give me a spoken version" or "dub this into French." "target_language" is the language name \
(e.g. "Spanish"). "voice" must be one of: alloy, echo, fable, onyx, nova, shimmer -- pick based on \
any tone the creator describes; default to alloy if no tone is given. Only include this if the \
creator explicitly asked to translate/dub the file's actual spoken content into another language.
- voiceover: {"text", "voice"} -- generates a brand-new narration audio clip read by a synthesized \
voice, from text the creator supplies or you write -- NOT a translation of anything already said in \
the file (use dub_speech for that). If the creator gave you their own exact words (e.g. in quotation \
marks, or clearly dictated), use that text VERBATIM -- never rewrite it. If they asked for a \
voiceover/narration but gave no exact words, write a short original one yourself that fits their \
request. "voice" must be one of: alloy, echo, fable, onyx, nova, shimmer -- pick based on any tone \
the creator describes (calm, energetic, deep, warm, etc.); default to alloy if no tone is given. \
Only include this if the creator explicitly asked for a new voiceover, narration, or generated \
speech to be created from given or invented words.

CRITICAL HONESTY RULES:
- Both dub_speech and voiceover always use one of OpenAI's own fixed, synthesized voices -- never a \
cloned or impersonated real person's voice, and never the creator's own actual voice.
- dub_speech speaks a REAL, correct translation of the REAL transcribed speech -- it never invents \
what was said. Because translated text is rarely the exact same length as the original, the new \
audio track will not be frame-perfectly lip-synced to the footage -- it's a real, correct translation \
spoken aloud as its own audio track, not a synced redub.
- Translating the SUBTITLE TEXT (the "subtitles" tool's translate_to) is a separate, simpler real \
operation from dub_speech -- one produces a translated text file, the other produces translated \
spoken audio. A creator can ask for either or both.
- clean_audio is automatic noise reduction and loudness normalization, not manual audio engineering \
or mixing.
- Never invent what was said in the file -- you have no transcript yet at planning time; the \
subtitles and dub_speech tools, if included, produce their own real transcript afterward.

REPLY FORMAT -- CRITICAL: reply with ONLY valid JSON, nothing before or after it, no markdown code \
fences, matching exactly this shape (every top-level key is optional except "notes"):

{
  "clean_audio": {"enabled": true, "level": "standard"} or null,
  "subtitles": {"enabled": true, "translate_to": null} or null,
  "dub_speech": {"enabled": true, "target_language": "Spanish", "voice": "alloy"} or null,
  "voiceover": {"text": "Exact or AI-written words", "voice": "alloy"} or null,
  "notes": "One sentence explaining what you're doing and why."
}

If nothing in the instruction maps to any of the four tools above, return all four as null and use \
"notes" to say so honestly -- never invent an operation just to have something to do."""


def _merge_words_into_segments(segments, words):
    """OpenAI returns "segments" and "words" as two separate flat arrays
    (not nested) when both granularities are requested -- this associates
    each word with whichever segment's time range it overlaps most, so
    every segment ends up with its own "words" list. Adds the "words" key
    in place and also returns `segments` for convenience. A word that
    doesn't clearly overlap any segment (rare, right at a boundary) is
    just dropped rather than guessed into the wrong place -- the caller
    treats a segment with no words as "no word-level timing" and falls
    back to plain, non-animated captions for that stretch."""
    for seg in segments:
        seg["words"] = []
    for w in words:
        best_seg, best_overlap = None, 0.0
        for seg in segments:
            overlap = min(w["end"], seg["end"]) - max(w["start"], seg["start"])
            if overlap > best_overlap:
                best_overlap, best_seg = overlap, seg
        if best_seg is not None:
            best_seg["words"].append(w)
    return segments


def transcribe_audio(audio_path):
    """Transcribe spoken audio to text with per-segment AND per-word
    timestamps, using OpenAI's Whisper API (real speech-to-text, not a
    guess).

    Returns (ok, segments, error_message). `segments` is a list of
    {"start": float, "end": float, "text": str, "words": [...]} dicts in
    the clip's own original timeline, where each word is
    {"start": float, "end": float, "text": str}. The per-word timing is
    what powers the animated, karaoke-style caption highlight -- a
    segment with no usable word timing just gets an empty "words" list,
    and callers fall back to the plain caption style for it rather than
    guessing. If the clip has no detectable speech, this returns ok=True
    with an empty list -- that's a real, honest result, not a failure.
    Any actual failure (no key, network issue, bad file) returns
    ok=False with a clear error -- it never invents dialogue.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return False, None, "No OpenAI API key is configured (OPENAI_API_KEY is not set)."

    try:
        with open(audio_path, "rb") as f:
            response = requests.post(
                OPENAI_TRANSCRIPTION_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (os.path.basename(audio_path), f, "audio/mpeg")},
                # A plain dict can't hold the "timestamp_granularities[]"
                # field twice, and OpenAI wants it sent once per value we
                # ask for -- a list of (key, value) tuples lets requests
                # encode the repeated field correctly.
                data=[
                    ("model", TRANSCRIPTION_MODEL),
                    ("response_format", "verbose_json"),
                    ("timestamp_granularities[]", "word"),
                    ("timestamp_granularities[]", "segment"),
                ],
                timeout=TRANSCRIPTION_TIMEOUT_SECONDS,
            )
    except requests.exceptions.Timeout:
        return False, None, "The request to OpenAI timed out while transcribing audio. Try again in a moment."
    except requests.exceptions.RequestException as exc:
        return False, None, f"Couldn't reach OpenAI to transcribe audio: {exc}"
    except OSError as exc:
        return False, None, f"Couldn't read the extracted audio file: {exc}"

    if response.status_code == 200:
        try:
            data = response.json()
            raw_segments = data.get("segments", [])
            raw_words = data.get("words", [])
        except ValueError:
            return False, None, "OpenAI returned a transcription response we couldn't read."
        segments = [
            {"start": float(s["start"]), "end": float(s["end"]), "text": s.get("text", "").strip()}
            for s in raw_segments
            if s.get("text", "").strip()
        ]
        words = [
            {"start": float(w["start"]), "end": float(w["end"]), "text": w.get("word", "").strip()}
            for w in raw_words
            if w.get("word", "").strip()
        ]
        _merge_words_into_segments(segments, words)
        return True, segments, None

    detail = None
    try:
        detail = response.json().get("error", {}).get("message")
    except ValueError:
        pass
    if response.status_code == 401:
        return False, None, "OpenAI rejected the API key (401 Unauthorized) while transcribing audio."
    if response.status_code == 429:
        return False, None, detail or "OpenAI reported rate limiting or no remaining quota/credit (429) while transcribing audio."
    return False, None, detail or f"OpenAI returned an error while transcribing audio (HTTP {response.status_code})."


def _parse_json_reply(text):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    return json.loads(cleaned)


def generate_edit_plan(instruction, clips_info):
    """AI Video Editor: turn a free-text instruction + one or more raw
    clips' real durations/transcripts into a concrete, machine-readable
    OPERATIONS PLAN -- the model picks which real tools to run (cutting/
    reordering with transitions, speed change, color grade, captions +
    optional translation, text overlay), not a fixed one-size-fits-all
    pipeline. "Just add captions" and "make a full cut-and-color-graded
    Reel with a quote" both go through this same function; the difference
    is which keys come back non-null.

    clips_info: list of {"index": int, "filename": str, "duration": float,
    "transcript": [{"start","end","text"}, ...]} -- the real, already-
    measured facts about each uploaded clip; this function never guesses
    at them.

    Returns (ok, plan, error_message). `plan` always has every key below,
    already validated/clamped against the real clip durations and the
    real tool options given -- a caller can execute it directly without
    re-checking bounds:
      {"trim_and_reorder": {"segments": [...], "transition": {...} or None},
       "change_speed": {"factor": float} or None,
       "color_grade": {"preset": str} or None,
       "captions": {"enabled": bool, "translate_to": str or None} or None,
       "text_overlay": {"text","start","end"} or None,
       "notes": str}
    If trim_and_reorder was omitted/null in the model's reply, this fills
    in one full-duration segment per uploaded clip, in upload order, so
    there's always a valid cut/concat step for the pipeline to run even
    when the creator only wanted a non-cutting operation like captions.
    """
    lines = []
    for clip in clips_info:
        lines.append(f"Clip {clip['index']} -- \"{clip['filename']}\" -- duration {clip['duration']:.2f}s")
        if clip["transcript"]:
            for seg in clip["transcript"]:
                lines.append(f"  [{seg['start']:.2f}-{seg['end']:.2f}] {seg['text']}")
        else:
            lines.append("  (no speech detected in this clip)")
    user_content = f"Creator's instruction: {instruction}\n\n" + "\n".join(lines)

    ok, text, error = _chat_completion(VIDEO_EDITOR_SYSTEM_PROMPT, user_content)
    if not ok:
        return False, None, error

    try:
        raw_plan = _parse_json_reply(text)
    except ValueError:
        return False, None, "OpenAI's response wasn't valid JSON, so no real edit plan could be parsed."
    if not isinstance(raw_plan, dict):
        return False, None, "OpenAI's response wasn't a usable edit plan."

    durations_by_index = {clip["index"]: clip["duration"] for clip in clips_info}

    # --- trim_and_reorder (defaults to "use every clip, in full, in order") ---
    raw_trim = raw_plan.get("trim_and_reorder")
    valid_segments = []
    if isinstance(raw_trim, dict):
        for seg in raw_trim.get("segments", []) or []:
            try:
                clip_index = int(seg["clip_index"])
                start = float(seg["start"])
                end = float(seg["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if clip_index not in durations_by_index:
                continue
            clip_duration = durations_by_index[clip_index]
            start = max(0.0, min(start, clip_duration))
            end = max(0.0, min(end, clip_duration))
            if end - start < 0.3:  # too short to be a real usable cut
                continue
            valid_segments.append({"clip_index": clip_index, "start": round(start, 2), "end": round(end, 2)})
    if not valid_segments:
        valid_segments = [
            {"clip_index": clip["index"], "start": 0.0, "end": round(clip["duration"], 2)}
            for clip in clips_info
        ]

    transition = None
    if isinstance(raw_trim, dict) and isinstance(raw_trim.get("transition"), dict):
        style = raw_trim["transition"].get("style")
        if style in mp.TRANSITION_STYLES and style != "none" and len(valid_segments) > 1:
            try:
                duration = float(raw_trim["transition"].get("duration", 0.4))
            except (TypeError, ValueError):
                duration = 0.4
            transition = {"style": style, "duration": max(0.1, min(2.0, duration))}

    trim_and_reorder = {"segments": valid_segments, "transition": transition}

    # --- change_speed ---
    change_speed = None
    raw_speed = raw_plan.get("change_speed")
    if isinstance(raw_speed, dict):
        try:
            factor = float(raw_speed.get("factor", 1.0))
        except (TypeError, ValueError):
            factor = None
        if factor and abs(factor - 1.0) > 0.01:
            change_speed = {"factor": max(mp.MIN_SPEED_FACTOR, min(mp.MAX_SPEED_FACTOR, factor))}

    # --- color_grade ---
    color_grade = None
    raw_color = raw_plan.get("color_grade")
    if isinstance(raw_color, dict) and raw_color.get("preset") in mp.COLOR_PRESETS:
        color_grade = {"preset": raw_color["preset"]}

    # --- captions ---
    captions = None
    raw_captions = raw_plan.get("captions")
    if isinstance(raw_captions, dict) and raw_captions.get("enabled"):
        translate_to = raw_captions.get("translate_to")
        translate_to = str(translate_to).strip() if translate_to else None
        captions = {"enabled": True, "translate_to": translate_to}

    # --- text_overlay ---
    text_overlay = None
    raw_overlay = raw_plan.get("text_overlay")
    if isinstance(raw_overlay, dict) and raw_overlay.get("text"):
        try:
            o_start = float(raw_overlay.get("start", 0.0))
            o_end = float(raw_overlay.get("end", o_start + 3.0))
        except (TypeError, ValueError):
            o_start, o_end = 0.0, 3.0
        if o_end <= o_start:
            o_end = o_start + 3.0
        text_overlay = {
            "text": str(raw_overlay["text"]).strip(),
            "start": round(max(0.0, o_start), 2),
            "end": round(o_end, 2),
        }

    plan = {
        "trim_and_reorder": trim_and_reorder,
        "change_speed": change_speed,
        "color_grade": color_grade,
        "captions": captions,
        "text_overlay": text_overlay,
        "notes": raw_plan.get("notes", "").strip() if isinstance(raw_plan.get("notes"), str) else "",
    }
    return True, plan, None


def translate_caption_segments(entries, target_language):
    """Translate already-transcribed caption text into another language,
    keeping every entry's real timing untouched -- only the `text` field
    changes. Returns (ok, translated_entries, error_message). If the
    model's reply doesn't line up 1:1 with what was sent, this fails
    honestly rather than risk mismatched/garbled captions.
    """
    if not entries:
        return True, entries, None
    numbered = "\n".join(f"{i}: {e['text']}" for i, e in enumerate(entries))
    system_prompt = (
        f"Translate each numbered line into {target_language}. Reply with ONLY a JSON array of "
        f"strings, in the same order, same count ({len(entries)} items), each the translation of "
        "the matching line -- no numbering, no commentary, no markdown fences."
    )
    ok, text, error = _chat_completion(system_prompt, numbered)
    if not ok:
        return False, None, error
    try:
        translated = _parse_json_reply(text)
    except ValueError:
        return False, None, "OpenAI's translation reply wasn't valid JSON."
    if not isinstance(translated, list) or len(translated) != len(entries):
        return False, None, "OpenAI's translation reply didn't match the number of captions sent."
    result = [
        {"start": e["start"], "end": e["end"], "text": str(t).strip()}
        for e, t in zip(entries, translated)
    ]
    return True, result, None


def generate_audio_plan(instruction, file_info):
    """AI Audio & Voice Agent: turn a free-text instruction + the real
    facts about one uploaded file into a concrete, machine-readable
    OPERATIONS PLAN -- the model picks which real tools to run (clean up
    the audio, produce real subtitles with optional translation, generate
    a real translated spoken dub of the actual speech, generate a real
    brand-new voiceover narration), not a fixed pipeline.

    file_info: {"filename": str, "duration": float, "is_video": bool} --
    the real, already-measured facts about the uploaded file; this
    function never guesses at them.

    Returns (ok, plan, error_message). `plan` always has every key below,
    already validated against the real tool options given:
      {"clean_audio": {"enabled": bool, "level": str} or None,
       "subtitles": {"enabled": bool, "translate_to": str or None} or None,
       "dub_speech": {"enabled": bool, "target_language": str, "voice": str} or None,
       "voiceover": {"text": str, "voice": str} or None,
       "notes": str}
    """
    kind = "video" if file_info.get("is_video") else "audio-only"
    user_content = (
        f"Creator's instruction: {instruction}\n\n"
        f"Uploaded file: \"{file_info['filename']}\" -- {kind} file, duration {file_info['duration']:.2f}s"
    )

    ok, text, error = _chat_completion(AUDIO_VOICE_SYSTEM_PROMPT, user_content)
    if not ok:
        return False, None, error

    try:
        raw_plan = _parse_json_reply(text)
    except ValueError:
        return False, None, "OpenAI's response wasn't valid JSON, so no real plan could be parsed."
    if not isinstance(raw_plan, dict):
        return False, None, "OpenAI's response wasn't a usable plan."

    # --- clean_audio ---
    clean_audio = None
    raw_clean = raw_plan.get("clean_audio")
    if isinstance(raw_clean, dict) and raw_clean.get("enabled"):
        level = raw_clean.get("level")
        if level not in mp.NOISE_REDUCTION_LEVELS:
            level = "standard"
        clean_audio = {"enabled": True, "level": level}

    # --- subtitles ---
    subtitles = None
    raw_subs = raw_plan.get("subtitles")
    if isinstance(raw_subs, dict) and raw_subs.get("enabled"):
        translate_to = raw_subs.get("translate_to")
        translate_to = str(translate_to).strip() if translate_to else None
        subtitles = {"enabled": True, "translate_to": translate_to}

    # --- dub_speech ---
    dub_speech = None
    raw_dub = raw_plan.get("dub_speech")
    if isinstance(raw_dub, dict) and raw_dub.get("enabled") and raw_dub.get("target_language"):
        voice = raw_dub.get("voice")
        if voice not in VOICE_OPTIONS:
            voice = DEFAULT_VOICE
        dub_speech = {
            "enabled": True,
            "target_language": str(raw_dub["target_language"]).strip(),
            "voice": voice,
        }

    # --- voiceover ---
    voiceover = None
    raw_vo = raw_plan.get("voiceover")
    if isinstance(raw_vo, dict) and raw_vo.get("text"):
        voice = raw_vo.get("voice")
        if voice not in VOICE_OPTIONS:
            voice = DEFAULT_VOICE
        voiceover = {"text": str(raw_vo["text"]).strip(), "voice": voice}

    plan = {
        "clean_audio": clean_audio,
        "subtitles": subtitles,
        "dub_speech": dub_speech,
        "voiceover": voiceover,
        "notes": raw_plan.get("notes", "").strip() if isinstance(raw_plan.get("notes"), str) else "",
    }
    return True, plan, None


def translate_full_text(text, target_language):
    """AI Audio & Voice Agent (dub_speech): translate a block of real
    transcribed speech into another language as one natural, fluent
    passage -- used to generate a real spoken dub, where natural phrasing
    matters more than preserving the original's exact per-sentence timing
    (translate_caption_segments is the one that keeps per-segment timing,
    for captions). Returns (ok, translated_text, error_message). An empty
    input returns an honest empty translation rather than calling OpenAI
    for nothing."""
    if not text or not text.strip():
        return True, "", None
    system_prompt = (
        f"Translate the following transcribed speech into {target_language}. Reply with ONLY the "
        "translated text as natural, fluent spoken language -- no quotation marks, no commentary, "
        "no markdown, nothing before or after it."
    )
    ok, translated, error = _chat_completion(system_prompt, text.strip())
    if not ok:
        return False, None, error
    return True, translated.strip(), None


def generate_voiceover_audio(text, voice, output_path):
    """AI Audio & Voice Agent: generate a real narration audio clip from
    text, using OpenAI's text-to-speech API and one of its fixed,
    synthesized voices (VOICE_OPTIONS) -- never a cloned or impersonated
    real person's voice. Writes the real returned audio bytes to
    `output_path` (mp3). Returns (ok, output_path, error_message) -- never
    fabricates an audio file if the call fails."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return False, None, "No OpenAI API key is configured (OPENAI_API_KEY is not set)."
    if voice not in VOICE_OPTIONS:
        voice = DEFAULT_VOICE
    if not text or not text.strip():
        return False, None, "No voiceover text was provided."

    payload = {"model": TTS_MODEL, "voice": voice, "input": text.strip(), "response_format": "mp3"}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        response = requests.post(OPENAI_TTS_URL, headers=headers, json=payload, timeout=TTS_TIMEOUT_SECONDS)
    except requests.exceptions.Timeout:
        return False, None, "The request to OpenAI timed out while generating the voiceover. Try again in a moment."
    except requests.exceptions.RequestException as exc:
        return False, None, f"Couldn't reach OpenAI to generate the voiceover: {exc}"

    if response.status_code == 200:
        try:
            with open(output_path, "wb") as f:
                f.write(response.content)
        except OSError as exc:
            return False, None, f"Couldn't save the generated voiceover: {exc}"
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return False, None, "OpenAI returned an empty voiceover file."
        return True, output_path, None

    detail = None
    try:
        detail = response.json().get("error", {}).get("message")
    except ValueError:
        pass
    if response.status_code == 401:
        return False, None, "OpenAI rejected the API key (401 Unauthorized) while generating the voiceover."
    if response.status_code == 429:
        return False, None, detail or "OpenAI reported rate limiting or no remaining quota/credit (429) while generating the voiceover."
    return False, None, detail or f"OpenAI returned an error while generating the voiceover (HTTP {response.status_code})."


def _brand_context_block(brand_profile):
    if not brand_profile:
        return ""
    parts = []
    if brand_profile.get("name"):
        parts.append(f"Brand: {brand_profile['name']}")
    if brand_profile.get("tone_of_voice"):
        parts.append(f"Tone of voice: {brand_profile['tone_of_voice']}")
    if brand_profile.get("target_audience"):
        parts.append(f"Target audience: {brand_profile['target_audience']}")
    if brand_profile.get("preferred_style"):
        parts.append(f"Preferred style: {brand_profile['preferred_style']}")
    if not parts:
        return ""
    return "\n\nBrand context from this creator's Brand Memory:\n" + "\n".join(parts)


def _chat_completion(system_prompt, user_content):
    """Call OpenAI's chat completions endpoint. Returns (ok, text, error_message).

    Exactly one of `text` / `error_message` is set when ok is True/False
    respectively, so callers never have to guess which one to trust. This
    never fabricates a result -- any failure comes back as an honest error.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return False, None, "No OpenAI API key is configured (OPENAI_API_KEY is not set)."

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.8,
        "max_tokens": 900,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            OPENAI_CHAT_COMPLETIONS_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except requests.exceptions.Timeout:
        return False, None, "The request to OpenAI timed out. Try again in a moment."
    except requests.exceptions.RequestException as exc:
        return False, None, f"Couldn't reach OpenAI: {exc}"

    if response.status_code == 200:
        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"].strip()
        except (ValueError, KeyError, IndexError):
            return False, None, "OpenAI returned a response we couldn't read. Try again."
        if not text:
            return False, None, "OpenAI returned an empty response. Try again."
        return True, text, None

    # Try to surface OpenAI's own error message; fall back to the status code.
    detail = None
    try:
        detail = response.json().get("error", {}).get("message")
    except ValueError:
        pass

    if response.status_code == 401:
        return False, None, "OpenAI rejected the API key (401 Unauthorized). Double-check the key is correct and active."
    if response.status_code == 429:
        return False, None, detail or "OpenAI reported rate limiting or no remaining quota/credit (429). Check your OpenAI account's usage and billing."
    return False, None, detail or f"OpenAI returned an error (HTTP {response.status_code})."


def generate_script(request_summary, brand_profile=None):
    """Scriptwriter agent: generate a real, ready-to-film script."""
    user_content = request_summary + _brand_context_block(brand_profile)
    return _chat_completion(SCRIPTWRITER_SYSTEM_PROMPT, user_content)


def generate_trend_research(request_summary, brand_profile=None):
    """Trend Researcher agent: generate strategic content research.

    Honesty-critical: this is never allowed to assert a fake real-time
    trend as fact. The system prompt above enforces that at the model
    level, and the output always carries a "Note on data" section saying
    whether it's based on what the creator provided or on strategic
    brainstorming.
    """
    user_content = request_summary + _brand_context_block(brand_profile)
    return _chat_completion(TREND_RESEARCHER_SYSTEM_PROMPT, user_content)


def generate_ads_creative(request_summary, brand_profile=None):
    """Ads Manager agent: generate ad concepts, headlines, copy, AND
    teach budget/platform strategy (which platform to start on, campaign
    objective, test budget, scaling, audience split).

    Scope-critical: this never launches a campaign, never spends real
    budget, and never invents a real performance number (CTR, CPA, etc.)
    tied to this creator's account -- any budget/bidding advice it gives
    is general best practice, not account data. The system prompt
    enforces that, and the output always opens with a "Note on scope"
    line making it explicit.
    """
    user_content = request_summary + _brand_context_block(brand_profile)
    return _chat_completion(ADS_MANAGER_SYSTEM_PROMPT, user_content)


def generate_social_content(request_summary, brand_profile=None):
    """Social Media Manager agent: generate captions, hashtags, and a
    content calendar draft.

    Scope-critical: this never posts, schedules, or publishes anything --
    it only writes drafts for the creator to post themselves. Real
    publishing needs a connected Instagram/TikTok/YouTube account, which
    isn't built yet (see config.AGENT_INTEGRATIONS -- this agent still
    lists those as its longer-term integration keys; the OpenAI-only
    draft generation path is separate and doesn't require them).
    """
    user_content = request_summary + _brand_context_block(brand_profile)
    return _chat_completion(SOCIAL_MEDIA_MANAGER_SYSTEM_PROMPT, user_content)


def generate_campaign_plan(name, objective, allowed_agent_slugs, brand_profile=None):
    """Campaign Planner agent: ask OpenAI to break this specific campaign
    into an ordered list of (agent_slug, task_title) steps tailored to its
    name/objective, instead of always handing back the same fixed template.

    Returns (ok, parsed_tasks, error_message). parsed_tasks is a list of
    (agent_slug, task_title) tuples already validated against
    allowed_agent_slugs -- the caller never has to re-check them. If the
    model's reply can't be parsed into at least one valid task, this
    returns ok=False with an honest error so the caller falls back to the
    standard task template rather than silently creating an empty plan.
    """
    agent_slug_list = "\n".join(f"- {slug}" for slug in allowed_agent_slugs)
    system_prompt = CAMPAIGN_PLANNER_SYSTEM_PROMPT.format(agent_slug_list=agent_slug_list)
    user_content = f"Campaign name: {name}\nCampaign objective: {objective}" + _brand_context_block(brand_profile)
    ok, text, error = _chat_completion(system_prompt, user_content)
    if not ok:
        return False, None, error

    allowed = set(allowed_agent_slugs)
    parsed_tasks = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("plan summary"):
            continue
        if "|" not in line:
            continue
        slug_part, _, title_part = line.partition("|")
        slug = slug_part.strip().strip("-").strip().lower()
        title = title_part.strip()
        if slug in allowed and title:
            parsed_tasks.append((slug, title))

    if not parsed_tasks:
        return False, None, "OpenAI's response didn't match the expected plan format, so no real plan could be parsed."

    return True, parsed_tasks, None


def generate_performance_analysis(request_summary, brand_profile=None):
    """Performance Analyst agent: analyze real numbers the creator pastes
    in (views, engagement, watch time, etc.).

    Honesty-critical: this never invents a number. It only analyzes data
    the creator actually gave it, and says so plainly if none was given.
    llm_client.PERFORMANCE_ANALYST_SYSTEM_PROMPT enforces that.
    """
    user_content = request_summary + _brand_context_block(brand_profile)
    return _chat_completion(PERFORMANCE_ANALYST_SYSTEM_PROMPT, user_content)
