from flask import Blueprint, redirect, session, url_for, request
import requests

from core.auth import get_discord_oauth_url, exchange_code


auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login")
def login():
    return redirect(get_discord_oauth_url())


@auth_bp.route("/callback")
def callback():
    code = request.args.get("code")

    if not code:
        return redirect(url_for("home"))

    token_data = exchange_code(code)

    if not token_data:
        return "فشل تسجيل الدخول عبر Discord", 400

    access_token = token_data.get("access_token")

    if not access_token:
        return "لم يتم الحصول على Access Token", 400

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    try:
        user_response = requests.get(
            "https://discord.com/api/users/@me",
            headers=headers,
            timeout=10
        )
    except requests.RequestException:
        return "تعذر الاتصال بـ Discord", 500

    if user_response.status_code != 200:
        return "تعذر الحصول على بيانات حساب Discord", 400

    user_data = user_response.json()

    session["discord_user"] = user_data
    session["access_token"] = access_token

    return redirect(url_for("dashboard.dashboard"))
