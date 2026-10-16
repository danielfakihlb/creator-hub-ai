from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

import config
import database as db
from email_sender import send_email
from rate_limit import rate_limit

bp = Blueprint("auth", __name__)

# Shown on the Forgot Password page whenever a POST happens, regardless of
# whether the email actually matched an account -- this is deliberate:
# confirming or denying that an email exists would let anyone probe this
# app for which addresses have accounts on it.
_RESET_REQUESTED_MESSAGE = (
    "If an account exists for that email, we've sent a link to reset your password. "
    "It expires in 1 hour."
)


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


@bp.route("/forgot-password", methods=["GET", "POST"])
@rate_limit(max_attempts=5, window_seconds=3600,
            message="Too many password reset requests from this connection. Please wait a bit and try again.")
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        if not config.is_configured("email"):
            # Honest about an operational gap, not about any one account --
            # this message doesn't depend on whether the email matched
            # anyone, so it can't be used to probe which addresses exist.
            flash(
                "Password reset emails aren't set up for this site yet. "
                "Contact the site owner directly for help resetting your password.",
                "warning",
            )
            return render_template("auth/forgot_password.html", title="Forgot password")

        user = db.get_user_by_email(email) if email else None
        if user:
            token = db.create_password_reset_token(user["id"])
            reset_url = url_for("auth.reset_password", token=token, _external=True)
            body = (
                f"Hi {user['full_name']},\n\n"
                "Someone (hopefully you) asked to reset the password for your Creator Hub AI "
                f"account ({user['email']}).\n\n"
                f"Reset it here: {reset_url}\n\n"
                "This link expires in 1 hour and can only be used once. "
                "If you didn't request this, you can safely ignore this email -- "
                "your password hasn't been changed."
            )
            # Sent best-effort: whether this succeeds or fails, the page
            # shows the same neutral message below either way, so a failed
            # send can't be used to tell real accounts from nonexistent ones.
            send_email(user["email"], "Reset your Creator Hub AI password", body)

        flash(_RESET_REQUESTED_MESSAGE, "info")
        return render_template("auth/forgot_password.html", title="Forgot password")

    return render_template("auth/forgot_password.html", title="Forgot password")


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
@rate_limit(max_attempts=10, window_seconds=3600,
            message="Too many password reset attempts from this connection. Please wait a bit and try again.")
def reset_password(token):
    reset = db.get_valid_password_reset(token)
    if not reset:
        flash("This password reset link is invalid or has expired. Request a new one below.", "danger")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        error = None
        if len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords don't match."

        if error:
            flash(error, "danger")
            return render_template("auth/reset_password.html", title="Reset password", token=token)

        db.update_user_password(reset["user_id"], generate_password_hash(password))
        db.mark_password_reset_used(reset["id"])
        db.invalidate_password_resets_for_user(reset["user_id"])
        flash("Your password has been reset. Log in with your new password.", "info")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", title="Reset password", token=token)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("marketing.landing"))
