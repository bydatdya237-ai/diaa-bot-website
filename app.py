import os
from urllib.parse import urlencode

import requests

from flask import (
    Flask,
    redirect,
    request,
    session,
)

from pymongo import MongoClient


# =========================================================
# Flask
# =========================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "change-this-secret-key"
)


# =========================================================
# Discord
# =========================================================

CLIENT_ID = os.getenv(
    "DISCORD_CLIENT_ID"
)

CLIENT_SECRET = os.getenv(
    "DISCORD_CLIENT_SECRET"
)

REDIRECT_URI = (
    "https://diaa-bot-website-production.up.railway.app/callback"
)

DISCORD_API = (
    "https://discord.com/api/v10"
)


# =========================================================
# MongoDB
# =========================================================

MONGO_URI = os.getenv(
    "MONGO_URI"
)

if not MONGO_URI:

    raise RuntimeError(
        "❌ MONGO_URI غير موجود"
    )

mongo_client = MongoClient(
    MONGO_URI
)

db = mongo_client[
    "discord_bot_db"
]

commands_collection = db[
    "website_commands"
]

guilds_collection = db[
    "website_guilds"
]

settings_collection = db[
    "website_command_settings"
]


# =========================================================
# HTML
# =========================================================

def page_style():

    return """
    <style>

    * {
        box-sizing: border-box;
    }

    body {
        margin: 0;
        font-family: Arial, sans-serif;
        background:
            linear-gradient(
                135deg,
                #12051f,
                #241044,
                #13051f
            );
        color: white;
        min-height: 100vh;
    }

    .container {
        width: 92%;
        max-width: 1100px;
        margin: auto;
        padding: 30px 0;
    }

    .navbar {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 18px 25px;
        background: rgba(255,255,255,0.06);
        border: 1px solid rgba(255,255,255,0.1);
        border-radius: 18px;
        margin-bottom: 25px;
    }

    .logo {
        font-size: 24px;
        font-weight: bold;
    }

    .btn {
        display: inline-block;
        padding: 12px 20px;
        border-radius: 12px;
        text-decoration: none;
        border: none;
        cursor: pointer;
        color: white;
        background: #8b5cf6;
        font-size: 15px;
    }

    .btn:hover {
        opacity: .85;
    }

    .btn.red {
        background: #dc2626;
    }

    .btn.gray {
        background: #374151;
    }

    .hero {
        padding: 45px 25px;
        text-align: center;
        background: rgba(255,255,255,0.05);
        border-radius: 22px;
        border: 1px solid rgba(255,255,255,0.1);
    }

    .hero h1 {
        font-size: 40px;
        margin-bottom: 10px;
    }

    .hero p {
        color: #d1d5db;
        font-size: 17px;
    }

    .cards {
        display: grid;
        grid-template-columns:
            repeat(auto-fit, minmax(230px, 1fr));
        gap: 18px;
        margin-top: 25px;
    }

    .card {
        background: rgba(255,255,255,0.06);
        border: 1px solid rgba(255,255,255,0.1);
        border-radius: 18px;
        padding: 22px;
    }

    .command {
        margin-top: 18px;
    }

    .command-name {
        font-size: 23px;
        font-weight: bold;
        color: #c4b5fd;
    }

    .description {
        color: #d1d5db;
        margin: 10px 0;
    }

    .badge {
        display: inline-block;
        background: #312e81;
        padding: 6px 10px;
        border-radius: 9px;
        margin: 3px;
        font-size: 13px;
    }

    input, select {
        width: 100%;
        padding: 13px;
        margin-top: 8px;
        margin-bottom: 15px;
        border-radius: 10px;
        border: 1px solid #4b5563;
        background: #111827;
        color: white;
    }

    label {
        font-weight: bold;
    }

    .section {
        margin-top: 25px;
    }

    .check {
        display: block;
        padding: 8px;
        background: rgba(255,255,255,.04);
        border-radius: 8px;
        margin: 5px 0;
    }

    .empty {
        text-align: center;
        color: #9ca3af;
        padding: 40px;
    }

    </style>
    """


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
            or "المستخدم"
        )

        return f"""
        <!DOCTYPE html>

        <html lang="ar" dir="rtl">

        <head>

            <meta charset="UTF-8">

            <meta
                name="viewport"
                content="width=device-width,
                initial-scale=1.0"
            >

            <title>ضياء BOT</title>

            {page_style()}

        </head>

        <body>

        <div class="container">

            <div class="navbar">

                <div class="logo">
                    🤖 ضياء BOT
                </div>

                <a
                    class="btn red"
                    href="/logout"
                >
                    تسجيل الخروج
                </a>

            </div>

            <div class="hero">

                <h1>
                    أهلاً {username} 👋
                </h1>

                <p>
                    لوحة تحكم ضياء BOT
                </p>

                <br>

                <a
                    class="btn"
                    href="/dashboard"
                >
                    لوحة التحكم
                </a>

                <a
                    class="btn"
                    href="/commands"
                >
                    أوامر البوت
                </a>

            </div>

        </div>

        </body>

        </html>
        """

    return f"""
    <!DOCTYPE html>

    <html lang="ar" dir="rtl">

    <head>

        <meta charset="UTF-8">

        <meta
            name="viewport"
            content="width=device-width,
            initial-scale=1.0"
        >

        <title>ضياء BOT</title>

        {page_style()}

    </head>

    <body>

    <div class="container">

        <div class="hero">

            <h1>
                🤖 ضياء BOT
            </h1>

            <p>
                الموقع الرسمي لبوت ضياء
            </p>

            <br>

            <a
                class="btn"
                href="/login"
            >
                تسجيل الدخول بواسطة Discord
            </a>

        </div>

    </div>

    </body>

    </html>
    """


# =========================================================
# تسجيل الدخول
# =========================================================

@app.route("/login")
def login():

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": "identify",
    }

    discord_url = (
        "https://discord.com/oauth2/authorize?"
        + urlencode(params)
    )

    return redirect(
        discord_url
    )


# =========================================================
# Callback
# =========================================================

@app.route("/callback")
def callback():

    code = request.args.get(
        "code"
    )

    if not code:

        return (
            "لم يتم استلام رمز تسجيل الدخول.",
            400
        )

    data = {

        "client_id": CLIENT_ID,

        "client_secret": CLIENT_SECRET,

        "grant_type":
            "authorization_code",

        "code": code,

        "redirect_uri":
            REDIRECT_URI,
    }

    headers = {
        "Content-Type":
            "application/x-www-form-urlencoded"
    }

    token_response = requests.post(

        f"{DISCORD_API}/oauth2/token",

        data=data,

        headers=headers,

        timeout=15
    )

    if token_response.status_code != 200:

        return (
            "حدث خطأ أثناء تسجيل الدخول بواسطة Discord.",
            400
        )

    token_data = (
        token_response.json()
    )

    access_token = (
        token_data.get(
            "access_token"
        )
    )

    if not access_token:

        return (
            "لم يتم الحصول على Access Token.",
            400
        )

    user_response = requests.get(

        f"{DISCORD_API}/users/@me",

        headers={
            "Authorization":
                f"Bearer {access_token}"
        },

        timeout=15
    )

    if user_response.status_code != 200:

        return (
            "تعذر الحصول على معلومات حساب Discord.",
            400
        )

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

    user_id = str(
        session["user"]["id"]
    )

    guilds = list(
        guilds_collection.find({
            "owner_id": user_id
        })
    )

    html = ""

    for guild in guilds:

        html += f"""
        <div class="card">

            <h2>
                🏠 {guild.get("guild_name", "سيرفر")}
            </h2>

            <p>
                الرومات:
                {len(guild.get("channels", []))}
            </p>

            <p>
                الرتب:
                {len(guild.get("roles", []))}
            </p>

            <a
                class="btn"
                href="/commands?guild={guild["guild_id"]}"
            >
                إدارة الأوامر
            </a>

        </div>
        """

    if not html:

        html = """
        <div class="empty">

            لا يوجد سيرفر أنت مالكه
            والبوت موجود فيه حاليًا.

        </div>
        """

    return f"""
    <!DOCTYPE html>

    <html lang="ar" dir="rtl">

    <head>

        <meta charset="UTF-8">

        <meta
            name="viewport"
            content="width=device-width,
            initial-scale=1.0"
        >

        <title>لوحة التحكم</title>

        {page_style()}

    </head>

    <body>

    <div class="container">

        <div class="navbar">

            <div class="logo">
                🤖 لوحة التحكم
            </div>

            <a
                class="btn gray"
                href="/"
            >
                الرئيسية
            </a>

        </div>

        <h1>
            سيرفراتك
        </h1>

        <div class="cards">

            {html}

        </div>

    </div>

    </body>

    </html>
    """


# =========================================================
# الأوامر
# =========================================================

@app.route("/commands")
def commands_page():

    if "user" not in session:

        return redirect("/login")

    user_id = str(
        session["user"]["id"]
    )

    guild_id = request.args.get(
        "guild"
    )

    # -----------------------------------------------------
    # إذا ما اختار سيرفر
    # -----------------------------------------------------

    if not guild_id:

        guilds = list(
            guilds_collection.find({
                "owner_id": user_id
            })
        )

        cards = ""

        for guild in guilds:

            cards += f"""
            <div class="card">

                <h2>
                    🏠 {guild["guild_name"]}
                </h2>

                <a
                    class="btn"
                    href="/commands?guild={guild["guild_id"]}"
                >
                    إدارة الأوامر
                </a>

            </div>
            """

        return f"""
        <!DOCTYPE html>

        <html lang="ar" dir="rtl">

        <head>

            <meta charset="UTF-8">

            <title>الأوامر</title>

            {page_style()}

        </head>

        <body>

        <div class="container">

            <div class="navbar">

                <div class="logo">
                    أوامر البوت
                </div>

                <a
                    class="btn gray"
                    href="/dashboard"
                >
                    رجوع
                </a>

            </div>

            <div class="cards">

                {cards or
                '<div class="empty">لا توجد سيرفرات.</div>'}

            </div>

        </div>

        </body>

        </html>
        """

    # -----------------------------------------------------
    # تحقق أن السيرفر للمستخدم
    # -----------------------------------------------------

    guild = guilds_collection.find_one({
        "guild_id": str(guild_id),
        "owner_id": user_id
    })

    if not guild:

        return (
            "غير مصرح لك بإدارة هذا السيرفر.",
            403
        )

    # -----------------------------------------------------
    # الأوامر
    # -----------------------------------------------------

    commands_list = list(
        commands_collection.find({}).sort(
            "name",
            1
        )
    )

    # -----------------------------------------------------
    # البحث
    # -----------------------------------------------------

    search = request.args.get(
        "search",
        ""
    ).strip().lower()

    if search:

        commands_list = [

            command
            for command in commands_list

            if search in command.get(
                "name",
                ""
            ).lower()

        ]

    # -----------------------------------------------------
    # HTML
    # -----------------------------------------------------

    cards = ""

    for command in commands_list:

        command_name = command.get(
            "name",
            ""
        )

        description = command.get(
            "description",
            "لا يوجد وصف."
        )

        aliases = command.get(
            "aliases",
            []
        )

        setting = settings_collection.find_one({
            "guild_id": str(guild_id),
            "command_name": command_name
        })

        enabled = (
            setting.get(
                "enabled",
                False
            )
            if setting
            else False
        )

        selected_channels = (
            setting.get(
                "channel_ids",
                []
            )
            if setting
            else []
        )

        selected_roles = (
            setting.get(
                "role_ids",
                []
            )
            if setting
            else []
        )

        aliases_html = ""

        for alias in aliases:

            aliases_html += (
                f'<span class="badge">'
                f'{alias}'
                f'</span>'
            )

        channels_html = ""

        for channel in guild.get(
            "channels",
            []
        ):

            checked = (
                "checked"
                if str(channel["id"])
                in [
                    str(x)
                    for x in selected_channels
                ]
                else ""
            )

            channels_html += f"""
            <label class="check">

                <input
                    type="checkbox"
                    name="channel_ids"
                    value="{channel["id"]}"
                    {checked}
                >

                #{channel["name"]}

            </label>
            """

        roles_html = ""

        for role in guild.get(
            "roles",
            []
        ):

            checked = (
                "checked"
                if str(role["id"])
                in [
                    str(x)
                    for x in selected_roles
                ]
                else ""
            )

            roles_html += f"""
            <label class="check">

                <input
                    type="checkbox"
                    name="role_ids"
                    value="{role["id"]}"
                    {checked}
                >

                {role["name"]}

            </label>
            """

        cards += f"""
        <div class="card command">

            <div class="command-name">
                {command_name}
            </div>

            <div class="description">
                {description}
            </div>

            <div>
                {aliases_html}
            </div>

            <br>

            <form
                method="POST"
                action="/save_command"
            >

                <input
                    type="hidden"
                    name="guild_id"
                    value="{guild_id}"
                >

                <input
                    type="hidden"
                    name="command_name"
                    value="{command_name}"
                >

                <div class="section">

                    <label>
                        التحكم بالأمر
                    </label>

                    <select name="enabled">

                        <option
                            value="false"
                            {"selected" if not enabled else ""}
                        >
                            غير مفعل
                        </option>

                        <option
                            value="true"
                            {"selected" if enabled else ""}
                        >
                            مفعل
                        </option>

                    </select>

                </div>

                <div class="section">

                    <label>
                        الرومات المسموح فيها
                    </label>

                    {channels_html}

                </div>

                <div class="section">

                    <label>
                        الرتب المسموح لها
                    </label>

                    {roles_html}

                </div>

                <br>

                <button
                    class="btn"
                    type="submit"
                >
                    💾 حفظ إعدادات الأمر
                </button>

            </form>

        </div>
        """

    return f"""
    <!DOCTYPE html>

    <html lang="ar" dir="rtl">

    <head>

        <meta charset="UTF-8">

        <meta
            name="viewport"
            content="width=device-width,
            initial-scale=1.0"
        >

        <title>إدارة الأوامر</title>

        {page_style()}

    </head>

    <body>

    <div class="container">

        <div class="navbar">

            <div class="logo">
                ⚙️ إدارة الأوامر
            </div>

            <a
                class="btn gray"
                href="/dashboard"
            >
                رجوع
            </a>

        </div>

        <div class="card">

            <h2>
                🏠 {guild["guild_name"]}
            </h2>

            <form method="GET">

                <input
                    type="hidden"
                    name="guild"
                    value="{guild_id}"
                >

                <input
                    name="search"
                    placeholder="🔎 ابحث عن أمر..."
                    value="{search}"
                >

                <button
                    class="btn"
                    type="submit"
                >
                    بحث
                </button>

            </form>

        </div>

        <div class="cards">

            {cards or
            '<div class="empty">لا توجد أوامر.</div>'}

        </div>

    </div>

    </body>

    </html>
    """


# =========================================================
# حفظ إعدادات الأمر
# =========================================================

@app.route(
    "/save_command",
    methods=["POST"]
)
def save_command():

    if "user" not in session:

        return redirect("/login")

    user_id = str(
        session["user"]["id"]
    )

    guild_id = request.form.get(
        "guild_id"
    )

    command_name = request.form.get(
        "command_name"
    )

    # -----------------------------------------------------
    # تحقق من الملكية
    # -----------------------------------------------------

    guild = guilds_collection.find_one({
        "guild_id": str(guild_id),
        "owner_id": user_id
    })

    if not guild:

        return (
            "غير مصرح لك.",
            403
        )

    # -----------------------------------------------------
    # البيانات
    # -----------------------------------------------------

    enabled = (
        request.form.get(
            "enabled"
        )
        == "true"
    )

    channel_ids = request.form.getlist(
        "channel_ids"
    )

    role_ids = request.form.getlist(
        "role_ids"
    )

    # -----------------------------------------------------
    # الحفظ
    # -----------------------------------------------------

    settings_collection.update_one(

        {
            "guild_id":
                str(guild_id),

            "command_name":
                command_name,
        },

        {
            "$set": {

                "guild_id":
                    str(guild_id),

                "command_name":
                    command_name,

                "enabled":
                    enabled,

                "channel_ids":
                    channel_ids,

                "role_ids":
                    role_ids,

                "updated_at":
                    __import__(
                        "datetime"
                    ).datetime.now(
                        __import__(
                            "datetime"
                        ).timezone.utc
                    ),
            }
        },

        upsert=True
    )

    return redirect(
        f"/commands?guild={guild_id}"
    )


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
        os.getenv(
            "PORT",
            "8080"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
