from flask import Blueprint, render_template, request, redirect, url_for, flash

import access
import database as db

bp = Blueprint("media", __name__)


@bp.route("/media", methods=["GET", "POST"])
@access.login_required
def library():
    user = access.current_user()

    if request.method == "POST":
        filename = request.form.get("filename", "").strip()
        media_type = request.form.get("media_type", "video")
        folder = request.form.get("folder", "Uploads").strip() or "Uploads"
        description = request.form.get("description", "").strip()
        tags = request.form.get("tags", "").strip()
        if not filename:
            flash("Give the asset a filename.", "danger")
        else:
            # Real byte storage requires a connected object-storage
            # integration; this records the asset entry (metadata, tags,
            # search) honestly without claiming a file was uploaded to
            # cloud storage that isn't configured.
            db.create_media_asset(user["id"], filename, media_type, folder, description, tags)
            flash("Asset added to your library.", "info")
        return redirect(url_for("media.library"))

    search = request.args.get("q", "").strip()
    folder = request.args.get("folder", "").strip()
    media_type = request.args.get("type", "").strip()
    return render_template(
        "media_library.html", title="Media Library", active="media",
        assets=db.list_media(user["id"], search=search or None, folder=folder or None, media_type=media_type or None),
        folders=db.list_folders(user["id"]),
        search=search, selected_folder=folder, selected_type=media_type,
    )
