import os
import requests

from flask import Flask, redirect, request, session


app = Flask(__name__)

# =========================================================
# إعدادات Discord
# =========================================================

CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")

REDIRECT_URI = "https://diaa-bot-website-production.up.railway.app/callback"

DISCORD_API = "https://discord.com/api/v10"

# مفتاح للجلسة
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-secret-key")


# =========================================================
# الصفحة الرئيسية
# =========================================================

@app.route("/")
def home():

    if "user" in session:
        user = session["user"]

        username = user.get("global_name") or user.get("username")

        return f"""
        <!DOCTYPE html>
        <html lang="ar" dir="rtl">

        <head>
            <meta charset="UTF-8">
            <meta name="viewport"
                  content="width=device-width, initial-scale=1.0">

            <title>ضياء BOT</title>

            <style>
                * {{
                    box-sizing: border-box;
                }}

                body {{
                    margin: 0;
                    background: #0d0915;
                    color: white;
                    font-family: Arial, sans-serif;
                    text-align: center;
                }}

                .container {{
                    padding: 100px 20px;
                }}

                h1 {{
                    font-size: 45px;
                    color: #b66cff;
                }}

                .user {{
                    margin: 25px auto;
                    padding: 20px;
                    max-width: 500px;
                    background: #171020;
                    border-radius: 15px;
                }}

                .button {{
                    display: inline-block;
                    padding: 14px 30px;
                    margin: 10px;
                    border-radius: 10px;
                    text-decoration: none;
                    color: white;
                    background: #8b3dff;
                }}

                .logout {{
                    background: #3a263f;
                }}
            </style>
        </head>

        <body>

            <div class="container">

                <h1>ضياء BOT 🤖</h1>

                <div class="user">

                    <h2>أهلاً بك، {username}</h2>

                    <p>
                        تم تسجيل دخولك بواسطة Discord بنجاح.
                    </p>

                    <a class="button" href="/dashboard">
                        لوحة التحكم
                    </a>

                    <a class="button logout" href="/logout">
                        تسجيل الخروج
                    </a>

                </div>

            </div>

        </body>
        </html>
        """

    return """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">

    <head>

        <meta charset="UTF-8">

        <meta name="viewport"
              content="width=device-width, initial-scale=1.0">

        <title>ضياء BOT</title>

        <style>

            * {
                box-sizing: border-box;
            }

            body {
                margin: 0;
                background: #0d0915;
                color: white;
                font-family: Arial, sans-serif;
                text-align: center;
            }

            .container {
                padding: 110px 20px;
            }

            h1 {
                font-size: 50px;
                color: #b66cff;
            }

            p {
                color: #cccccc;
                font-size: 19px;
            }

            .button {
                display: inline-block;
                margin-top: 30px;
                padding: 15px 35px;
                border-radius: 12px;
                background: #8b3dff;
                color: white;
                text-decoration: none;
                font-size: 18px;
            }

            .button:hover {
                background: #a45cff;
            }

        </style>

    </head>

    <body>

        <div class="container">

            <h1>ضياء BOT 🤖</h1>

            <p>
                أهلاً بك في الموقع الرسمي للبوت
            </p>

            <a class="button" href="/login">
                🔵 تسجيل الدخول بواسطة Discord
            </a>

        </div>

    </body>

    </html>
    """


# =========================================================
# تسجيل الدخول
# =========================================================

@app.route("/login")
def login():

    discord_url = (
        "https://discord.com/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        "&response_type=code"
        f"&redirect_uri={REDIRECT_URI}"
        "&scope=identify"
    )

    return redirect(discord_url)


# =========================================================
# Discord Callback
# =========================================================

@app.route("/callback")
def callback():

    code = request.args.get("code")

    if not code:
        return "لم يتم استلام رمز تسجيل الدخول.", 400

    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }

    token_response = requests.post(
        f"{DISCORD_API}/oauth2/token",
        data=data,
        headers=headers,
        timeout=15
    )

    if token_response.status_code != 200:
        return "حدث خطأ أثناء تسجيل الدخول بواسطة Discord.", 400

    token_data = token_response.json()

    access_token = token_data.get("access_token")

    if not access_token:
        return "لم يتم الحصول على Access Token.", 400

    # الحصول على معلومات المستخدم
    user_response = requests.get(
        f"{DISCORD_API}/users/@me",
        headers={
            "Authorization": f"Bearer {access_token}"
        },
        timeout=15
    )

    if user_response.status_code != 200:
        return "تعذر الحصول على معلومات حساب Discord.", 400

    user = user_response.json()

    session["user"] = user

    return redirect("/")


# =========================================================
# لوحة التحكم
# =========================================================

@app.route("/dashboard")
def dashboard():

    if "user" not in session:
        return redirect("/login")

    return """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">

    <head>

        <meta charset="UTF-8">

        <meta name="viewport"
              content="width=device-width, initial-scale=1.0">

        <title>لوحة التحكم</title>

        <style>

            body {
                margin: 0;
                background: #0d0915;
                color: white;
                font-family: Arial, sans-serif;
                text-align: center;
            }

            .container {
                padding: 80px 20px;
            }

            h1 {
                color: #b66cff;
            }

            .box {
                max-width: 600px;
                margin: 30px auto;
                padding: 30px;
                background: #171020;
                border-radius: 15px;
            }

        </style>

    </head>

    <body>

        <div class="container">

            <h1>لوحة تحكم ضياء BOT ⚙️</h1>

            <div class="box">

                <h2>مرحباً بك 👋</h2>

                <p>
                    تم تسجيل دخولك بنجاح.
                </p>

                <p>
                    لوحة السيرفرات والصلاحيات سيتم إضافتها هنا.
                </p>

            </div>

        </div>

    </body>

    </html>
    """


# =========================================================
# تسجيل الخروج
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect("/")


# =========================================================
# تشغيل الموقع
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=8080
    )
