import os
import requests

from flask import Flask, redirect, request, session
from pymongo import MongoClient


app = Flask(__name__)


# =========================================================
# إعدادات Discord
# =========================================================

CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")

REDIRECT_URI = "https://diaa-bot-website-production.up.railway.app/callback"

DISCORD_API = "https://discord.com/api/v10"

# مفتاح الجلسة
app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "change-this-secret-key"
)


# =========================================================
# اتصال MongoDB
# =========================================================

MONGO_URI = os.getenv("MONGO_URI")

if not MONGO_URI:
    raise RuntimeError(
        "❌ MONGO_URI غير موجود في Environment Variables"
    )

mongo_client = MongoClient(MONGO_URI)

db = mongo_client["discord_bot_db"]

commands_collection = db["website_commands"]

website_settings = db["website_settings"]


# =========================================================
# الصفحة الرئيسية
# =========================================================

@app.route("/")
def home():

    if "user" in session:

        user = session["user"]

        username = (
            user.get("global_name")
            or user.get("username")
            or "مستخدم"
        )

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
                    padding: 25px;
                    max-width: 600px;
                    background: #171020;
                    border-radius: 15px;
                    border: 1px solid #2a1b3d;
                }}

                .button {{
                    display: inline-block;
                    padding: 14px 30px;
                    margin: 10px;
                    border-radius: 10px;
                    text-decoration: none;
                    color: white;
                    background: #8b3dff;
                    transition: 0.2s;
                }}

                .button:hover {{
                    background: #a45cff;
                    transform: translateY(-2px);
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
                        ⚙️ لوحة التحكم
                    </a>

                    <a class="button" href="/commands">
                        📋 أوامر البوت
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
                padding: 80px 20px;
            }

            h1 {
                color: #b66cff;
                font-size: 40px;
            }

            .box {
                max-width: 700px;
                margin: 30px auto;
                padding: 35px;
                background: #171020;
                border-radius: 15px;
                border: 1px solid #2a1b3d;
            }

            .button {
                display: inline-block;
                padding: 14px 30px;
                margin: 10px;
                border-radius: 10px;
                text-decoration: none;
                color: white;
                background: #8b3dff;
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

                <a class="button" href="/commands">
                    📋 عرض جميع الأوامر
                </a>

                <a class="button" href="/">
                    🏠 الرئيسية
                </a>

            </div>

        </div>

    </body>

    </html>
    """


# =========================================================
# صفحة جميع الأوامر
# =========================================================

@app.route("/commands")
def commands_page():

    if "user" not in session:
        return redirect("/login")

    try:

        commands_data = list(
            commands_collection.find(
                {},
                {
                    "_id": 0,
                    "name": 1,
                    "description": 1,
                    "aliases": 1
                }
            ).sort("name", 1)
        )

        settings = website_settings.find_one(
            {"_id": "commands"}
        )

    except Exception as error:

        print(
            f"❌ خطأ أثناء قراءة أوامر MongoDB: "
            f"{type(error).__name__}: {error}"
        )

        return """
        <h2 style="color:white;text-align:center;">
            حدث خطأ أثناء تحميل أوامر البوت.
        </h2>
        """, 500

    count = len(commands_data)

    updated_at = "غير معروف"

    if settings and settings.get("updated_at"):

        updated_at = str(
            settings["updated_at"]
        )

    command_cards = ""

    for command in commands_data:

        name = command.get(
            "name",
            "بدون اسم"
        )

        description = command.get(
            "description",
            "لا يوجد وصف لهذا الأمر."
        )

        aliases = command.get(
            "aliases",
            []
        )

        aliases_text = ""

        if aliases:

            aliases_text = (
                "<div class='aliases'>"
                "الاختصارات: "
                + " ، ".join(
                    f"<code>{alias}</code>"
                    for alias in aliases
                )
                + "</div>"
            )

        command_cards += f"""
        <div class="command-card">

            <div class="command-name">
                <code>-{name}</code>
            </div>

            <div class="command-description">
                {description}
            </div>

            {aliases_text}

        </div>
        """

    if not command_cards:

        command_cards = """
        <div class="empty">
            لم يتم العثور على أي أوامر.
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">

    <head>

        <meta charset="UTF-8">

        <meta name="viewport"
              content="width=device-width, initial-scale=1.0">

        <title>أوامر ضياء BOT</title>

        <style>

            * {{
                box-sizing: border-box;
            }}

            body {{
                margin: 0;
                background:
                    radial-gradient(
                        circle at top,
                        #211034,
                        #0d0915 55%
                    );
                color: white;
                font-family: Arial, sans-serif;
            }}

            .container {{
                width: min(1100px, 94%);
                margin: auto;
                padding: 50px 0;
            }}

            .header {{
                text-align: center;
                margin-bottom: 35px;
            }}

            .header h1 {{
                margin: 0;
                color: #b66cff;
                font-size: 42px;
            }}

            .header p {{
                color: #aaa;
                margin-top: 12px;
            }}

            .stats {{
                display: flex;
                justify-content: center;
                margin-bottom: 30px;
            }}

            .stat {{
                background: #171020;
                border: 1px solid #2a1b3d;
                border-radius: 15px;
                padding: 18px 35px;
                text-align: center;
            }}

            .stat-number {{
                display: block;
                color: #b66cff;
                font-size: 30px;
                font-weight: bold;
            }}

            .stat-label {{
                color: #aaa;
                font-size: 14px;
            }}

            .search {{
                width: 100%;
                padding: 16px 20px;
                margin-bottom: 25px;
                border: 1px solid #35214c;
                border-radius: 12px;
                background: #171020;
                color: white;
                outline: none;
                font-size: 16px;
            }}

            .search:focus {{
                border-color: #8b3dff;
            }}

            .commands {{
                display: grid;
                grid-template-columns:
                    repeat(auto-fit, minmax(280px, 1fr));
                gap: 18px;
            }}

            .command-card {{
                background: #171020;
                border: 1px solid #2a1b3d;
                border-radius: 16px;
                padding: 22px;
                transition: 0.2s;
            }}

            .command-card:hover {{
                transform: translateY(-3px);
                border-color: #8b3dff;
                box-shadow:
                    0 8px 30px rgba(139, 61, 255, 0.12);
            }}

            .command-name {{
                margin-bottom: 12px;
            }}

            .command-name code {{
                color: #c58cff;
                font-size: 20px;
                font-weight: bold;
            }}

            .command-description {{
                color: #ccc;
                line-height: 1.7;
            }}

            .aliases {{
                margin-top: 12px;
                color: #888;
                font-size: 13px;
            }}

            .aliases code {{
                color: #b66cff;
            }}

            .empty {{
                text-align: center;
                padding: 50px;
                background: #171020;
                border-radius: 15px;
                color: #aaa;
            }}

            .back {{
                display: block;
                width: fit-content;
                margin: 35px auto 0;
                padding: 13px 25px;
                border-radius: 10px;
                background: #8b3dff;
                color: white;
                text-decoration: none;
            }}

            @media (max-width: 600px) {{

                .header h1 {{
                    font-size: 32px;
                }}

                .commands {{
                    grid-template-columns: 1fr;
                }}

            }}

        </style>

    </head>

    <body>

        <div class="container">

            <div class="header">

                <h1>📋 أوامر ضياء BOT</h1>

                <p>
                    جميع الأوامر المسجلة في البوت
                </p>

            </div>

            <div class="stats">

                <div class="stat">

                    <span class="stat-number">
                        {count}
                    </span>

                    <span class="stat-label">
                        أمر مسجل
                    </span>

                </div>

            </div>

            <input
                id="search"
                class="search"
                type="text"
                placeholder="🔎 ابحث عن أمر..."
                oninput="searchCommands()"
            >

            <div id="commands" class="commands">

                {command_cards}

            </div>

            <a class="back" href="/dashboard">
                ⚙️ العودة إلى لوحة التحكم
            </a>

        </div>


        <script>

            function searchCommands() {{

                const search =
                    document
                    .getElementById("search")
                    .value
                    .toLowerCase()
                    .trim();

                const cards =
                    document.querySelectorAll(
                        ".command-card"
                    );

                cards.forEach(function(card) {{

                    const text =
                        card.textContent
                        .toLowerCase();

                    if (text.includes(search)) {{
                        card.style.display = "";
                    }} else {{
                        card.style.display = "none";
                    }}

                }});
            }}

        </script>

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

    port = int(
        os.getenv("PORT", "8080")
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
