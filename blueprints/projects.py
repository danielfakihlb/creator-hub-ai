from flask import Blueprint, render_template, request, redirect, url_for, flash

import access
import database as db

bp = Blueprint("projects", __name__)


@bp.route("/projects")
@access.login_required
def list_view():
    user = access.current_user()
    return render_template(
        "projects_list.html", title="Projects", active="projects",
        projects=db.list_projects(user["id"]),
    )


@bp.route("/projects/new", methods=["GET", "POST"])
@access.login_required
def create():
    user = access.current_user()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        if not name:
            flash("Give your project a name.", "danger")
        else:
            brand_profiles = db.list_brand_profiles(user["id"])
            brand_id = request.form.get("brand_profile_id") or None
            project_id = db.create_project(user["id"], name, description, brand_id)
            return redirect(url_for("projects.detail", project_id=project_id))
    return render_template(
        "project_new.html", title="New project", active="projects",
        brand_profiles=db.list_brand_profiles(user["id"]),
    )


@bp.route("/projects/<int:project_id>")
@access.login_required
def detail(project_id):
    user = access.current_user()
    project = db.get_project(user["id"], project_id)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("projects.list_view"))
    return render_template(
        "project_detail.html", title=project["name"], active="projects",
        project=project,
        media=db.list_media(user["id"])[:8],
        campaigns=[c for c in db.list_campaigns(user["id"]) if c["project_id"] == project_id],
        tasks=db.list_agent_tasks(user["id"], limit=50),
    )
