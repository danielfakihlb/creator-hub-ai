"""Cross-Site Request Forgery (CSRF) protection.

The real attack this guards against: a malicious site tricks a logged-in
user's browser into silently submitting a form to this app (e.g. "cancel
my subscription" or "approve this task" or "delete this brand profile")
without the user ever seeing or approving it -- just by getting them to
visit a page that auto-submits a hidden form pointed at this site. The
fix is a secret, per-session token that every real form on this site
includes, which an attacker's page has no way to know -- so a forged
submission is missing (or has a wrong) token and gets rejected before the
real route logic ever runs.

No external package is needed for this: Flask's own session (itself
protected by SECRET_KEY, see config.py) already gives us a safe place to
keep a random per-session token, and comparing it is a few lines.
"""
import secrets

from flask import session, request, abort

def csrf_token():
    """Jinja helper: returns this session's CSRF token, creating one on
    first use. Every real form on this site includes it once:
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    """
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]

def init_csrf_protection(app):
    """Registers a before_request check: every state-changing request
    (POST -- the only method this app's forms use, though PUT/PATCH/
    DELETE are covered too in case that ever changes) must carry a
    csrf_token field matching this session's token, or it's rejected
    with 400 before any route code runs. Make the Jinja helper available
    to every template too."""
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def _check_csrf():
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return None
        submitted = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not secrets.compare_digest(submitted, expected):
            abort(400, description="This form could not be verified -- your session may have expired. Please refresh the page and try again.")
        return None
