import secrets

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from werkzeug.security import generate_password_hash

import config
import access
import database as db

bp = Blueprint("admin", __name__)


@bp.route("/admin")
@access.admin_required
def overview():
    """An agency admin's own team page -- scoped to their own agency only.
    Shows their teammates, the agency's shared plan (one subscription now
    covers the whole team, not each person separately), and -- on the
    Agency plan only -- lets the admin update their branding (logo/name
    shown instead of "Creator Hub AI") and invite new teammates. Never
    shows another agency's data -- the cross-agency view is the separate
    platform-owner page below."""
    user = access.current_user()
    agency = db.get_agency(user["agency_id"]) if user["agency_id"] else None
    members = db.list_agency_members(user["agency_id"]) if user["agency_id"] else []
    subscription = db.get_active_subscription(user["id"])
    plan_slug = subscription["plan_slug"] if subscription else None

    return render_template(
        "admin.html", title="Admin", active="admin",
        agency=agency,
        members=members,
        subscription=subscription,
        has_agency_features=config.plan_allows_agency_features(plan_slug),
        plans=config.PLANS, plan_order=config.PLAN_ORDER,
        agents=[config.AGENTS[s] for s in config.AGENT_ORDER],
        integrations=config.INTEGRATIONS,
        integrations_status={k: config.is_configured(k) for k in config.INTEGRATIONS},
    )


def _require_agency_features():
    """Shared guard for the two agency-plan-only actions below (branding
    and invites). Returns a redirect response if the acting user's agency
    isn't on a plan that grants these, else None. Checked here rather than
    only hidden in the template, since a hidden form is never real access
    control -- the spec's own rule (enforce on the backend, not just the
    UI) applies here exactly like it does to agent access in access.py."""
    user = access.current_user()
    subscription = db.get_active_subscription(user["id"])
    plan_slug = subscription["plan_slug"] if subscription else None
    if not config.plan_allows_agency_features(plan_slug):
        flash(
            f"Custom branding and inviting teammates are part of the {config.PLANS['full_studio']['name']} plan "
            f"(${config.PLANS['full_studio']['price_usd']}/mo). Upgrade to unlock them for your whole team.",
            "warning",
        )
        return redirect(url_for("billing.pricing"))
    return None


@bp.route("/admin/branding", methods=["POST"])
@access.admin_required
def update_branding():
    guard = _require_agency_features()
    if guard:
        return guard
    user = access.current_user()
    if not user["agency_id"]:
        abort(404)
    name = request.form.get("name", "").strip()
    logo_url = request.form.get("logo_url", "").strip() or None
    if not name:
        flash("Agency name can't be empty.", "danger")
        return redirect(url_for("admin.overview"))
    db.update_agency(user["agency_id"], name=name, logo_url=logo_url)
    flash("Branding updated.", "info")
    return redirect(url_for("admin.overview"))


@bp.route("/admin/invite", methods=["POST"])
@access.admin_required
def invite_teammate():
    """Adds a new teammate directly into the admin's own agency -- an
    Agency-plan feature, enforced by _require_agency_features() above. No
    email service is required for this (config.is_configured('email') may
    well be False, same gap as password-reset emails) -- the admin sets a
    temporary password here and tells the teammate directly, who can
    change it from their account page afterward. The new teammate always
    joins the SAME agency as the admin inviting them, is_admin=False, and
    can never be made a platform owner this way."""
    guard = _require_agency_features()
    if guard:
        return guard
    user = access.current_user()
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip().lower()
    temp_password = request.form.get("temp_password", "").strip()

    if not temp_password:
        temp_password = secrets.token_urlsafe(9)

    error = None
    if not full_name or not email:
        error = "Please fill in the teammate's name and email."
    elif len(temp_password) < 8:
        error = "Temporary password must be at least 8 characters."
    elif db.get_user_by_email(email):
        error = "An account with that email already exists."

    if error:
        flash(error, "danger")
        return redirect(url_for("admin.overview"))

    db.create_user(email, generate_password_hash(temp_password), full_name, user["agency_id"], is_admin=False)
    flash(
        f"{full_name} was added to your team. Share these sign-in details with them: "
        f"{email} / {temp_password} (they can change the password from Account Settings).",
        "info",
    )
    return redirect(url_for("admin.overview"))


@bp.route("/admin/platform")
@access.platform_owner_required
def platform_overview():
    """Daniel's own cross-agency view -- every agency on the platform at
    once, with member counts and current plan. The one page in the app
    that isn't scoped to a single agency, gated by platform_owner_required
    rather than admin_required."""
    agencies = db.list_agencies()
    return render_template(
        "admin_platform.html", title="Platform Admin", active="platform_admin",
        agencies=agencies, plans=config.PLANS,
    )


@bp.route("/admin/platform/<int:agency_id>/toggle-active", methods=["POST"])
@access.platform_owner_required
def toggle_agency_active(agency_id):
    agency = db.get_agency(agency_id)
    if not agency:
        flash("That agency no longer exists.", "danger")
        return redirect(url_for("admin.platform_overview"))
    db.update_agency(agency_id, is_active=0 if agency["is_active"] else 1)
    flash(f"{agency['name']} is now {'suspended' if agency['is_active'] else 'active'}.", "info")
    return redirect(url_for("admin.platform_overview"))
