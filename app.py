"""Creator Hub AI -- Flask application factory."""

import os
from flask import Flask, g

import config
import database as db
import access


def create_app():
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY
    # Video uploads (Video Studio) are real files, not text -- cap the
    # size so one huge upload can't exhaust this server's disk or memory.
    # 250MB comfortably covers a short raw clip for a demo.
    app.config["MAX_CONTENT_LENGTH"] = 250 * 1024 * 1024

    db.init_db()  # idempotent -- CREATE TABLE IF NOT EXISTS
    db.seed_demo_account()

    from blueprints.marketing import bp as marketing_bp
    from blueprints.auth import bp as auth_bp
    from blueprints.dashboard import bp as dashboard_bp
    from blueprints.agents import bp as agents_bp
    from blueprints.projects import bp as projects_bp
    from blueprints.media import bp as media_bp
    from blueprints.media_tools import bp as media_tools_bp
    from blueprints.brand import bp as brand_bp
    from blueprints.campaigns import bp as campaigns_bp
    from blueprints.billing import bp as billing_bp
    from blueprints.account import bp as account_bp
    from blueprints.admin import bp as admin_bp

    app.register_blueprint(marketing_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(agents_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(media_tools_bp)
    app.register_blueprint(brand_bp)
    app.register_blueprint(campaigns_bp)
    app.register_blueprint(billing_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(admin_bp)

    @app.context_processor
    def inject_globals():
        user = access.current_user()
        plan_slug = access.current_plan_slug()
        plan = config.PLANS.get(plan_slug) if plan_slug else None
        return {
            "g_user": user,
            "g_plan": plan,
            "cfg": config,
        }

    @app.errorhandler(403)
    def forbidden(e):
        return "403 Forbidden -- you don't have permission to view this.", 403

    @app.errorhandler(404)
    def not_found(e):
        return "404 -- that page doesn't exist.", 404

    @app.errorhandler(413)
    def too_large(e):
        return "413 -- that file is too large. The limit for a single upload is 250MB.", 413

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # Debug mode is for local development only -- it must stay OFF on any
    # publicly-reachable deployment (it would otherwise expose an
    # interactive Python debugger to the internet). It's on by default
    # here for convenience while building; set FLASK_DEBUG=0 to turn it
    # off, which any hosting provider should do automatically since they
    # won't set FLASK_DEBUG at all... so to be safe, we default debug OFF
    # unless FLASK_DEBUG=1 is explicitly set.
    debug_mode = os.environ.get("FLASK_DEBUG") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
