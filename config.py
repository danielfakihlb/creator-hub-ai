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
        "requires_integration": "A video processing engine (e.g. FFmpeg-based render worker or a managed video API) must be connected before exports are real.",
        "capabilities": [
            "Upload and analyze raw footage",
            "Surface candidate highlight moments",
            "Draft Reels / TikTok / Shorts cut lists",
            "Resize for multiple platforms",
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
        "requires_integration": "Real pixel-level grading needs a video processing engine connected (same dependency as the Video Editor).",
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
        "requires_integration": "Direct publishing requires official platform API connections (Instagram, TikTok, YouTube) under Account Settings → Integrations. Until connected, content exports for manual posting.",
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
        "short_description": "Develop and organize advertising campaigns.",
        "icon": "megaphone",
        "plans": ["full_studio"],
        "status": "orchestration_ready",
        "requires_integration": "Ad concepts, copy, and creative briefs use your connected OpenAI key and work now. Launching live campaigns or spending budget still requires a connected ad-platform API and explicit per-launch approval -- the system never spends money automatically.",
        "capabilities": [
            "Ad concepts, copy, headlines, angles",
            "Creative briefs & A/B variations",
            "Campaign objective & budget planning",
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
        "requires_integration": "Needs connected platform analytics (or user-provided data exports) — it will tell you what's missing rather than inventing numbers.",
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
        "requires_integration": "Fully functional for task orchestration; quality of generated plans improves once the Scriptwriter/Trend Researcher integrations above are connected.",
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
        "label": "OpenAI API key (Scriptwriter / Trend Researcher / Ads Manager / Campaign Planner)",
        "env_vars": ["OPENAI_API_KEY"],
    },
    "video_processing": {
        "label": "Video processing engine (Video Editor / Color Grading)",
        "env_vars": ["VIDEO_PROCESSING_API_KEY"],
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
    spec = INTEGRATIONS.get(integration_key)
    if not spec:
        return False
    return all(os.environ.get(v) for v in spec["env_vars"])


SECRET_KEY = os.environ.get("CREATOR_HUB_SECRET_KEY", "dev-only-insecure-key-change-me")
DATABASE_PATH = os.environ.get(
    "CREATOR_HUB_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "creator_hub.db"),
)
