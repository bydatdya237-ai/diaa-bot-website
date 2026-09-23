import os
import secrets
from urllib.parse import urlencode

import requests
from flask import (
    Flask,
    redirect,
    request,
    session,
    url_for,
    render_template_string,
)
from pymongo import MongoClient


# =========================================================
# إعدادات
# =========================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    secrets.token_hex(32)
)

CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
BOT_TOKEN = os.getenv("TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

BASE_URL = os.getenv(
    "WEBSITE_URL",
    "https://diaa-bot-website-production.up.railway.app"
).rstrip("/")

REDIRECT_URI = f"{BASE_URL}/callback"


# =========================================================
# صاحب البوت
# =========================================================

BOT_OWNER_ID = "1154374165642620948"


# =========================================================
# أوامر الإدارة
#
# هذه الأوامر تظهر لصاحب البوت فقط.
# أي شخص آخر لن يراها في الموقع ولن يستطيع
# تعديل إعداداتها من الـ API.
# =========================================================

ADMIN_COMMAND_NAMES = {
    "اعطي",
    "سحب",
    "تصفير",
    "توزيع",
    "تعطيل",
    "تفعيل",
    "رتبة",
    "سوي روم",
}


# =========================================================
# فحص المتغيرات
# =========================================================

if not CLIENT_ID:
    raise RuntimeError(
        "DISCORD_CLIENT_ID غير موجود"
    )

if not CLIENT_SECRET:
    raise RuntimeError(
        "DISCORD_CLIENT_SECRET غير موجود"
    )

if not BOT_TOKEN:
    raise RuntimeError(
        "TOKEN غير موجود"
    )

if not MONGO_URI:
    raise RuntimeError(
        "MONGO_URI غير موجود"
    )


# =========================================================
# MongoDB
# =========================================================

mongo = MongoClient(MONGO_URI)

db = mongo["discord_bot_db"]

commands_collection = db["website_commands"]

guilds_collection = db["website_guilds"]

settings_collection = db["website_command_settings"]

economy_settings_collection = db["economy_settings"]


# =========================================================
# Discord API
# =========================================================

DISCORD_API = "https://discord.com/api/v10"


def discord_headers():

    return {
        "Authorization": f"Bot {BOT_TOKEN}",
        "Content-Type": "application/json",
    }


# =========================================================
# المستخدم الحالي
# =========================================================

def get_user():

    token = session.get(
        "access_token"
    )

    if not token:
        return None

    try:

        response = requests.get(
            f"{DISCORD_API}/users/@me",
            headers={
                "Authorization": f"Bearer {token}"
            },
            timeout=10,
        )

    except requests.RequestException:

        return None

    if response.status_code != 200:

        session.clear()

        return None

    return response.json()


# =========================================================
# هل المستخدم صاحب البوت؟
# =========================================================

def is_bot_owner():

    user = get_user()

    if not user:
        return False

    return (
        str(user.get("id"))
        == BOT_OWNER_ID
    )


# =========================================================
# هل الأمر إداري؟
# =========================================================

def is_admin_command(command):

    if not isinstance(command, dict):
        return False

    command_name = str(
        command.get(
            "name",
            command.get(
                "command_name",
                ""
            )
        )
    ).strip()

    if command_name in ADMIN_COMMAND_NAMES:
        return True

    if command.get(
        "admin_only"
    ) is True:
        return True

    if command.get(
        "is_admin"
    ) is True:
        return True

    return False


# =========================================================
# هل اسم الأمر إداري؟
# =========================================================

def command_name_is_admin(command_name):

    command_name = str(
        command_name or ""
    ).strip()

    if command_name in ADMIN_COMMAND_NAMES:
        return True

    command = commands_collection.find_one(
        {
            "$or": [
                {
                    "name": command_name
                },
                {
                    "command_name": command_name
                }
            ]
        }
    )

    if command and is_admin_command(command):
        return True

    return False


# =========================================================
# Discord Guilds
# =========================================================

def get_user_guilds():

    token = session.get(
        "access_token"
    )

    if not token:
        return []

    try:

        response = requests.get(
            f"{DISCORD_API}/users/@me/guilds",
            headers={
                "Authorization":
                f"Bearer {token}"
            },
            timeout=10,
        )

    except requests.RequestException:

        return []

    if response.status_code != 200:
        return []

    return response.json()


def bot_in_guild(guild_id):

    try:

        response = requests.get(
            f"{DISCORD_API}/guilds/{guild_id}",
            headers=discord_headers(),
            timeout=10,
        )

    except requests.RequestException:

        return False

    return response.status_code == 200


def get_bot_guild(guild_id):

    try:

        response = requests.get(
            f"{DISCORD_API}/guilds/{guild_id}",
            headers=discord_headers(),
            timeout=10,
        )

    except requests.RequestException:

        return None

    if response.status_code != 200:
        return None

    return response.json()


def get_bot_channels(guild_id):

    try:

        response = requests.get(
            f"{DISCORD_API}/guilds/{guild_id}/channels",
            headers=discord_headers(),
            timeout=10,
        )

    except requests.RequestException:

        return []

    if response.status_code != 200:
        return []

    return response.json()


def get_bot_roles(guild_id):

    try:

        response = requests.get(
            f"{DISCORD_API}/guilds/{guild_id}/roles",
            headers=discord_headers(),
            timeout=10,
        )

    except requests.RequestException:

        return []

    if response.status_code != 200:
        return []

    return response.json()


# =========================================================
# معالجة أنواع الرومات
# =========================================================

def normalize_channel_type(channel):

    channel_type = channel.get(
        "type"
    )

    if isinstance(
        channel_type,
        int
    ):

        mapping = {

            0: "text",

            2: "voice",

            4: "category",

            5: "news",

            13: "stage_voice",

            15: "forum",

            16: "media",

        }

        return mapping.get(
            channel_type,
            str(channel_type)
        )

    channel_type = str(
        channel_type
    ).lower().strip()

    mapping = {

        "text": "text",

        "voice": "voice",

        "category": "category",

        "news": "news",

        "announcement": "news",

        "stage_voice": "stage_voice",

        "stage": "stage_voice",

        "forum": "forum",

        "media": "media",

    }

    return mapping.get(
        channel_type,
        channel_type
    )


def prepare_channels_for_picker(
    channels
):

    result = []

    allowed_types = {
        "text",
        "news",
        "forum",
    }

    for channel in channels:

        if not isinstance(
            channel,
            dict
        ):
            continue

        channel_type = normalize_channel_type(
            channel
        )

        if channel_type not in allowed_types:
            continue

        result.append({

            "id": str(
                channel.get(
                    "id",
                    ""
                )
            ),

            "name": channel.get(
                "name",
                "روم"
            ),

            "type": channel_type,

            "position": channel.get(
                "position",
                0
            ),

        })

    result.sort(
        key=lambda x:
        x.get(
            "position",
            0
        )
    )

    return result


# =========================================================
# صلاحية المستخدم على السيرفر
# =========================================================

def user_can_control(
    guild_id
):

    user = get_user()

    if not user:
        return False

    user_id = str(
        user.get("id")
    )

    guild_data = guilds_collection.find_one({

        "guild_id":
        str(guild_id)

    })

    if not guild_data:
        return False

    owner_id = str(
        guild_data.get(
            "owner_id",
            ""
        )
    )

    installer_id = str(
        guild_data.get(
            "installer_id",
            ""
        )
    )

    # صاحب السيرفر
    if user_id == owner_id:
        return True

    # الشخص الذي أضاف البوت
    if (
        installer_id
        and user_id == installer_id
    ):
        return True

    return False


# =========================================================
# الصفحة الرئيسية
# =========================================================

HOME_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>ضياء BOT</title>

<style>

* {
    box-sizing:border-box;
}

body {
    margin:0;
    font-family:Arial,sans-serif;

    background:
        radial-gradient(
            circle at top right,
            #682cff55,
            transparent 35%
        ),

        radial-gradient(
            circle at bottom left,
            #9b4dff33,
            transparent 35%
        ),

        #090914;

    color:white;

    min-height:100vh;
}

.container {
    width:min(1050px,92%);
    margin:auto;
}

nav {
    height:75px;

    display:flex;

    align-items:center;

    justify-content:space-between;
}

.logo {
    font-size:24px;
    font-weight:bold;
}

.logo span {
    color:#a66cff;
}

.btn {
    display:inline-block;

    border:0;

    padding:13px 22px;

    border-radius:13px;

    background:
        linear-gradient(
            135deg,
            #7b3cff,
            #b16cff
        );

    color:white;

    text-decoration:none;

    font-weight:bold;

    cursor:pointer;

    box-shadow:
        0 8px 25px #6b35ff33;
}

.btn:hover {
    transform:translateY(-1px);
}

.hero {
    text-align:center;

    padding:
        100px 0
        80px;
}

.badge {
    display:inline-block;

    background:#ffffff0d;

    border:
        1px solid
        #ffffff15;

    padding:8px 14px;

    border-radius:30px;

    color:#cbb8ff;

    margin-bottom:20px;
}

h1 {
    font-size:
        clamp(
            40px,
            8vw,
            75px
        );

    margin:10px 0;
}

h1 span {

    background:
        linear-gradient(
            90deg,
            #a66cff,
            #e0caff
        );

    -webkit-background-clip:
        text;

    color:transparent;
}

.hero p {

    color:#aaa7b8;

    font-size:18px;

    line-height:1.8;

    max-width:650px;

    margin:
        20px auto
        30px;
}

.card {

    background:#ffffff08;

    border:
        1px solid
        #ffffff12;

    border-radius:22px;

    padding:25px;

    backdrop-filter:blur(15px);
}

.features {

    display:grid;

    grid-template-columns:
        repeat(3,1fr);

    gap:15px;

    padding-bottom:50px;
}

.feature h3 {
    margin-top:0;
}

.feature p {

    color:#9996a8;

    line-height:1.7;
}

@media(max-width:700px) {

    .features {
        grid-template-columns:1fr;
    }

    .hero {
        padding-top:65px;
    }

}

</style>

</head>

<body>

<div class="container">

<nav>

<div class="logo">
ضياء <span>BOT</span>
</div>

<a
class="btn"
href="/login">

تسجيل الدخول

</a>

</nav>


<section class="hero">

<div class="badge">
Discord Bot Control Panel
</div>

<h1>

تحكم ببوتك

<span>
بسهولة.
</span>

</h1>

<p>

لوحة تحكم احترافية لبوت ضياء،
لإدارة الأوامر والرومات والرتب
من مكان واحد وبواجهة بسيطة.

</p>

<a
class="btn"
href="/login">

🚀 دخول لوحة التحكم

</a>

</section>


<section class="features">


<div class="card feature">

<h3>
⚙️ إدارة الأوامر
</h3>

<p>

اختر الرومات والرتب المسموح لها
باستخدام كل أمر.

</p>

</div>


<div class="card feature">

<h3>
📁 إدارة الرومات
</h3>

<p>

أنشئ رومات جديدة من لوحة التحكم
بدون الحاجة للدخول إلى ديسكورد.

</p>

</div>


<div class="card feature">

<h3>
🔐 تحكم آمن
</h3>

<p>

لوحة التحكم متاحة لصاحب السيرفر
والشخص الذي أضاف البوت.

</p>

</div>


</section>

</div>

</body>

</html>
"""


# =========================================================
# تسجيل الدخول
# =========================================================

@app.route("/")
def home():

    return render_template_string(
        HOME_HTML
    )


@app.route("/login")
def login():

    params = {

        "client_id":
        CLIENT_ID,

        "response_type":
        "code",

        "redirect_uri":
        REDIRECT_URI,

        "scope":
        "identify guilds",

    }

    url = (
        "https://discord.com/oauth2/authorize?"
        + urlencode(params)
    )

    return redirect(url)


@app.route("/callback")
def callback():

    code = request.args.get(
        "code"
    )

    if not code:

        return (
            "لم يتم استلام كود تسجيل الدخول.",
            400
        )

    try:

        response = requests.post(

            f"{DISCORD_API}/oauth2/token",

            data={

                "client_id":
                CLIENT_ID,

                "client_secret":
                CLIENT_SECRET,

                "grant_type":
                "authorization_code",

                "code":
                code,

                "redirect_uri":
                REDIRECT_URI,

            },

            headers={

                "Content-Type":
                "application/x-www-form-urlencoded"

            },

            timeout=10,

        )

    except requests.RequestException:

        return (
            "تعذر الاتصال بـ Discord.",
            500
        )

    if response.status_code != 200:

        return (
            "فشل تسجيل الدخول إلى Discord.",
            400
        )

    data = response.json()

    session["access_token"] = data[
        "access_token"
    ]

    return redirect(
        url_for("dashboard")
    )


# =========================================================
# رابط إضافة البوت
# =========================================================

@app.route("/invite")
def invite():

    params = {

        "client_id":
        CLIENT_ID,

        "scope":
        "bot applications.commands",

    }

    url = (
        "https://discord.com/oauth2/authorize?"
        + urlencode(params)
    )

    return redirect(url)


# =========================================================
# Dashboard
# =========================================================

DASHBOARD_HTML = """
<!DOCTYPE html>

<html lang="ar" dir="rtl">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>
لوحة التحكم - ضياء BOT
</title>

<style>

body {

    margin:0;

    background:#090914;

    color:white;

    font-family:Arial,sans-serif;

}

.container {

    width:min(1000px,92%);

    margin:auto;

}

nav {

    height:75px;

    display:flex;

    justify-content:space-between;

    align-items:center;

}

.logo {

    font-size:23px;

    font-weight:bold;

}

.logo span {

    color:#a66cff;

}

.card {

    background:#ffffff08;

    border:
        1px solid
        #ffffff12;

    border-radius:20px;

    padding:22px;

    margin-bottom:15px;

}

.server {

    display:flex;

    align-items:center;

    justify-content:space-between;

    gap:15px;

}

.server-name {

    font-size:19px;

    font-weight:bold;

}

.small {

    color:#92909f;

    font-size:13px;

    margin-top:6px;

}

.btn {

    border:0;

    border-radius:12px;

    padding:11px 18px;

    background:
        linear-gradient(
            135deg,
            #7136ff,
            #ad65ff
        );

    color:white;

    text-decoration:none;

    font-weight:bold;

}

.empty {

    text-align:center;

    padding:50px 20px;

    color:#aaa7b8;

}

@media(max-width:600px) {

    .server {

        flex-direction:column;

        align-items:stretch;

    }

    .btn {

        text-align:center;

    }

}

</style>

</head>

<body>

<div class="container">

<nav>

<div class="logo">
ضياء <span>BOT</span>
</div>

<a
class="btn"
href="/logout">

تسجيل خروج

</a>

</nav>


<h1>
سيرفراتك
</h1>


{% if guilds %}

{% for guild in guilds %}

<div class="card server">

<div>

<div class="server-name">

{{ guild.name }}

</div>

<div class="small">

{% if guild.status == "owner" %}

👑 مالك السيرفر

{% else %}

🔑 الشخص الذي أضاف البوت

{% endif %}

</div>

</div>


<a
class="btn"
href="/server/{{ guild.id }}">

إدارة السيرفر

</a>

</div>

{% endfor %}

{% else %}

<div class="card empty">

<h2>
لا يوجد سيرفر متاح
</h2>

<p>

تأكد أن البوت موجود في السيرفر
وأنك صاحب السيرفر أو الشخص الذي أضاف البوت.

</p>

<br>

<a
class="btn"
href="/invite">

➕ إضافة البوت

</a>

</div>

{% endif %}

</div>

</body>

</html>
"""


@app.route("/dashboard")
def dashboard():

    user = get_user()

    if not user:

        return redirect(
            url_for("login")
        )

    user_id = str(
        user["id"]
    )

    database_guilds = list(
        guilds_collection.find({})
    )

    result = []

    for guild in database_guilds:

        guild_id = str(
            guild.get(
                "guild_id"
            )
        )

        if not bot_in_guild(
            guild_id
        ):
            continue

        owner_id = str(
            guild.get(
                "owner_id",
                ""
            )
        )

        installer_id = str(
            guild.get(
                "installer_id",
                ""
            )
        )

        if user_id == owner_id:

            result.append({

                "id":
                guild_id,

                "name":
                guild.get(
                    "guild_name",
                    "سيرفر"
                ),

                "status":
                "owner"

            })

        elif (
            installer_id
            and user_id == installer_id
        ):

            result.append({

                "id":
                guild_id,

                "name":
                guild.get(
                    "guild_name",
                    "سيرفر"
                ),

                "status":
                "installer"

            })

    return render_template_string(

        DASHBOARD_HTML,

        guilds=result

    )


# =========================================================
# صفحة السيرفر
# =========================================================

SERVER_HTML = """
<!DOCTYPE html>

<html lang="ar" dir="rtl">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>
{{ guild_name }} - ضياء BOT
</title>

<style>

body {

    margin:0;

    background:#090914;

    color:white;

    font-family:Arial,sans-serif;

}

.container {

    width:min(950px,92%);

    margin:auto;

}

nav {

    height:75px;

    display:flex;

    justify-content:space-between;

    align-items:center;

}

.card {

    background:#ffffff08;

    border:
        1px solid
        #ffffff12;

    border-radius:22px;

    padding:25px;

    margin-bottom:16px;

}

h1 {

    font-size:34px;

}

.grid {

    display:grid;

    grid-template-columns:
        repeat(2,1fr);

    gap:15px;

}

.big {

    font-size:30px;

    font-weight:bold;

    color:#b58aff;

}

.btn {

    display:inline-block;

    padding:13px 20px;

    border-radius:13px;

    background:
        linear-gradient(
            135deg,
            #7136ff,
            #ad65ff
        );

    color:white;

    text-decoration:none;

    font-weight:bold;

    margin-top:10px;

    border:0;

    cursor:pointer;

}

.secondary {

    background:#ffffff0d;

}

input {

    width:100%;

    padding:14px;

    margin-top:10px;

    box-sizing:border-box;

    border-radius:12px;

    border:
        1px solid
        #ffffff14;

    background:#ffffff0b;

    color:white;

    outline:none;

}

input:focus {

    border-color:#8b5cff;

}

.status {

    display:inline-block;

    padding:7px 12px;

    border-radius:20px;

    margin-top:10px;

    font-size:13px;

}

.status.on {

    background:#22c55e22;

    color:#6ee7a0;

}

.status.off {

    background:#ef444422;

    color:#ff8585;

}

.economy-actions {

    display:flex;

    gap:10px;

    flex-wrap:wrap;

}

.danger {

    background:
        linear-gradient(
            135deg,
            #b42323,
            #e05252
        );

}

.info {

    color:#9d9aaa;

    line-height:1.7;

    font-size:14px;

}

.message {

    margin-top:12px;

    padding:12px;

    border-radius:12px;

    display:none;

}

.success {

    background:#22c55e18;

    color:#7af0a5;

}

.error {

    background:#ef444418;

    color:#ff8a8a;

}

@media(max-width:650px) {

    .grid {

        grid-template-columns:1fr;

    }

    .economy-actions {

        flex-direction:column;

    }

    .economy-actions .btn {

        width:100%;

        text-align:center;

    }

}

</style>

</head>

<body>

<div class="container">

<nav>

<a
class="btn secondary"
href="/dashboard">

← السيرفرات

</a>

<a
class="btn secondary"
href="/logout">

خروج

</a>

</nav>


<div class="card">

<h1>
⚡ {{ guild_name }}
</h1>

<p>
لوحة تحكم السيرفر
</p>

</div>


<div class="grid">


<div class="card">

<div class="big">
{{ command_count }}
</div>

<div>
الأوامر
</div>

</div>


<div class="card">

<div class="big">
{{ channel_count }}
</div>

<div>
الرومات
</div>

</div>


<div class="card">

<div class="big">
{{ role_count }}
</div>

<div>
الرتب
</div>

</div>


<div class="card">

<div class="big">
⚙️
</div>

<div>
إدارة البوت
</div>

</div>


</div>


<!-- =====================================================
     نظام الاقتصاد
===================================================== -->

<div class="card">

<h2>
💰 نظام الاقتصاد
</h2>

<p class="info">

من هنا تقوم بتفعيل نظام الاقتصاد لهذا السيرفر
وتحديد روم الاقتصاد.

<br>

تغيير الإعدادات هنا لا يحذف أرصدة اللاعبين
ولا يصفر أي بيانات موجودة.

</p>


{% if economy_enabled %}

<div class="status on">

🟢 نظام الاقتصاد مفعّل

</div>

<p class="info">

روم الاقتصاد الحالي:

<br>

<b>
#{{ economy_room_name }}
</b>

<br>

ID:
{{ economy_room_id }}

</p>


<div class="economy-actions">

<button
class="btn"
onclick="changeEconomyRoom()">

⚙️ تغيير روم الاقتصاد

</button>


<button
class="btn danger"
onclick="disableEconomy()">

🔴 تعطيل نظام الاقتصاد

</button>

</div>

{% else %}

<div class="status off">

🔴 نظام الاقتصاد غير مفعّل

</div>

<p class="info">

أدخل ID الروم الذي تريد استخدامه للاقتصاد.

</p>


<input
id="economyRoomId"
placeholder="مثال: 1544334212734124174"
inputmode="numeric"
>


<button
class="btn"
onclick="enableEconomy()">

💰 تفعيل نظام الاقتصاد

</button>

{% endif %}


<div
id="economyMessage"
class="message">
</div>

</div>


<!-- =====================================================
     إدارة الأوامر
===================================================== -->

<div class="card">

<h2>
🧩 إدارة الأوامر
</h2>

<p>

تحكم في الرومات والرتب والتفعيل
الخاص بكل أمر.

</p>

<a
class="btn"
href="/commands?guild={{ guild_id }}">

فتح الأوامر

</a>

</div>


<!-- =====================================================
     إنشاء روم
===================================================== -->

<div class="card">

<h2>
📁 إنشاء روم
</h2>

<p>

أنشئ روم جديد داخل السيرفر.

</p>

<a
class="btn"
href="/create-channel?guild={{ guild_id }}">

إنشاء روم

</a>

</div>

</div>


<script>

function showEconomyMessage(
    message,
    success
) {

    const box =
        document.getElementById(
            "economyMessage"
        );

    box.innerText = message;

    box.className =
        "message " +
        (
            success
            ? "success"
            : "error"
        );

    box.style.display =
        "block";
}


function enableEconomy() {

    const input =
        document.getElementById(
            "economyRoomId"
        );

    const roomId =
        input.value.trim();

    if (!/^\\d+$/.test(roomId)) {

        showEconomyMessage(
            "❌ أدخل ID روم صحيح.",
            false
        );

        return;
    }

    fetch(
        "/api/economy/enable",
        {

            method:"POST",

            headers:{
                "Content-Type":
                "application/json"
            },

            body:JSON.stringify({

                guild_id:
                "{{ guild_id }}",

                economy_room_id:
                roomId

            })

        }
    )
    .then(
        r => r.json()
    )
    .then(
        data => {

            if (data.success) {

                showEconomyMessage(
                    "✅ تم تفعيل نظام الاقتصاد.",
                    true
                );

                setTimeout(
                    () =>
                    location.reload(),
                    700
                );

            } else {

                showEconomyMessage(
                    "❌ " +
                    (
                        data.error ||
                        "حدث خطأ."
                    ),
                    false
                );

            }

        }
    )
    .catch(
        () => {

            showEconomyMessage(
                "❌ تعذر الاتصال بالموقع.",
                false
            );

        }
    );

}


function changeEconomyRoom() {

    const roomId =
        prompt(
            "أدخل ID روم الاقتصاد الجديد:"
        );

    if (!roomId) {
        return;
    }

    if (
        !/^\\d+$/.test(
            roomId.trim()
        )
    ) {

        alert(
            "❌ ID الروم غير صحيح."
        );

        return;
    }

    fetch(
        "/api/economy/enable",
        {

            method:"POST",

            headers:{
                "Content-Type":
                "application/json"
            },

            body:JSON.stringify({

                guild_id:
                "{{ guild_id }}",

                economy_room_id:
                roomId.trim()

            })

        }
    )
    .then(
        r => r.json()
    )
    .then(
        data => {

            if (data.success) {

                alert(
                    "✅ تم تغيير روم الاقتصاد."
                );

                location.reload();

            } else {

                alert(
                    "❌ " +
                    (
                        data.error ||
                        "حدث خطأ."
                    )
                );

            }

        }
    );

}


function disableEconomy() {

    if (
        !confirm(
            "هل أنت متأكد من تعطيل نظام الاقتصاد؟\\n\\nلن يتم حذف أرصدة اللاعبين أو أي بيانات."
        )
    ) {

        return;

    }

    fetch(
        "/api/economy/disable",
        {

            method:"POST",

            headers:{
                "Content-Type":
                "application/json"
            },

            body:JSON.stringify({

                guild_id:
                "{{ guild_id }}"

            })

        }
    )
    .then(
        r => r.json()
    )
    .then(
        data => {

            if (data.success) {

                alert(
                    "🔴 تم تعطيل نظام الاقتصاد."
                );

                location.reload();

            } else {

                alert(
                    "❌ " +
                    (
                        data.error ||
                        "حدث خطأ."
                    )
                );

            }

        }
    );

}

</script>

</body>

</html>
"""


@app.route("/server/<guild_id>")
def server_page(
    guild_id
):

    if not user_can_control(
        guild_id
    ):

        return redirect(
            url_for("dashboard")
        )

    guild = guilds_collection.find_one({

        "guild_id":
        str(guild_id)

    })

    if not guild:

        return (
            "السيرفر غير موجود.",
            404
        )

    economy = (
        economy_settings_collection.find_one({

            "guild_id":
            str(guild_id)

        })
        or {}
    )

    economy_enabled = bool(
        economy.get(
            "currency_enabled",
            False
        )
    )

    economy_room_id = str(
        economy.get(
            "economy_room_id",
            ""
        )
    )

    economy_room_name = "غير محدد"

    if economy_room_id:

        channels = guild.get(
            "channels",
            []
        )

        for channel in channels:

            if str(
                channel.get("id")
            ) == economy_room_id:

                economy_room_name = (
                    channel.get(
                        "name",
                        "الروم"
                    )
                )

                break

        if (
            economy_room_name
            == "غير محدد"
        ):

            economy_room_name = (
                economy_room_id
            )

    return render_template_string(

        SERVER_HTML,

        guild_id=
        guild_id,

        guild_name=
        guild.get(
            "guild_name",
            "السيرفر"
        ),

        command_count=
        commands_collection.count_documents({}),

        channel_count=
        len(
            guild.get(
                "channels",
                []
            )
        ),

        role_count=
        len(
            guild.get(
                "roles",
                []
            )
        ),

        economy_enabled=
        economy_enabled,

        economy_room_id=
        economy_room_id,

        economy_room_name=
        economy_room_name

    )


# =========================================================
# تفعيل نظام الاقتصاد
# =========================================================

@app.route(
    "/api/economy/enable",
    methods=["POST"]
)
def enable_economy():

    data = request.get_json(
        silent=True
    ) or {}

    guild_id = data.get(
        "guild_id"
    )

    economy_room_id = str(
        data.get(
            "economy_room_id",
            ""
        )
    ).strip()

    if not guild_id:

        return {
            "success":False,
            "error":
            "guild_id مفقود."
        }, 400

    if not user_can_control(
        guild_id
    ):

        return {
            "success":False,
            "error":
            "غير مصرح لك."
        }, 403

    if not economy_room_id.isdigit():

        return {
            "success":False,
            "error":
            "ID روم الاقتصاد غير صحيح."
        }, 400

    channels = get_bot_channels(
        guild_id
    )

    channel_exists = False

    for channel in channels:

        if str(
            channel.get("id")
        ) == economy_room_id:

            channel_type = channel.get(
                "type"
            )

            if channel_type in (
                0,
                5,
                15
            ):

                channel_exists = True

            break

    if not channel_exists:

        return {
            "success":False,
            "error":
            "روم الاقتصاد غير موجود أو ليس رومًا كتابيًا."
        }, 400

    economy_settings_collection.update_one(

        {
            "guild_id":
            str(guild_id)
        },

        {
            "$set": {

                "guild_id":
                str(guild_id),

                "currency_enabled":
                True,

                "economy_room_id":
                economy_room_id

            }
        },

        upsert=True

    )

    return {
        "success":True
    }


# =========================================================
# تعطيل نظام الاقتصاد
# =========================================================

@app.route(
    "/api/economy/disable",
    methods=["POST"]
)
def disable_economy():

    data = request.get_json(
        silent=True
    ) or {}

    guild_id = data.get(
        "guild_id"
    )

    if not guild_id:

        return {
            "success":False,
            "error":
            "guild_id مفقود."
        }, 400

    if not user_can_control(
        guild_id
    ):

        return {
            "success":False,
            "error":
            "غير مصرح لك."
        }, 403

    economy_settings_collection.update_one(

        {
            "guild_id":
            str(guild_id)
        },

        {
            "$set": {

                "guild_id":
                str(guild_id),

                "currency_enabled":
                False

            }
        },

        upsert=True

    )

    return {
        "success":True
    }


# =========================================================
# صفحة الأوامر
# =========================================================

COMMANDS_HTML = """
<!DOCTYPE html>

<html lang="ar" dir="rtl">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>
الأوامر - ضياء BOT
</title>

<style>

body {

    margin:0;

    background:#090914;

    color:white;

    font-family:Arial,sans-serif;

}

.container {

    width:min(900px,92%);

    margin:auto;

}

nav {

    height:75px;

    display:flex;

    justify-content:space-between;

    align-items:center;

}

.card {

    background:#ffffff08;

    border:
        1px solid
        #ffffff12;

    border-radius:18px;

    padding:18px;

    margin-bottom:12px;

}

.command {

    display:flex;

    justify-content:space-between;

    align-items:center;

    gap:15px;

}

.name {

    font-weight:bold;

    font-size:18px;

}

.desc {

    color:#9491a1;

    margin-top:7px;

    font-size:14px;

}

.btn {

    border:0;

    border-radius:12px;

    padding:11px 17px;

    background:
        linear-gradient(
            135deg,
            #7136ff,
            #ad65ff
        );

    color:white;

    font-weight:bold;

    cursor:pointer;

}

.modal-bg {

    display:none;

    position:fixed;

    inset:0;

    background:#000b;

    align-items:center;

    justify-content:center;

    padding:15px;

}

.modal {

    width:min(600px,100%);

    max-height:90vh;

    overflow:auto;

    background:#11111e;

    border:
        1px solid
        #ffffff15;

    border-radius:22px;

    padding:23px;

    box-shadow:
        0 20px 80px #000;

}

.close {

    float:left;

    cursor:pointer;

    font-size:22px;

}

.picker {

    position:relative;

    margin-top:10px;

}

.picker-btn {

    width:100%;

    text-align:right;

    background:#ffffff0b;

    border:
        1px solid
        #ffffff14;

    color:white;

    padding:14px;

    border-radius:13px;

    cursor:pointer;

}

.menu {

    display:none;

    margin-top:7px;

    background:#181827;

    border:
        1px solid
        #ffffff12;

    border-radius:14px;

    padding:8px;

    max-height:220px;

    overflow:auto;

}

.item {

    display:block;

    padding:9px;

    border-radius:9px;

}

.item:hover {

    background:#ffffff09;

}

.item input {

    margin-left:8px;

}

.save {

    width:100%;

    margin-top:20px;

}

.info {

    color:#9b98aa;

    font-size:13px;

    line-height:1.7;

}

.empty-picker {

    color:#aaa7b8;

    text-align:center;

    padding:15px;

}

.toggle-box {

    background:#ffffff08;

    border:
        1px solid
        #ffffff12;

    border-radius:14px;

    padding:14px;

    margin-bottom:15px;

}

.toggle-row {

    display:flex;

    align-items:center;

    justify-content:space-between;

    gap:15px;

}

.switch {

    position:relative;

    width:52px;

    height:28px;

}

.switch input {

    display:none;

}

.slider {

    position:absolute;

    inset:0;

    cursor:pointer;

    background:#383847;

    border-radius:30px;

    transition:.2s;

}

.slider:before {

    content:"";

    position:absolute;

    width:22px;

    height:22px;

    left:3px;

    top:3px;

    background:white;

    border-radius:50%;

    transition:.2s;

}

.switch input:checked + .slider {

    background:#7136ff;

}

.switch input:checked + .slider:before {

    transform:
        translateX(24px);

}

.admin-badge {

    display:inline-block;

    margin-right:8px;

    padding:4px 8px;

    border-radius:8px;

    background:#7136ff22;

    color:#c7aaff;

    font-size:11px;

}

@media(max-width:600px) {

    .command {

        flex-direction:column;

        align-items:stretch;

    }

    .command .btn {

        width:100%;

    }

}

</style>

</head>

<body>

<div class="container">

<nav>

<a
class="btn"
href="/server/{{ guild_id }}">

← رجوع

</a>

<h2>
الأوامر
</h2>

</nav>


{% for command in commands %}

<div class="card command">

<div>

<div class="name">

{{ command.name }}

{% if command.is_admin_display %}

<span class="admin-badge">
👑 إدارة
</span>

{% endif %}

</div>

<div class="desc">

{{ command.description }}

</div>

</div>


<button
class="btn"
onclick='openSettings({{ command.name|tojson }})'>

⚙️ إعداد

</button>

</div>

{% endfor %}

</div>


<!-- =====================================================
     Modal
===================================================== -->

<div
class="modal-bg"
id="modalBg">

<div class="modal">

<span
class="close"
onclick="closeModal()">

×

</span>


<h2 id="modalTitle">
إعداد الأمر
</h2>


<div class="toggle-box">

<div class="toggle-row">

<div>

<b>
حالة الأمر
</b>

<div class="info">

يمكنك تعطيل الأمر بالكامل من هنا.

</div>

</div>


<label class="switch">

<input
type="checkbox"
id="enabledCheck"
>

<span class="slider"></span>

</label>

</div>

</div>


<p class="info">

إذا لم تختر أي روم أو رتبة،
فإن صلاحيات الروم والرتبة لن تكون مقيدة
من إعدادات الموقع.

</p>


<h3>
📁 الرومات المسموحة
</h3>


<div class="picker">

<button
class="picker-btn"
onclick="togglePicker('channelsMenu')"
id="channelButton">

اختيار الرومات

</button>


<div
class="menu"
id="channelsMenu">

{% if channels %}

{% for channel in channels %}

<label class="item">

<input
type="checkbox"
class="channel-check"
value="{{ channel.id }}"
>

#{{ channel.name }}

</label>

{% endfor %}

{% else %}

<div class="empty-picker">

❌ لم يتم العثور على رومات كتابية.

</div>

{% endif %}

</div>

</div>


<h3>
🎭 الرتب المسموحة
</h3>


<div class="picker">

<button
class="picker-btn"
onclick="togglePicker('rolesMenu')"
id="roleButton">

اختيار الرتب

</button>


<div
class="menu"
id="rolesMenu">

{% for role in roles %}

<label class="item">

<input
type="checkbox"
class="role-check"
value="{{ role.id }}"
>

{{ role.name }}

</label>

{% endfor %}

</div>

</div>


<button
class="btn save"
onclick="saveSettings()">

💾 حفظ الإعدادات

</button>

</div>

</div>


<script>

let selectedCommand = "";


function openSettings(
    command
) {

    selectedCommand =
        command;

    document.getElementById(
        "modalTitle"
    ).innerText =
        "⚙️ إعداد: " +
        command;


    document.getElementById(
        "modalBg"
    ).style.display =
        "flex";


    document.querySelectorAll(
        ".channel-check"
    ).forEach(
        x =>
        x.checked = false
    );


    document.querySelectorAll(
        ".role-check"
    ).forEach(
        x =>
        x.checked = false
    );


    document.getElementById(
        "enabledCheck"
    ).checked = false;


    updateButtonText();


    fetch(
        "/api/command-settings?guild={{ guild_id }}&command="
        +
        encodeURIComponent(
            command
        )
    )
    .then(
        r => r.json()
    )
    .then(
        data => {

            if (!data.success)
                return;


            (
                data.channel_ids
                || []
            ).forEach(
                id => {

                    id =
                        String(id);

                    const box =
                        document.querySelector(
                            '.channel-check[value="' +
                            id +
                            '"]'
                        );

                    if (box) {
                        box.checked =
                            true;
                    }

                }
            );


            (
                data.role_ids
                || []
            ).forEach(
                id => {

                    id =
                        String(id);

                    const box =
                        document.querySelector(
                            '.role-check[value="' +
                            id +
                            '"]'
                        );

                    if (box) {
                        box.checked =
                            true;
                    }

                }
            );


            document.getElementById(
                "enabledCheck"
            ).checked =
                data.enabled === true;


            updateButtonText();

        }
    )
    .catch(
        () => {

            console.log(
                "تعذر جلب إعدادات الأمر"
            );

        }
    );

}


function closeModal() {

    document.getElementById(
        "modalBg"
    ).style.display =
        "none";


    document.getElementById(
        "channelsMenu"
    ).style.display =
        "none";


    document.getElementById(
        "rolesMenu"
    ).style.display =
        "none";

}


function togglePicker(
    id
) {

    const menu =
        document.getElementById(
            id
        );

    menu.style.display =
        menu.style.display === "block"
        ? "none"
        : "block";

}


function updateButtonText() {

    const channels =
        document.querySelectorAll(
            ".channel-check:checked"
        ).length;


    const roles =
        document.querySelectorAll(
            ".role-check:checked"
        ).length;


    document.getElementById(
        "channelButton"
    ).innerText =
        channels
        ? "📁 تم اختيار " +
          channels +
          " روم"
        : "📁 اختيار الرومات";


    document.getElementById(
        "roleButton"
    ).innerText =
        roles
        ? "🎭 تم اختيار " +
          roles +
          " رتبة"
        : "🎭 اختيار الرتب";

}


document.addEventListener(
    "change",
    function(e) {

        if (

            e.target.classList.contains(
                "channel-check"
            )

            ||

            e.target.classList.contains(
                "role-check"
            )

        ) {

            updateButtonText();

        }

    }
);


function saveSettings() {

    const channels = [

        ...
        document.querySelectorAll(
            ".channel-check:checked"
        )

    ].map(
        x =>
        String(x.value)
    );


    const roles = [

        ...
        document.querySelectorAll(
            ".role-check:checked"
        )

    ].map(
        x =>
        String(x.value)
    );


    const enabled =
        document.getElementById(
            "enabledCheck"
        ).checked;


    fetch(
        "/save-command",
        {

            method:"POST",

            headers:{

                "Content-Type":
                "application/json"

            },

            body:JSON.stringify({

                guild_id:
                "{{ guild_id }}",

                command_name:
                selectedCommand,

                channel_ids:
                channels,

                role_ids:
                roles,

                enabled:
                enabled

            })

        }
    )
    .then(
        r => r.json()
    )
    .then(
        data => {

            if (data.success) {

                alert(
                    "✅ تم حفظ إعدادات الأمر"
                );

                closeModal();

            } else {

                alert(
                    "❌ " +
                    (
                        data.error ||
                        "حدث خطأ"
                    )
                );

            }

        }
    )
    .catch(
        () => {

            alert(
                "❌ تعذر الاتصال بالموقع."
            );

        }
    );

}

</script>

</body>

</html>
"""


@app.route("/commands")
def commands_page():

    guild_id = request.args.get(
        "guild"
    )

    if not guild_id:

        return redirect(
            url_for("dashboard")
        )

    if not user_can_control(
        guild_id
    ):

        return redirect(
            url_for("dashboard")
        )


    all_commands = list(
        commands_collection.find(
            {},
            {
                "_id":0
            }
        )
    )


    bot_owner = is_bot_owner()

    commands = []


    for command in all_commands:

        command_name = str(

            command.get(
                "name",
                command.get(
                    "command_name",
                    ""
                )
            )

        ).strip()


        admin_command = (
            is_admin_command(
                command
            )
        )


        # =================================================
        # الأوامر الإدارية تظهر لصاحب البوت فقط
        # =================================================

        if (
            admin_command
            and not bot_owner
        ):

            continue


        command["is_admin_display"] = (
            admin_command
        )


        commands.append(
            command
        )


    guild = guilds_collection.find_one({

        "guild_id":
        str(guild_id)

    })


    if not guild:

        return (
            "السيرفر غير موجود.",
            404
        )


    # =====================================================
    # جلب الرومات مباشرة من Discord API
    # =====================================================

    discord_channels = get_bot_channels(
        guild_id
    )


    channels = prepare_channels_for_picker(
        discord_channels
    )


    # =====================================================
    # جلب الرتب
    # =====================================================

    roles = get_bot_roles(
        guild_id
    )


    return render_template_string(

        COMMANDS_HTML,

        guild_id=
        guild_id,

        commands=
        commands,

        channels=
        channels,

        roles=
        roles

    )


# =========================================================
# جلب إعداد أمر
# =========================================================

@app.route(
    "/api/command-settings"
)
def command_settings():

    guild_id = request.args.get(
        "guild"
    )

    command_name = request.args.get(
        "command"
    )


    if (
        not guild_id
        or not command_name
    ):

        return {
            "success":False
        }


    if not user_can_control(
        guild_id
    ):

        return {
            "success":False,
            "error":
            "غير مصرح"
        }, 403


    # =====================================================
    # حماية أوامر الإدارة
    # =====================================================

    if (
        command_name_is_admin(
            command_name
        )
        and not is_bot_owner()
    ):

        return {
            "success":False,
            "error":
            "هذا الأمر متاح لصاحب البوت فقط."
        }, 403


    setting = settings_collection.find_one({

        "guild_id":
        str(guild_id),

        "command_name":
        str(command_name)

    })


    if not setting:

        return {

            "success":True,

            "channel_ids":[],

            "role_ids":[],

            "enabled":False

        }


    return {

        "success":True,

        "channel_ids":[

            str(x)

            for x in setting.get(
                "channel_ids",
                []
            )

        ],

        "role_ids":[

            str(x)

            for x in setting.get(
                "role_ids",
                []
            )

        ],

        "enabled":
        setting.get(
            "enabled",
            False
        )

    }


# =========================================================
# حفظ إعدادات الأمر
# =========================================================

@app.route(
    "/save-command",
    methods=["POST"]
)
def save_command():

    data = request.get_json(
        silent=True
    ) or {}


    guild_id = data.get(
        "guild_id"
    )

    command_name = data.get(
        "command_name"
    )


    channel_ids = [

        str(x)

        for x in data.get(
            "channel_ids",
            []
        )

    ]


    role_ids = [

        str(x)

        for x in data.get(
            "role_ids",
            []
        )

    ]


    if (
        not guild_id
        or not command_name
    ):

        return {

            "success":False,

            "error":
            "بيانات ناقصة"

        }, 400


    if not user_can_control(
        guild_id
    ):

        return {

            "success":False,

            "error":
            "غير مصرح لك"

        }, 403


    # =====================================================
    # حماية أوامر الإدارة
    # =====================================================

    if (
        command_name_is_admin(
            command_name
        )
        and not is_bot_owner()
    ):

        return {

            "success":False,

            "error":
            "هذا الأمر متاح لصاحب البوت فقط."

        }, 403


    settings_collection.update_one(

        {

            "guild_id":
            str(guild_id),

            "command_name":
            str(command_name)

        },

        {

            "$set": {

                "guild_id":
                str(guild_id),

                "command_name":
                str(command_name),

                "channel_ids":
                channel_ids,

                "role_ids":
                role_ids,

                "enabled":
                bool(
                    data.get(
                        "enabled",
                        False
                    )
                )

            }

        },

        upsert=True

    )


    return {
        "success":True
    }


# =========================================================
# إنشاء روم
# =========================================================

CREATE_CHANNEL_HTML = """
<!DOCTYPE html>

<html lang="ar" dir="rtl">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>
إنشاء روم
</title>

<style>

body {

    margin:0;

    background:#090914;

    color:white;

    font-family:Arial;

}

.container {

    width:min(550px,92%);

    margin:80px auto;

}

.card {

    background:#ffffff08;

    border:
        1px solid
        #ffffff12;

    border-radius:22px;

    padding:25px;

}

input,select {

    width:100%;

    padding:14px;

    margin:
        8px 0
        18px;

    box-sizing:border-box;

    border-radius:12px;

    border:
        1px solid
        #ffffff14;

    background:#ffffff0b;

    color:white;

}

button {

    width:100%;

    padding:14px;

    border:0;

    border-radius:13px;

    background:
        linear-gradient(
            135deg,
            #7136ff,
            #ad65ff
        );

    color:white;

    font-weight:bold;

    cursor:pointer;

}

.back {

    display:block;

    margin-top:15px;

    color:#b58aff;

    text-align:center;

    text-decoration:none;

}

</style>

</head>

<body>

<div class="container">

<div class="card">

<h1>
📁 إنشاء روم
</h1>


<form method="POST">

<label>
اسم الروم
</label>


<input
name="name"
placeholder="مثال: الأوامر"
required
maxlength="100"
>


<label>
نوع الروم
</label>


<select name="type">

<option value="text">
روم كتابي
</option>

<option value="voice">
روم صوتي
</option>

</select>


<button>
✨ إنشاء الروم
</button>

</form>


<a
class="back"
href="/server/{{ guild_id }}">

← الرجوع للسيرفر

</a>

</div>

</div>

</body>

</html>
"""


@app.route(
    "/create-channel",
    methods=["GET","POST"]
)
def create_channel():

    guild_id = request.args.get(
        "guild"
    )


    if not guild_id:

        return redirect(
            url_for("dashboard")
        )


    if not user_can_control(
        guild_id
    ):

        return redirect(
            url_for("dashboard")
        )


    if request.method == "GET":

        return render_template_string(

            CREATE_CHANNEL_HTML,

            guild_id=
            guild_id

        )


    name = (

        request.form.get(
            "name",
            ""
        )

        .strip()

    )


    channel_type = request.form.get(
        "type",
        "text"
    )


    if not name:

        return (
            "اسم الروم مطلوب.",
            400
        )


    payload = {

        "name":
        name,

        "type":
        0
        if channel_type == "text"
        else 2

    }


    try:

        response = requests.post(

            f"{DISCORD_API}/guilds/{guild_id}/channels",

            headers=
            discord_headers(),

            json=
            payload,

            timeout=10,

        )

    except requests.RequestException:

        return (
            "❌ تعذر الاتصال بـ Discord.",
            500
        )


    if response.status_code not in (
        200,
        201
    ):

        return (

            "❌ فشل إنشاء الروم.<br><br>"

            "تأكد أن البوت يملك "
            "Manage Channels "
            "في السيرفر.",

            403

        )


    return redirect(

        url_for(

            "server_page",

            guild_id=
            guild_id

        )

    )


# =========================================================
# تسجيل خروج
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("home")
    )


# =========================================================
# تشغيل Railway
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
