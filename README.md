# Creator Hub AI

All-in-one AI content creation platform -- foundation build (stage 1 of the roadmap in the brief).

## Run it

```
pip install -r requirements.txt
python3 app.py
```

Then open http://localhost:5000. A demo account is seeded automatically:

- **demo@creatorhub.ai** / **demo12345** -- Full Studio plan, admin access
- Sign up for a new account to try the Creator plan and the upgrade-gate flow

## What's real vs. what needs connecting

Everything in this build is real, working code -- routes, database, auth, and the
subscription/permission system all run for real, nothing is mocked. What's
*not* connected yet is external paid services, by design (see `config.py` ->
`INTEGRATIONS`): Stripe, a language model API, video/audio processing, and
social/ad platform APIs. Every place that needs one of these says so clearly
in the UI (Account Settings -> Integrations, and on each agent's page)
instead of faking a result. Add the relevant environment variable and the
corresponding `is_configured()` check flips on.

## Where things are

- `config.py` -- the single source of truth for subscription plans and which
  agents each plan unlocks. Move an agent between plans by editing one list
  here; nothing else in the app needs to change.
- `access.py` -- login/admin/agent-access decorators. This is where backend
  permission enforcement lives (the UI never does this alone).
- `database.py` / `schema.sql` -- SQLite data layer. Ports conceptually to
  Postgres later; the schema is written to be compatible.
- `blueprints/` -- one file per section of the app (auth, dashboard, agents,
  projects, media, brand, campaigns, billing, account, admin).
- `templates/` -- server-rendered Jinja2 pages, dark-mode design system in
  `static/css/main.css` (no external fonts/CDNs -- fully self-contained).

## Stack note

This was built on Flask + SQLite instead of the suggested Next.js/Postgres
stack because the build environment had no access to npm/pip package
registries at the time -- Flask/Jinja2/Werkzeug were already available, so
this is real, tested, running code rather than untested Next.js source. The
database schema, permission model, and route structure all carry over
conceptually if you want this rebuilt on Next.js + Postgres later.

## Not yet built (next stages)

Per the roadmap: real external integrations (payments, LLM calls, video
rendering, social publishing, ads), an in-app admin editor for plan/agent
assignment (currently edited in `config.py`), file upload storage, and
production deployment config.
