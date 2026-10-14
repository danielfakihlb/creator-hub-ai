"""
Creator Hub AI - central configuration.

This file is the single source of truth for subscription plans and which
AI agents each plan can access. The spec requires that agent-to-plan
assignment be configuration, not hardcoded per-route logic -- so every
permission check in the app (templates, route guards, API responses) reads
from PLANS / AGENTS below instead of checking agent names directly.

To move an agent between plans, change its "plans" list here. Nothing else
in the codebase needs to change.
"""

import os

# -----------------------------------------------------------------------
# Subscription plans
# -----------------------------------------------------------------------
# "slug" is the stable identifier stored in the database and used in code.
# "price_usd" is display-only right now; real billing amounts must come
# from the configured payment provider once one is connected (see billing
# blueprint) -- we never invent a charged amount.

PLANS = {
    "creator": {
        "slug": "creator",
        "name": "Creator",
        "price_usd": 50,
        "tagline": "The core toolkit for solo creators.",
        "badge": None,
    },
    "full_studio": {
        "slug": "full_studio",
        "name": "Full Studio",
        "price_usd": 80,
        "tagline": "Every agent. Every workflow. Full creative team.",
        "badge": "Full Studio",
    },
}

PLAN_ORDER = ["creator", "full_studio"]

# -----------------------------------------------------------------------
# AI Agents
# -----------------------------------------------------------------------
# "plans" lists which plan slugs can access this agent. An agent reachable
# from BOTH plans should list both slugs. Agents exclusive to Full Studio
# (per spec section 2 / 3) list only "full_studio".
#
# "status" communicates real implementation state to the UI so we never
# present a placeholder as a finished feature:
#   "orchestration_ready" -> workspace, data model and task routing are
#                            real and working; the underlying processing
#                            call (video render, LLM call, publishing API)
#                            needs external credentials to go live.
#   "planned"             -> UI scaffold only, not yet wired to any backend
#                            logic.

AGENTS = {
    "video_editor": {
        "slug": "video_editor",
        "name": "AI Video Editor",
        "short_description": "Turn raw footage into edited short-form content.",
        "icon": "video",
        "plans": ["creator", "full_studio"],
        "status": "orchestration_ready",
        "requires_integration": "Works now -- real trimming and platform resizing run on FFmpeg, installed directly on this server (no external key or signup needed). Highlight-moment detection and multi-clip assembly aren't built yet.",
        "capabilities": [
            "Upload a real video file and get a real file back",
            "Trim to an exact time range",
            "Real resize/crop (no letterboxing) for Reels / TikTok / Shorts, Square, or Landscape",
            "Runs on FFmpeg on this server -- no external video API needed",
            "Hand off to Color Grading & Audio agents",
        ],
    },
    "color_grading": {
        "slug": "color_grading",
        "name": "AI Color Grading Agent",
        "short_description": "Consistent, cinematic color across every clip.",
        "icon": "palette",
        "plans": ["creator", "full_studio"],
        "status": "orchestration_ready",
        "requires_integration": "Works now -- real color adjustments run on FFmpeg, installed directly on this server (no external key or signup needed). This is an automatic, good-enough grade, not a frame-by-frame, shot-matched correction a human colorist would do.",
        "capabilities": [
            "Exposure, contrast, white balance correction",
            "Cinematic style presets (dark, natural, vibrant, documentary, luxury)",
            "Save & reuse custom looks",
            "Match footage to a reference",
        ],
    },
    "scriptwriter": {
        "slug": "scriptwriter",
        "name": "AI Scriptwriter",
        "short_description": "Hooks, scripts, and scene breakdowns on demand.",
        "icon": "pen",
        "plans": ["creator", "full_studio"],
        "status": "orchestration_ready",
        "requires_integration": "Needs an OpenAI API key connected to generate real script text (see Account Settings → Integrations).",
        "capabilities": [
            "Hook, script, scene breakdown, CTA generation",
            "Multiple tones, platforms, languages",
            "Brand-voice aware (reads Brand Memory)",
            "Send finished scripts to the Video Editor",
        ],
    },
    "audio_voice": {
        "slug": "audio_voice",
        "name": "AI Audio & Voice Agent",
        "short_description": "Clean dialogue, subtitles, and voiceovers.",
        "icon": "waveform",
        "plans": ["creator", "full_studio"],
        "status": "orchestration_ready",
        "requires_integration": "Noise removal, transcription and voice generation each need a connected audio-processing / speech API.",
        "capabilities": [
            "Noise reduction & dialogue clarity",
            "Transcription & synced subtitles",
            "Subtitle translation",
            "Voiceover generation (authorized voices only)",
        ],
    },
    "social_media_manager": {
        "slug": "social_media_manager",
        "name": "AI Social Media Manager",
        "short_description": "Plan, organize, and publish across platforms.",
        "icon": "calendar",
        "plans": ["full_studio"],
        "status": "orchestration_ready",
        "requires_integration": "Captions, hashtags, and content calendar drafts use your connected OpenAI key and work now. Direct publishing to Instagram, TikTok, or YouTube requires each platform's own connected API (a separate, bigger integration) -- until then, everything exports as a draft for you to post yourself.",
        "capabilities": [
            "Captions, hashtags, titles",
            "Content calendar & drafts",
            "Multi-platform repurposing",
            "Publishing status tracking (never marked published without provider confirmation)",
        ],
    },
    "ads_manager": {
        "slug": "ads_manager",
        "name": "AI Ads Manager",
        "short_description": "Plan smart ad spend and write the creative to match.",
        "icon": "megaphone",
        "plans": ["full_studio"],
        "status": "orchestration_ready",
        "requires_integration": "Ad concepts, copy, and budget/platform strategy guidance use your connected OpenAI key and work now. Launching live campaigns or spending real budget still requires a connected ad-platform API and explicit per-launch approval -- the system never spends money automatically.",
        "capabilities": [
            "Campaign objective, platform, and test-budget guidance",
            "Ad concepts, copy, headlines, angles",
            "Creative briefs & A/B variations",
            "Performance review (recommendations clearly separated from verified results)",
        ],
    },
    "trend_researcher": {
        "slug": "trend_researcher",
        "name": "AI Trend Researcher & Content Strategist",
        "short_description": "Find opportunities and shape content strategy.",
        "icon": "compass",
        "plans": ["creator", "full_studio"],
        "status": "orchestration_ready",
        "requires_integration": "Live trend data requires a connected research/search API; without one, the agent works from information you provide rather than claiming unverified trends.",
        "capabilities": [
            "Niche & competitor research",
            "Content idea & format suggestions",
            "Collaboration opportunity spotting",
            "Clearly separates verified trends from predictions",
        ],
    },
    "performance_analyst": {
        "slug": "performance_analyst",
        "name": "AI Performance Analyst",
        "short_description": "Understand what's working and why.",
        "icon": "chart",
        "plans": ["full_studio"],
        "status": "orchestration_ready",
        "requires_integration": "Works now with your connected OpenAI key -- paste in your real numbers (views, engagement, watch time) and it analyzes them. A direct analytics platform connection (auto-pulling the numbers for you) isn't built yet; until then, it tells you what's missing rather than inventing numbers.",
        "capabilities": [
            "Views, watch time, engagement analysis",
            "Format & hook comparisons",
            "Weekly / monthly reports",
            "Sends insights to the Campaign Planner",
        ],
    },
    "campaign_planner": {
        "slug": "campaign_planner",
        "name": "AI Campaign Planner",
        "short_description": "Coordinates every agent into one campaign.",
        "icon": "layers",
        "plans": ["creator", "full_studio"],
        "status": "orchestration_ready",
        "requires_integration": "Fully functional for task orchestration. With your connected OpenAI key, it writes a custom task breakdown for each campaign's actual objective; if no key is connected, or the objective field is left blank, it falls back to a standard task template instead.",
        "capabilities": [
            "Breaks goals into tasks across agents",
            "Tracks dependencies & status",
            "Requests approval at key stages",
            "Respects each user's subscription plan",
        ],
    },
}

AGENT_ORDER = [
    "campaign_planner",
    "video_editor",
    "color_grading",
    "scriptwriter",
    "audio_voice",
    "trend_researcher",
    "social_media_manager",
    "ads_manager",
    "performance_analyst",
]

# -----------------------------------------------------------------------
# Account features
# -----------------------------------------------------------------------
# Brand Memory and Media Library are NOT agents: you don't give them a task
# and get a result back the way you do with an agent. They're shared
# infrastructure every agent reads from automatically, included with every
# plan at no extra cost -- so they're deliberately kept out of AGENTS /
# AGENT_ORDER (the agent count, the plan comparison table, the "N agents"
# copy) and out of the subscription/permission system entirely. They only
# require being logged in, not any particular plan.

ACCOUNT_FEATURES = {
    "brand_memory": {
        "slug": "brand_memory",
        "name": "Brand Memory",
        "short_description": "One consistent brand, remembered everywhere.",
        "icon": "bookmark",
        "capabilities": [
            "Multiple brand profiles per account",
            "Colors, fonts, tone of voice, guidelines",
            "Other agents read the active profile automatically",
            "Nothing saved as confirmed without your approval",
        ],
    },
    "media_library": {
        "slug": "media_library",
        "name": "Media Library",
        "short_description": "Every asset, organized and searchable.",
        "icon": "folder",
        "capabilities": [
            "Upload video, image, audio, brand assets",
            "Folders, tags, project associations",
            "Duplicate detection",
            "Secure, access-controlled storage",
        ],
    },
}

ACCOUNT_FEATURE_ORDER = ["brand_memory", "media_library"]


def agents_for_plan(plan_slug):
    """Return agent configs available to a given plan, in display order."""
    return [AGENTS[slug] for slug in AGENT_ORDER if plan_slug in AGENTS[slug]["plans"]]


def plan_includes_agent(plan_slug, agent_slug):
    agent = AGENTS.get(agent_slug)
    if not agent:
        return False
    return plan_slug in agent["plans"]


def cheapest_plan_for_agent(agent_slug):
    """The lowest-priced plan that unlocks this agent, for upgrade prompts."""
    agent = AGENTS.get(agent_slug)
    if not agent:
        return None
    for slug in PLAN_ORDER:
        if slug in agent["plans"]:
            return PLANS[slug]
    return None


# -----------------------------------------------------------------------
# Integration configuration
# -----------------------------------------------------------------------
# Every external dependency is read from the environment so no key is ever
# hardcoded or shipped in source. is_configured() lets templates/routes
# show an honest "setup required" state instead of pretending a feature
# works.

INTEGRATIONS = {
    "stripe": {
        "label": "Stripe (payments)",
        "env_vars": ["STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY"],
    },
    "llm_provider": {
        "label": "OpenAI API key (Scriptwriter / Trend Researcher / Ads Manager / Social Media Manager / Performance Analyst / Campaign Planner)",
        "env_vars": ["OPENAI_API_KEY"],
    },
    "video_processing": {
        "label": "FFmpeg on this server (Video Editor / Color Grading) -- checked by looking for the ffmpeg program, not an API key",
        "env_vars": [],
    },
    "speech_audio": {
        "label": "Speech / audio processing API (Audio & Voice Agent)",
        "env_vars": ["SPEECH_API_KEY"],
    },
    "social_instagram": {"label": "Instagram API", "env_vars": ["INSTAGRAM_ACCESS_TOKEN"]},
    "social_tiktok": {"label": "TikTok API", "env_vars": ["TIKTOK_ACCESS_TOKEN"]},
    "social_youtube": {"label": "YouTube API", "env_vars": ["YOUTUBE_ACCESS_TOKEN"]},
    "ads_platform": {"label": "Ads platform API", "env_vars": ["ADS_PLATFORM_API_KEY"]},
    "analytics": {"label": "Analytics data source", "env_vars": ["ANALYTICS_API_KEY"]},
}


def is_configured(integration_key):
    # video_processing is the one exception to the "env var present"
    # check below: there's no external account or key for it at all --
    # it's real, local FFmpeg running directly on this server, so the
    # honest check is "is the ffmpeg program actually here", not
    # "is some API key set" (there never was one to set).
    if integration_key == "video_processing":
        import shutil
        return shutil.which("ffmpeg") is not None

    spec = INTEGRATIONS.get(integration_key)
    if not spec:
        return False
    return all(os.environ.get(v) for v in spec["env_vars"])


SECRET_KEY = os.environ.get("CREATOR_HUB_SECRET_KEY", "dev-only-insecure-key-change-me")
DATABASE_PATH = os.environ.get(
    "CREATOR_HUB_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "creator_hub.db"),
)
