from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

import config
import database as db
from rate_limit import rate_limit

bp = Blueprint("auth", __name__)


@bp.route("/signup", methods=["GET", "POST"])
@rate_limit(max_attempts=8, window_seconds=3600,
            message="Too many signup attempts from this connection. Please wait a bit and try again.")
def signup():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        chosen_plan = request.form.get("plan") or request.args.get("plan")

        error = None
        if not full_name or not email or not password:
            error = "Please fill in every field."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif db.get_user_by_email(email):
            error = "An account with that email already exists."

        if error:
            flash(error, "danger")
            return render_template("auth/signup.html", title="Create your account", plans=config.PLANS, selected_plan=chosen_plan)

        user_id = db.create_user(email, generate_password_hash(password), full_name)

        # No payment provider is connected yet (see billing blueprint), so a
        # chosen plan is recorded as 'payment_required' rather than silently
        # granting paid access -- we never claim a subscription is active
        # until a real payment confirms it.
        if chosen_plan in config.PLANS:
            db.set_subscription(user_id, chosen_plan, status="payment_required")

        session["user_id"] = user_id
        flash("Welcome to Creator Hub AI.", "info")
        if chosen_plan in config.PLANS:
            return redirect(url_for("billing.checkout", plan=chosen_plan))
        return redirect(url_for("dashboard.home"))

    chosen_plan = request.args.get("plan")
    return render_template("auth/signup.html", title="Create your account", plans=config.PLANS, selected_plan=chosen_plan)


@bp.route("/login", methods=["GET", "POST"])
@rate_limit(max_attempts=8, window_seconds=60,
            message="Too many login attempts from this connection. Please wait a minute and try again.")
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = db.get_user_by_email(email)
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Incorrect email or password.", "danger")
            return render_template("auth/login.html", title="Log in")
        session["user_id"] = user["id"]
        next_url = request.args.get("next") or url_for("dashboard.home")
        return redirect(next_url)

    return render_template("auth/login.html", title="Log in")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("marketing.landing"))
