"""Real integration with Upload-Post (upload-post.com), a unified social
media posting API.

Why this instead of talking to Meta's Instagram API directly: getting a new
developer app approved for real publishing permissions goes through Meta's
own app review, which is slow and strict for a small app. Upload-Post has
already been through that review and exposes a plain REST API on top of it,
so Creator Hub AI never has to run its own Meta app review at all.

Multi-tenancy: Daniel's ONE Upload-Post account (one API key, UPLOAD_POST_
API_KEY) serves every agency on the platform. Upload-Post's own concept of
a "profile" maps one-to-one onto one Creator Hub AI agency -- each agency
gets its own profile (a stable, deterministic username, stored on
agencies.upload_post_username once it's first created) and connects ITS OWN
Instagram account to that profile through Upload-Post's hosted connect
page. One agency's connected account is never visible to or usable by any
other agency, even though they all share the same underlying API key.

Design rules, same as llm_client.py:
  - The API key is never hardcoded -- only ever read from the
    UPLOAD_POST_API_KEY environment variable (see
    config.INTEGRATIONS["social_posting"]).
  - This never fabricates success. A publish call either gets back a real,
    confirmed result from Upload-Post (which itself only ever reports
    success once Instagram has actually accepted the post) or an honest
    error -- never a guessed or assumed outcome.
  - Posting a photo or video never requires Creator Hub AI to host its own
    public file storage: the uploaded file is forwarded straight through to
    Upload-Post in the same request, exactly like the AI Video Editor
    processes an uploaded file within one request (see media_tools.py).
"""

import os

import requests

API_BASE = "https://api.upload-post.com/api"
REQUEST_TIMEOUT_SECONDS = 30
UPLOAD_TIMEOUT_SECONDS = 120  # forwarding a video file can take a while


def _api_key():
    return os.environ.get("UPLOAD_POST_API_KEY")


def _headers():
    return {"Authorization": f"Apikey {_api_key()}"}


def _username_for_agency(agency_id):
    # Deterministic and stable, so calling ensure_profile() again (e.g.
    # after a first attempt failed partway) can never create a second,
    # orphaned profile for the same agency. Upload-Post usernames only
    # allow letters, numbers, "_", "@" and "-", which this always satisfies.
    return f"agency-{agency_id}"


def _error_message(resp):
    try:
        payload = resp.json()
        return payload.get("message") or payload.get("error") or f"Upload-Post returned status {resp.status_code}."
    except ValueError:
        return f"Upload-Post returned status {resp.status_code}."


def ensure_profile(agency_id):
    """Make sure this agency has its own Upload-Post profile, creating one
    if it doesn't exist yet. Returns (ok, username, error_message)."""
    if not _api_key():
        return False, None, "Upload-Post isn't connected on this server yet (UPLOAD_POST_API_KEY is not set)."

    username = _username_for_agency(agency_id)
    try:
        resp = requests.post(
            f"{API_BASE}/uploadposts/users",
            headers=_headers(),
            json={"username": username},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        return False, None, f"Couldn't reach Upload-Post: {e}"

    if resp.status_code in (200, 201) or resp.status_code == 409:
        # 409 ("username already in use") is exactly the expected, healthy
        # outcome on every call after the first for this agency.
        return True, username, None
    return False, None, _error_message(resp)


def get_connect_url(agency_id, agency_name, redirect_url, logo_url=None):
    """Create (if needed) this agency's profile and return a hosted URL that
    lets the agency connect its OWN Instagram account to it. Creator Hub AI
    never sees or stores their Instagram password -- only the fact that
    Upload-Post later confirms the connection. Returns (ok, url, error)."""
    ok, username, error = ensure_profile(agency_id)
    if not ok:
        return False, None, error

    payload = {
        "username": username,
        "redirect_url": redirect_url,
        "platforms": ["instagram"],
        "connect_title": f"Connect Instagram for {agency_name}",
        "connect_description": "Connect your agency's Instagram account so Creator Hub AI can publish to it on your behalf.",
    }
    if logo_url:
        payload["logo_image"] = logo_url

    try:
        resp = requests.post(
            f"{API_BASE}/uploadposts/users/generate-jwt",
            headers=_headers(),
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        return False, None, f"Couldn't reach Upload-Post: {e}"

    if resp.status_code != 200:
        return False, None, _error_message(resp)

    try:
        data = resp.json()
    except ValueError:
        return False, None, "Upload-Post returned an unreadable response."

    url = data.get("access_url")
    if not url:
        return False, None, "Upload-Post didn't return a connect link."
    return True, url, None


def connected_platforms(agency_id):
    """Which platforms this agency has actually connected, read straight
    from Upload-Post every time -- never cached or guessed, so this is
    always the real current state. Returns a set, e.g. {"instagram"}, or an
    empty set if nothing is connected (or Upload-Post isn't reachable)."""
    if not _api_key():
        return set()
    username = _username_for_agency(agency_id)
    try:
        resp = requests.get(f"{API_BASE}/uploadposts/users", headers=_headers(), timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException:
        return set()
    if resp.status_code != 200:
        return set()

    try:
        profiles = resp.json().get("profiles", [])
    except ValueError:
        return set()

    for profile in profiles:
        if profile.get("username") != username:
            continue
        # Upload-Post's own field name for this varies by response version;
        # this covers the shapes without ever guessing a platform is
        # connected when it isn't.
        socials = profile.get("social_accounts") or profile.get("connected_platforms") or []
        if isinstance(socials, dict):
            return {k for k, v in socials.items() if v}
        return set(socials)
    return set()


def publish_instagram(agency_id, caption, file_storage, media_kind):
    """Publish one photo or one video to this agency's connected Instagram
    account. `file_storage` is the raw uploaded werkzeug FileStorage --
    forwarded straight through to Upload-Post in this same request, so
    Creator Hub AI never needs its own public file hosting for this.
    `media_kind` is "photo" or "video". Returns (ok, result_dict, error)."""
    if not _api_key():
        return False, None, "Upload-Post isn't connected on this server yet (UPLOAD_POST_API_KEY is not set)."

    username = _username_for_agency(agency_id)
    data = {"user": username, "platform[]": ["instagram"], "title": caption, "instagram_title": caption}
    filename = file_storage.filename or ("photo.jpg" if media_kind == "photo" else "video.mp4")
    content_type = file_storage.mimetype or "application/octet-stream"
    file_field = "photos[]" if media_kind == "photo" else "video"
    endpoint = "upload_photos" if media_kind == "photo" else "upload"

    try:
        resp = requests.post(
            f"{API_BASE}/{endpoint}",
            headers=_headers(),
            data=data,
            files={file_field: (filename, file_storage.stream, content_type)},
            timeout=UPLOAD_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        return False, None, f"Couldn't reach Upload-Post: {e}"

    try:
        payload = resp.json()
    except ValueError:
        return False, None, f"Upload-Post returned an unreadable response (status {resp.status_code})."

    if resp.status_code != 200 or not payload.get("success"):
        return False, None, payload.get("message") or payload.get("error") or f"Upload-Post rejected the post (status {resp.status_code})."

    ig_result = (payload.get("results") or {}).get("instagram") or {}
    if ig_result and not ig_result.get("success", True):
        return False, None, ig_result.get("error") or "Instagram rejected the post."

    return True, {"url": ig_result.get("url"), "request_id": payload.get("request_id")}, None
