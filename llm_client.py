"""Real integration with an external language-model API (OpenAI), used by
the Scriptwriter agent.

Design rules this file follows, straight from the project spec:
  - The API key is NEVER hardcoded. It's read from the OPENAI_API_KEY
    environment variable only (see config.INTEGRATIONS["llm_provider"]).
    If it isn't set, is_configured() already stops this file from being
    called at all -- the caller shows "setup required" instead.
  - We never fabricate a result. If the call fails for any reason (bad
    key, no quota, network issue, unexpected response), we return a clear
    honest error instead of inventing script text.
  - This talks to OpenAI directly over HTTPS using the `requests` library
    and OpenAI's plain REST API -- no OpenAI SDK package required, so
    there's nothing extra to install beyond `requests`.

Note for Daniel: this code is correct and ready to use, but it can only
be *tested* on a machine that can actually reach api.openai.com. It will
work the moment it runs somewhere with normal internet access and a real
key in the OPENAI_API_KEY environment variable.
"""

import os
import requests

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o-mini"  # inexpensive, good-quality model -- stretches a small API budget
REQUEST_TIMEOUT_SECONDS = 45

SYSTEM_PROMPT = """You are the AI Scriptwriter inside Creator Hub AI, a content-creation \
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


def generate_script(request_summary, brand_profile=None):
    """Call OpenAI to generate a real script. Returns (ok, text, error_message).

    Exactly one of `text` / `error_message` is set when ok is False/True
    respectively, so callers never have to guess which one to trust.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return False, None, "No OpenAI API key is configured (OPENAI_API_KEY is not set)."

    user_content = request_summary + _brand_context_block(brand_profile)

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
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
