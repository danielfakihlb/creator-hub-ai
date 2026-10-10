from flask import Blueprint, render_template, request, redirect, url_for, flash

import access
import database as db

bp = Blueprint("brand", __name__)


@bp.route("/brand", methods=["GET", "POST"])
@access.login_required
def settings():
    user = access.current_user()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Give this brand profile a name.", "danger")
        else:
            db.create_brand_profile(
                user["id"],
                name=name,
                description=request.form.get("description", "").strip(),
                tone_of_voice=request.form.get("tone_of_voice", "").strip(),
                target_audience=request.form.get("target_audience", "").strip(),
                primary_color=request.form.get("primary_color", "").strip(),
                secondary_color=request.form.get("secondary_color", "").strip(),
                font_family=request.form.get("font_family", "").strip(),
                preferred_style=request.form.get("preferred_style", "").strip(),
            )
            flash("Brand profile saved.", "info")
        return redirect(url_for("brand.settings"))

    return render_template(
        "brand_settings.html", title="Brand Settings", active="brand",
        profiles=db.list_brand_profiles(user["id"]),
    )


@bp.route("/brand/<int:profile_id>/delete", methods=["POST"])
@access.login_required
def delete(profile_id):
    user = access.current_user()
    db.delete_brand_profile(user["id"], profile_id)
    flash("Brand profile removed.", "info")
    return redirect(url_for("brand.settings"))
