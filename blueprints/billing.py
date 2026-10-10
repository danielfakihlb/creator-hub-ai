from flask import Blueprint, render_template, redirect, url_for, flash, request

import config
import access
import database as db

bp = Blueprint("billing", __name__)


@bp.route("/pricing")
def pricing():
    rows = []
    for slug in config.AGENT_ORDER:
        agent = config.AGENTS[slug]
        rows.append({
            "agent": agent,
            "in_creator": config.plan_includes_agent("creator", slug),
            "in_full_studio": config.plan_includes_agent("full_studio", slug),
        })
    return render_template(
        "pricing.html", title="Pricing", plans=config.PLANS, plan_order=config.PLAN_ORDER, rows=rows,
    )


@bp.route("/billing/checkout/<plan>", methods=["GET", "POST"])
@access.login_required
def checkout(plan):
    user = access.current_user()
    if plan not in config.PLANS:
        flash("Unknown plan.", "danger")
        return redirect(url_for("billing.pricing"))

    stripe_ready = config.is_configured("stripe")

    if request.method == "POST":
        if stripe_ready:
            # Real checkout-session creation / webhook confirmation would
            # happen here once Stripe keys are connected. Until then this
            # branch is unreachable -- we never fabricate a successful
            # charge.
            flash("Payment integration is connected but not yet wired to a live checkout flow.", "warning")
            return redirect(url_for("billing.checkout", plan=plan))
        if request.form.get("confirm_test_mode") == "yes":
            db.set_subscription(user["id"], plan, status="active", payment_provider_ref="TEST-MODE-NO-REAL-CHARGE")
            flash(f"Test mode: {config.PLANS[plan]['name']} activated with no real charge. Connect Stripe to take real payments.", "info")
            return redirect(url_for("billing.subscription"))

    return render_template(
        "checkout.html", title="Checkout", active="billing",
        plan=config.PLANS[plan], stripe_ready=stripe_ready,
    )


@bp.route("/account/subscription")
@access.login_required
def subscription():
    user = access.current_user()
    sub = db.get_active_subscription(user["id"])
    plan = config.PLANS.get(sub["plan_slug"]) if sub else None
    return render_template(
        "subscription.html", title="Subscription", active="billing",
        subscription=sub, plan=plan, plans=config.PLANS, plan_order=config.PLAN_ORDER,
    )


@bp.route("/account/subscription/cancel", methods=["POST"])
@access.login_required
def cancel():
    user = access.current_user()
    db.cancel_subscription(user["id"])
    flash("Your subscription has been canceled. You'll keep access until the end of the current billing period in a real billing setup; agent access is restricted immediately in this build.", "warning")
    return redirect(url_for("billing.subscription"))
