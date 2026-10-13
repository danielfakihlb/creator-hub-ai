"""Real integration with an external language-model API (OpenAI).

Shared by every agent that calls OpenAI for real (Scriptwriter, Trend
Researcher, and any future one) -- each agent gets its own system prompt
below, but they all go through the same honest call-and-error-handling
path in `_chat_completion`.

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

import os
import requests

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o-mini"  # inexpensive, good-quality model -- stretches a small API budget
REQUEST_TIMEOUT_SECONDS = 45

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
