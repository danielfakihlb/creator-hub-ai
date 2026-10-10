from flask import Blueprint, render_template

import config
import access

bp = Blueprint("account", __name__)


@bp.route("/account")
@access.login_required
def settings():
    integrations_status = {
        key: {"label": spec["label"], "configured": config.is_configured(key), "env_vars": spec["env_vars"]}
        for key, spec in config.INTEGRATIONS.items()
    }
    return render_template(
        "account_settings.html", title="Account Settings", active="account",
        integrations_status=integrations_status,
    )
