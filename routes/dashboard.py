from flask import Blueprint, render_template, redirect, session, url_for


dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
def dashboard():
    user = session.get("discord_user")

    if not user:
        return redirect(url_for("auth.login"))

    return render_template(
        "dashboard.html",
        user=user
    )
