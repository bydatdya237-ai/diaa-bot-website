import os
import time
import secrets
import threading
from urllib.parse import urlencode
from datetime import datetime

import requests
from flask import Flask, redirect, request, session, url_for, render_template_string
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

BOT_OWNER_ID = "1154374165642620948"

# =========================================================
# السيرفر الذي يكون الاقتصاد فيه إجباري
# =========================================================

FORCED_ECONOMY_GUILD_ID = "1545572112134312027"

DISCORD_API = "https://discord.com/api/v10"


# =========================================================
# فحص المتغيرات
# =========================================================

if not CLIENT_ID:
    raise RuntimeError("DISCORD_CLIENT_ID غير موجود")

if not CLIENT_SECRET:
    raise RuntimeError("DISCORD_CLIENT_SECRET غير موجود")

if not BOT_TOKEN:
    raise RuntimeError("TOKEN غير موجود")

if not MONGO_URI:
    raise RuntimeError("MONGO_URI غير موجود")


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
# Discord Session
# =========================================================

discord_session = requests.Session()

discord_session.headers.update({
    "Content-Type": "application/json",
    "User-Agent": "DiaaBOT-Website/1.0",
    "Authorization": f"Bot {BOT_TOKEN}",
})


# =========================================================
# Cache
# =========================================================

CACHE_TTL = 30

_cache = {}
_cache_lock = threading.Lock()

discord_rate_lock = threading.Lock()
discord_rate_until = 0.0


def cache_get(key):
    now = time.time()

    with _cache_lock:
        item = _cache.get(key)

        if not item:
            return None

        expires_at, value = item

        if expires_at <= now:
            _cache.pop(key, None)
            return None

        return value


def cache_set(key, value, ttl=CACHE_TTL):
    with _cache_lock:
        _cache[key] = (
            time.time() + ttl,
            value
        )


def cache_delete(key):
    with _cache_lock:
        _cache.pop(key, None)


def cache_delete_prefix(prefix):
    with _cache_lock:
        for key in list(_cache.keys()):
            if str(key).startswith(prefix):
                _cache.pop(key, None)


def discord_headers():
    return {
        "Authorization": f"Bot {BOT_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "DiaaBOT-Website/1.0",
    }


# =========================================================
# Discord API Request
# =========================================================

def discord_request(
    method,
    url,
    *,
    json=None,
    data=None,
    timeout=10,
    max_attempts=2
):
    global discord_rate_until

    for attempt in range(max_attempts):

        with discord_rate_lock:
            wait_time = discord_rate_until - time.time()

        if wait_time > 0:
            time.sleep(min(wait_time, 60))

        try:
            response = discord_session.request(
                method,
                url,
                headers=discord_headers(),
                json=json,
                data=data,
                timeout=timeout
            )

        except requests.RequestException:
            if attempt + 1 >= max_attempts:
                return None

            time.sleep(2)
            continue

        if response.status_code != 429:
            return response

        retry_after = 0

        try:
            body = response.json()

            retry_after = float(
                body.get("retry_after", 0)
            )

        except Exception:
            pass

        if retry_after <= 0:
            try:
                retry_after = float(
                    response.headers.get(
                        "Retry-After",
                        "5"
                    )
                )
            except (
                TypeError,
                ValueError
            ):
                retry_after = 5

        retry_after = max(
            1,
            min(retry_after, 60)
        )

        with discord_rate_lock:
            discord_rate_until = max(
                discord_rate_until,
                time.time() + retry_after
            )

        if attempt + 1 >= max_attempts:
            return response

        time.sleep(retry_after)

    return None


# =========================================================
# Discord OAuth User
# =========================================================

def get_user():

    token = session.get("access_token")

    if not token:
        return None

    cache_key = "oauth_user:" + token[:16]

    cached = cache_get(cache_key)

    if cached is not None:
        return cached

    try:
        response = requests.get(
            f"{DISCORD_API}/users/@me",
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "DiaaBOT-Website/1.0",
            },
            timeout=10,
        )

    except requests.RequestException:
        return None

    if response.status_code != 200:
        session.clear()
        return None

    user = response.json()

    cache_set(
        cache_key,
        user,
        30
    )

    return user


def is_bot_owner():

    user = get_user()

    if not user:
        return False

    return str(user.get("id")) == BOT_OWNER_ID


# =========================================================
# أوامر الإدارة
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

    if command.get("admin_only") is True:
        return True

    if command.get("is_admin") is True:
        return True

    return False


def command_name_is_admin(command_name):

    command_name = str(
        command_name or ""
    ).strip()

    if command_name in ADMIN_COMMAND_NAMES:
        return True

    command = commands_collection.find_one({
        "$or": [
            {"name": command_name},
            {"command_name": command_name}
        ]
    })

    if command and is_admin_command(command):
        return True

    return False


# =========================================================
# User Guilds
# =========================================================

def get_user_guilds():

    token = session.get("access_token")

    if not token:
        return []

    cache_key = "oauth_guilds:" + token[:16]

    cached = cache_get(cache_key)

    if cached is not None:
        return cached

    try:
        response = requests.get(
            f"{DISCORD_API}/users/@me/guilds",
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "DiaaBOT-Website/1.0",
            },
            timeout=10,
        )

    except requests.RequestException:
        return []

    if response.status_code != 200:
        return []

    guilds = response.json()

    cache_set(
        cache_key,
        guilds,
        30
    )

    return guilds


# =========================================================
# Bot Guild
# =========================================================

def get_bot_guild(guild_id):

    guild_id = str(guild_id)

    cache_key = f"bot_guild:{guild_id}"

    cached = cache_get(cache_key)

    if cached is not None:
        return cached

    response = discord_request(
        "GET",
        f"{DISCORD_API}/guilds/{guild_id}"
    )

    if response is None:
        return None

    if response.status_code != 200:
        return None

    guild = response.json()

    cache_set(
        cache_key,
        guild,
        CACHE_TTL
    )

    return guild


def bot_in_guild(guild_id):
    return get_bot_guild(guild_id) is not None


def get_bot_channels(guild_id):

    guild_id = str(guild_id)

    cache_key = f"bot_channels:{guild_id}"

    cached = cache_get(cache_key)

    if cached is not None:
        return cached

    response = discord_request(
        "GET",
        f"{DISCORD_API}/guilds/{guild_id}/channels"
    )

    if response is None:
        return []

    if response.status_code != 200:
        return []

    channels = response.json()

    cache_set(
        cache_key,
        channels,
        CACHE_TTL
    )

    return channels


def get_bot_roles(guild_id):

    guild_id = str(guild_id)

    cache_key = f"bot_roles:{guild_id}"

    cached = cache_get(cache_key)

    if cached is not None:
        return cached

    response = discord_request(
        "GET",
        f"{DISCORD_API}/guilds/{guild_id}/roles"
    )

    if response is None:
        return []

    if response.status_code != 200:
        return []

    roles = response.json()

    cache_set(
        cache_key,
        roles,
        CACHE_TTL
    )

    return roles


# =========================================================
# Channel Helpers
# =========================================================

def normalize_channel_type(channel):

    channel_type = channel.get("type")

    if isinstance(channel_type, int):

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


def prepare_channels_for_picker(channels):

    result = []

    allowed_types = {
        "text",
        "news",
        "forum",
    }

    for channel in channels:

        if not isinstance(channel, dict):
            continue

        channel_type = normalize_channel_type(
            channel
        )

        if channel_type not in allowed_types:
            continue

        result.append({
            "id": str(
                channel.get("id", "")
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
        key=lambda x: x.get(
            "position",
            0
        )
    )

    return result


# =========================================================
# صلاحية المستخدم على السيرفر
# =========================================================

def user_can_control(guild_id):

    user = get_user()

    if not user:
        return False

    user_id = str(
        user.get("id")
    )

    guild_id = str(
        guild_id
    )

    user_guilds = get_user_guilds()

    for guild in user_guilds:

        if str(
            guild.get("id")
        ) != guild_id:
            continue

        if guild.get(
            "owner",
            False
        ):
            return True

        try:
            permissions = int(
                guild.get(
                    "permissions",
                    0
                )
            )
        except (
            ValueError,
            TypeError
        ):
            permissions = 0

        if permissions & 0x8:
            return True

        if permissions & 0x20:
            return True

        break

    guild_data = guilds_collection.find_one({
        "guild_id": guild_id
    })

    if not guild_data:
        return False

    installer_id = str(
        guild_data.get(
            "installer_id",
            ""
        )
    )

    return bool(
        installer_id
        and user_id == installer_id
    )


# =========================================================
# الصفحة الرئيسية
# =========================================================

HOME_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">

<title>ضياء BOT</title>

<style>

*{
box-sizing:border-box
}

body{
margin:0;
font-family:Arial,sans-serif;
background:
radial-gradient(circle at 15% 20%,#008cff35,transparent 30%),
radial-gradient(circle at 85% 20%,#ffe60025,transparent 30%),
radial-gradient(circle at 50% 100%,#7b2cff25,transparent 40%),
#070910;
color:white;
min-height:100vh;
}

.container{
width:min(1080px,92%);
margin:auto
}

nav{
height:80px;
display:flex;
align-items:center;
justify-content:space-between
}

.logo{
font-size:25px;
font-weight:bold
}

.logo span{
background:linear-gradient(
90deg,
#1597ff,
#875cff,
#ffe600
);
-webkit-background-clip:text;
color:transparent
}

.btn{
display:inline-block;
border:1px solid #ffffff16;
padding:13px 22px;
border-radius:14px;
background:
linear-gradient(
135deg,
#087dff,
#7455ff 55%,
#ffd900
);
color:white;
text-decoration:none;
font-weight:bold;
cursor:pointer;
box-shadow:0 8px 30px #008cff20
}

.hero{
text-align:center;
padding:105px 0 85px
}

.badge{
display:inline-block;
background:#ffffff09;
border:1px solid #ffffff15;
padding:9px 16px;
border-radius:30px;
color:#d9d5ff;
margin-bottom:20px;
backdrop-filter:blur(10px)
}

h1{
font-size:clamp(42px,8vw,78px);
margin:10px 0;
letter-spacing:-2px
}

h1 span{
background:
linear-gradient(
90deg,
#1597ff,
#8c5cff,
#ffe600
);
-webkit-background-clip:text;
color:transparent
}

.hero p{
color:#aaaebc;
font-size:18px;
line-height:1.9;
max-width:680px;
margin:20px auto 32px
}

.card{
background:#ffffff08;
border:1px solid #ffffff12;
border-radius:24px;
padding:26px;
backdrop-filter:blur(14px);
box-shadow:0 15px 50px #0005
}

.features{
display:grid;
grid-template-columns:repeat(3,1fr);
gap:18px;
padding-bottom:55px
}

.feature h3{
margin-top:0
}

.feature p{
color:#999eac;
line-height:1.8
}

@media(max-width:700px){

.features{
grid-template-columns:1fr
}

.hero{
padding-top:65px
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

<a class="btn" href="/login">
تسجيل الدخول
</a>

</nav>

<section class="hero">

<div class="badge">
⚡ Discord Bot Control Panel
</div>

<h1>
تحكم ببوتك
<span>بسهولة.</span>
</h1>

<p>
لوحة تحكم احترافية لبوت ضياء،
لإدارة الأوامر والرومات والرتب
والاقتصاد من مكان واحد.
</p>

<a class="btn" href="/login">
🚀 دخول لوحة التحكم
</a>

</section>

<section class="features">

<div class="card feature">
<h3>⚙️ إدارة الأوامر</h3>
<p>
حدد الرومات والرتب المسموح لها
باستخدام كل أمر بسهولة.
</p>
</div>

<div class="card feature">
<h3>💰 الاقتصاد</h3>
<p>
تحكم باقتصاد كل سيرفر بشكل مستقل
بدون حذف الأرصدة.
</p>
</div>

<div class="card feature">
<h3>🔐 تحكم آمن</h3>
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


@app.route("/")
def home():
    return render_template_string(HOME_HTML)


# =========================================================
# Login
# =========================================================

@app.route("/login")
def login():

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": "identify guilds",
    }

    url = (
        "https://discord.com/oauth2/authorize?"
        + urlencode(params)
    )

    return redirect(url)


@app.route("/callback")
def callback():

    code = request.args.get("code")

    if not code:
        return (
            "لم يتم استلام كود تسجيل الدخول.",
            400
        )

    try:

        response = requests.post(
            f"{DISCORD_API}/oauth2/token",

            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },

            headers={
                "Content-Type":
                "application/x-www-form-urlencoded",

                "User-Agent":
                "DiaaBOT-Website/1.0"
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
# Invite
# =========================================================

@app.route("/invite")
def invite():

    params = {
        "client_id": CLIENT_ID,
        "scope": "bot applications.commands",
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
<meta name="viewport" content="width=device-width,initial-scale=1">

<title>لوحة التحكم - ضياء BOT</title>

<style>

*{
box-sizing:border-box
}

body{
margin:0;
background:
radial-gradient(circle at 10% 10%,#008cff28,transparent 30%),
radial-gradient(circle at 90% 20%,#ffe60020,transparent 30%),
#070910;
color:white;
font-family:Arial,sans-serif;
min-height:100vh
}

.container{
width:min(1050px,92%);
margin:auto
}

nav{
height:80px;
display:flex;
justify-content:space-between;
align-items:center
}

.logo{
font-size:24px;
font-weight:bold
}

.logo span{
background:
linear-gradient(
90deg,
#1597ff,
#865cff,
#ffe600
);
-webkit-background-clip:text;
color:transparent
}

.card{
background:#ffffff08;
border:1px solid #ffffff12;
border-radius:22px;
padding:22px;
margin-bottom:16px;
backdrop-filter:blur(12px)
}

.server{
display:flex;
align-items:center;
justify-content:space-between;
gap:15px
}

.server-name{
font-size:19px;
font-weight:bold
}

.small{
color:#9297a7;
font-size:13px;
margin-top:7px
}

.btn{
border:1px solid #ffffff15;
border-radius:13px;
padding:11px 18px;
background:
linear-gradient(
135deg,
#087dff,
#7555ff 55%,
#ffd900
);
color:white;
text-decoration:none;
font-weight:bold;
cursor:pointer
}

.secondary{
background:#ffffff0a
}

.empty{
text-align:center;
padding:55px 20px;
color:#aaaebc
}

@media(max-width:600px){

.server{
flex-direction:column;
align-items:stretch
}

.btn{
text-align:center
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

<a class="btn secondary" href="/logout">
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

{% elif guild.status == "installer" %}
🔑 الشخص الذي أضاف البوت

{% elif guild.status == "administrator" %}
🛡️ Administrator

{% else %}
⚙️ Manage Server
{% endif %}

</div>

</div>

<a class="btn"
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
وأن لديك صلاحية إدارة السيرفر.
</p>

<br>

<a class="btn" href="/invite">
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

    discord_guilds = get_user_guilds()

    result = []

    for discord_guild in discord_guilds:

        guild_id = str(
            discord_guild.get(
                "id",
                ""
            )
        )

        if not guild_id:
            continue

        if not bot_in_guild(guild_id):
            continue

        owner = bool(
            discord_guild.get(
                "owner",
                False
            )
        )

        try:
            permissions = int(
                discord_guild.get(
                    "permissions",
                    0
                )
            )
        except (
            ValueError,
            TypeError
        ):
            permissions = 0

        is_admin = bool(
            permissions & 0x8
        )

        manage_guild = bool(
            permissions & 0x20
        )

        database_guild = guilds_collection.find_one({
            "guild_id": guild_id
        })

        installer_id = ""

        if database_guild:
            installer_id = str(
                database_guild.get(
                    "installer_id",
                    ""
                )
            )

        is_installer = bool(
            installer_id
            and user_id == installer_id
        )

        if not (
            owner
            or is_admin
            or manage_guild
            or is_installer
        ):
            continue

        guild_name = discord_guild.get(
            "name",
            "سيرفر"
        )

        owner_id = str(
            discord_guild.get(
                "owner_id",
                ""
            )
        )

        update_data = {
            "guild_id": guild_id,
            "guild_name": guild_name,
            "owner_id": owner_id,
            "updated_at": datetime.utcnow()
        }

        if is_installer:
            update_data["installer_id"] = user_id

        guilds_collection.update_one(
            {
                "guild_id": guild_id
            },
            {
                "$set": update_data
            },
            upsert=True
        )

        if owner:
            status = "owner"
        elif is_installer:
            status = "installer"
        elif is_admin:
            status = "administrator"
        else:
            status = "manager"

        result.append({
            "id": guild_id,
            "name": guild_name,
            "status": status
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
<meta name="viewport" content="width=device-width,initial-scale=1">

<title>{{ guild_name }} - ضياء BOT</title>

<style>

*{
box-sizing:border-box
}

body{
margin:0;
background:
radial-gradient(circle at 10% 10%,#008cff30,transparent 32%),
radial-gradient(circle at 90% 15%,#ffe60022,transparent 30%),
radial-gradient(circle at 50% 100%,#7650ff25,transparent 40%),
#070910;
color:white;
font-family:Arial,sans-serif;
min-height:100vh
}

.container{
width:min(1000px,92%);
margin:auto
}

nav{
height:80px;
display:flex;
justify-content:space-between;
align-items:center
}

.card{
background:#ffffff08;
border:1px solid #ffffff12;
border-radius:24px;
padding:25px;
margin-bottom:17px;
backdrop-filter:blur(14px);
box-shadow:0 15px 50px #0004
}

h1{
font-size:34px
}

.grid{
display:grid;
grid-template-columns:repeat(2,1fr);
gap:15px
}

.big{
font-size:30px;
font-weight:bold;
background:
linear-gradient(
90deg,
#1597ff,
#8c5cff,
#ffe600
);
-webkit-background-clip:text;
color:transparent
}

.btn{
display:inline-block;
padding:13px 20px;
border-radius:14px;
background:
linear-gradient(
135deg,
#087dff,
#7555ff 55%,
#ffd900
);
color:white;
text-decoration:none;
font-weight:bold;
margin-top:10px;
border:1px solid #ffffff14;
cursor:pointer
}

.secondary{
background:#ffffff09
}

input{
width:100%;
padding:14px;
margin-top:10px;
box-sizing:border-box;
border-radius:13px;
border:1px solid #ffffff14;
background:#ffffff09;
color:white;
outline:none
}

.status{
display:inline-block;
padding:8px 13px;
border-radius:20px;
margin-top:10px;
font-size:13px
}

.status.on{
background:#22c55e20;
color:#75e7a4
}

.status.off{
background:#ef444420;
color:#ff8888
}

.economy-actions{
display:flex;
gap:10px;
flex-wrap:wrap
}

.danger{
background:linear-gradient(
135deg,
#c72b3c,
#e34e55
)
}

.info{
color:#9da1b0;
line-height:1.8;
font-size:14px
}

.forced{
border:1px solid #ffe60035;
background:
linear-gradient(
135deg,
#ffe6000c,
#7b4cff0c
)
}

.forced-badge{
display:inline-block;
padding:8px 13px;
border-radius:20px;
background:#ffe60016;
color:#ffe873;
font-size:13px;
margin-top:10px
}

@media(max-width:650px){

.grid{
grid-template-columns:1fr
}

.economy-actions{
flex-direction:column
}

}

</style>
</head>

<body>

<div class="container">

<nav>

<a class="btn secondary"
href="/dashboard">
← السيرفرات
</a>

<a class="btn secondary"
href="/logout">
خروج
</a>

</nav>

<div class="card">

<h1>
⚡ {{ guild_name }}
</h1>

<p class="info">
لوحة تحكم السيرفر
</p>

</div>

<div class="grid">

<div class="card">
<div class="big">
{{ command_count }}
</div>
<div>الأوامر</div>
</div>

<div class="card">
<div class="big">
{{ channel_count }}
</div>
<div>الرومات</div>
</div>

<div class="card">
<div class="big">
{{ role_count }}
</div>
<div>الرتب</div>
</div>

<div class="card">
<div class="big">
⚙️
</div>
<div>إدارة البوت</div>
</div>

</div>


<div class="card {% if is_forced_economy_guild %}forced{% endif %}">

<h2>
💰 نظام الاقتصاد
</h2>

{% if is_forced_economy_guild %}

<div class="forced-badge">
🔒 الاقتصاد إجباري في هذا السيرفر
</div>

<p class="info">
نظام الاقتصاد في هذا السيرفر يعمل بشكل إجباري،
ولا يمكن تعطيله من الموقع.
<br>
يمكنك فقط تغيير روم الاقتصاد.
</p>

{% else %}

<p class="info">
من هنا تقوم بتفعيل أو تعطيل نظام الاقتصاد لهذا السيرفر
وتحديد روم الاقتصاد.
<br><br>
إعدادات هذا السيرفر مستقلة عن بقية السيرفرات.
</p>

{% endif %}


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

<button class="btn"
onclick="changeEconomyRoom()">
⚙️ تغيير روم الاقتصاد
</button>

{% if not is_forced_economy_guild %}

<button class="btn danger"
onclick="disableEconomy()">
🔴 تعطيل نظام الاقتصاد
</button>

{% endif %}

</div>


{% else %}

<div class="status off">
🔴 لم يتم تحديد روم الاقتصاد
</div>

<p class="info">

{% if is_forced_economy_guild %}

حدد روم الاقتصاد مرة واحدة وسيتم تشغيل النظام
بشكل إجباري في هذا السيرفر.

{% else %}

أدخل ID الروم الذي تريد استخدامه للاقتصاد.

{% endif %}

</p>

<input
id="economyRoomId"
placeholder="مثال: 1544334212734124174"
inputmode="numeric"
>

<button class="btn"
onclick="enableEconomy()">
💰 حفظ وتفعيل الاقتصاد
</button>

{% endif %}

</div>


<div class="card">

<h2>
🧩 إدارة الأوامر
</h2>

<p class="info">
تحكم في حالة كل أمر والرومات والرتب المسموح لها
باستخدامه من لوحة احترافية.
</p>

<a class="btn"
href="/commands?guild={{ guild_id }}">
⚙️ إدارة الأوامر
</a>

</div>


<div class="card">

<h2>
📁 إنشاء روم
</h2>

<p class="info">
أنشئ روم كتابي أو صوتي داخل السيرفر.
</p>

<a class="btn"
href="/create-channel?guild={{ guild_id }}">
✨ إنشاء روم
</a>

</div>

</div>


<script>

function enableEconomy(){

const input=document.getElementById(
"economyRoomId"
);

if(!input)return;

const roomId=input.value.trim();

if(!/^\\d+$/.test(roomId)){

alert("❌ أدخل ID روم صحيح.");

return;

}

fetch(
"/api/economy/enable",
{
method:"POST",
headers:{
"Content-Type":"application/json"
},
body:JSON.stringify({
guild_id:"{{ guild_id }}",
economy_room_id:roomId
})
}
)
.then(r=>r.json())
.then(data=>{

if(data.success){

alert(
"✅ تم حفظ روم الاقتصاد وتفعيله."
);

location.reload();

}else{

alert(
"❌ "+(data.error||"حدث خطأ.")
);

}

})
.catch(()=>{

alert(
"❌ تعذر الاتصال بالموقع."
);

});

}


function changeEconomyRoom(){

const roomId=prompt(
"أدخل ID روم الاقتصاد الجديد:"
);

if(!roomId)return;

const cleanRoomId=roomId.trim();

if(!/^\\d+$/.test(cleanRoomId)){

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
"Content-Type":"application/json"
},
body:JSON.stringify({
guild_id:"{{ guild_id }}",
economy_room_id:cleanRoomId
})
}
)
.then(r=>r.json())
.then(data=>{

if(data.success){

alert(
"✅ تم حفظ روم الاقتصاد الجديد."
);

location.reload();

}else{

alert(
"❌ "+(data.error||"حدث خطأ.")
);

}

})
.catch(()=>{

alert(
"❌ تعذر الاتصال بالموقع."
);

});

}


function disableEconomy(){

if(!confirm(
"هل أنت متأكد من تعطيل نظام الاقتصاد؟\\n\\nلن يتم حذف أي أرصدة."
)){

return;

}

fetch(
"/api/economy/disable",
{
method:"POST",
headers:{
"Content-Type":"application/json"
},
body:JSON.stringify({
guild_id:"{{ guild_id }}"
})
}
)
.then(r=>r.json())
.then(data=>{

if(data.success){

alert(
"🔴 تم تعطيل نظام الاقتصاد."
);

location.reload();

}else{

alert(
"❌ "+(data.error||"حدث خطأ.")
);

}

})
.catch(()=>{

alert(
"❌ تعذر الاتصال بالموقع."
);

});

}

</script>

</body>
</html>
"""


@app.route("/server/<guild_id>")
def server_page(guild_id):

    guild_id = str(
        guild_id
    ).strip()

    if not guild_id:
        return redirect(
            url_for("dashboard")
        )

    if not user_can_control(guild_id):
        return redirect(
            url_for("dashboard")
        )

    discord_guild = get_bot_guild(
        guild_id
    )

    if not discord_guild:
        return (
            "البوت غير موجود في هذا السيرفر أو لا يستطيع الوصول إليه.",
            404
        )

    discord_channels = get_bot_channels(
        guild_id
    )

    discord_roles = get_bot_roles(
        guild_id
    )

    economy = (
        economy_settings_collection.find_one({
            "guild_id": guild_id
        })
        or {}
    )

    economy_enabled = (
        economy.get(
            "currency_enabled",
            False
        ) is True
    )

    economy_room_id = str(
        economy.get(
            "economy_room_id",
            ""
        )
    ).strip()

    is_forced_economy_guild = (
        guild_id == FORCED_ECONOMY_GUILD_ID
    )

    # =====================================================
    # الاقتصاد الإجباري
    # =====================================================

    if is_forced_economy_guild and economy_room_id:

        economy_settings_collection.update_one(
            {
                "guild_id": guild_id
            },
            {
                "$set": {
                    "guild_id": guild_id,
                    "currency_enabled": True,
                    "economy_room_id": economy_room_id
                }
            },
            upsert=True
        )

        economy_enabled = True

    # =====================================================
    # اسم روم الاقتصاد
    # =====================================================

    economy_room_name = "غير محدد"

    if economy_room_id:

        for channel in discord_channels:

            if str(
                channel.get("id")
            ) == economy_room_id:

                economy_room_name = channel.get(
                    "name",
                    "الروم"
                )

                break

        if economy_room_name == "غير محدد":
            economy_room_name = economy_room_id

    # =====================================================
    # حفظ معلومات السيرفر
    # =====================================================

    guilds_collection.update_one(
        {
            "guild_id": guild_id
        },
        {
            "$set": {
                "guild_id": guild_id,
                "guild_name": discord_guild.get(
                    "name",
                    "السيرفر"
                ),
                "channels": discord_channels,
                "roles": discord_roles,
                "updated_at": datetime.utcnow()
            }
        },
        upsert=True
    )

    return render_template_string(
        SERVER_HTML,

        guild_id=guild_id,

        guild_name=discord_guild.get(
            "name",
            "السيرفر"
        ),

        command_count=commands_collection.count_documents({}),

        channel_count=len(
            discord_channels
        ),

        role_count=len(
            discord_roles
        ),

        economy_enabled=economy_enabled,

        economy_room_id=economy_room_id,

        economy_room_name=economy_room_name,

        is_forced_economy_guild=is_forced_economy_guild
    )


# =========================================================
# اقتصاد - تفعيل / تغيير
# =========================================================

@app.route(
    "/api/economy/enable",
    methods=["POST"]
)
def enable_economy():

    data = request.get_json(
        silent=True
    ) or {}

    guild_id = str(
        data.get(
            "guild_id",
            ""
        )
    ).strip()

    economy_room_id = str(
        data.get(
            "economy_room_id",
            ""
        )
    ).strip()

    if not guild_id:
        return {
            "success": False,
            "error": "guild_id مفقود."
        }, 400

    if not economy_room_id:
        return {
            "success": False,
            "error": "ID روم الاقتصاد مفقود."
        }, 400

    if not economy_room_id.isdigit():
        return {
            "success": False,
            "error": "ID روم الاقتصاد غير صحيح."
        }, 400

    if not user_can_control(guild_id):
        return {
            "success": False,
            "error": "غير مصرح لك."
        }, 403

    bot_guild = get_bot_guild(
        guild_id
    )

    if not bot_guild:
        return {
            "success": False,
            "error": "البوت غير موجود في هذا السيرفر."
        }, 404

    channels = get_bot_channels(
        guild_id
    )

    if not channels:
        return {
            "success": False,
            "error":
            "تعذر جلب رومات السيرفر من Discord."
        }, 400

    selected_channel = None

    for channel in channels:

        if str(
            channel.get("id")
        ) != economy_room_id:
            continue

        channel_type = channel.get("type")

        if channel_type not in (0, 5, 15):
            return {
                "success": False,
                "error":
                "روم الاقتصاد يجب أن يكون رومًا كتابيًا."
            }, 400

        selected_channel = channel
        break

    if selected_channel is None:
        return {
            "success": False,
            "error":
            "روم الاقتصاد غير موجود في هذا السيرفر."
        }, 400

    economy_room_name = str(
        selected_channel.get(
            "name",
            "روم الاقتصاد"
        )
    )

    economy_settings_collection.update_one(
        {
            "guild_id": guild_id
        },
        {
            "$set": {
                "guild_id": guild_id,
                "currency_enabled": True,
                "economy_room_id": economy_room_id
            }
        },
        upsert=True
    )

    saved = economy_settings_collection.find_one({
        "guild_id": guild_id
    })

    if not saved:
        return {
            "success": False,
            "error":
            "تعذر حفظ إعدادات الاقتصاد."
        }, 500

    return {
        "success": True,
        "guild_id": guild_id,
        "economy_room_id": str(
            saved.get(
                "economy_room_id",
                ""
            )
        ),
        "economy_room_name": economy_room_name,
        "currency_enabled": True
    }


# =========================================================
# اقتصاد - تعطيل
# =========================================================

@app.route(
    "/api/economy/disable",
    methods=["POST"]
)
def disable_economy():

    data = request.get_json(
        silent=True
    ) or {}

    guild_id = str(
        data.get(
            "guild_id",
            ""
        )
    ).strip()

    if not guild_id:
        return {
            "success": False,
            "error": "guild_id مفقود."
        }, 400

    if not user_can_control(guild_id):
        return {
            "success": False,
            "error": "غير مصرح لك."
        }, 403

    # =====================================================
    # منع تعطيل الاقتصاد في السيرفر الإجباري
    # =====================================================

    if guild_id == FORCED_ECONOMY_GUILD_ID:

        economy = economy_settings_collection.find_one({
            "guild_id": guild_id
        }) or {}

        room_id = str(
            economy.get(
                "economy_room_id",
                ""
            )
        ).strip()

        economy_settings_collection.update_one(
            {
                "guild_id": guild_id
            },
            {
                "$set": {
                    "guild_id": guild_id,
                    "currency_enabled": True
                }
            },
            upsert=True
        )

        return {
            "success": False,
            "error":
            "نظام الاقتصاد إجباري في هذا السيرفر ولا يمكن تعطيله."
        }, 403

    # =====================================================
    # السيرفرات العادية
    # =====================================================

    economy_settings_collection.update_one(
        {
            "guild_id": guild_id
        },
        {
            "$set": {
                "guild_id": guild_id,
                "currency_enabled": False
            }
        },
        upsert=True
    )

    return {
        "success": True,
        "guild_id": guild_id
    }


# =========================================================
# صفحة الأوامر - تصميم احترافي
# =========================================================

COMMANDS_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>
إدارة الأوامر - ضياء BOT
</title>

<style>

*{
box-sizing:border-box
}

body{
margin:0;
background:
radial-gradient(circle at 5% 5%,#008cff30,transparent 30%),
radial-gradient(circle at 95% 10%,#ffe60020,transparent 28%),
radial-gradient(circle at 50% 100%,#8155ff25,transparent 40%),
#070910;
color:white;
font-family:Arial,sans-serif;
min-height:100vh
}

.container{
width:min(980px,92%);
margin:auto
}

nav{
height:80px;
display:flex;
justify-content:space-between;
align-items:center
}

.logo{
font-size:23px;
font-weight:bold
}

.logo span{
background:
linear-gradient(
90deg,
#1597ff,
#865cff,
#ffe600
);
-webkit-background-clip:text;
color:transparent
}

.card{
background:#ffffff08;
border:1px solid #ffffff12;
border-radius:22px;
padding:20px;
margin-bottom:13px;
backdrop-filter:blur(13px)
}

.command{
display:flex;
justify-content:space-between;
align-items:center;
gap:15px;
transition:.2s
}

.command:hover{
transform:translateY(-2px);
border-color:#ffffff25
}

.name{
font-weight:bold;
font-size:18px
}

.desc{
color:#969bab;
margin-top:7px;
font-size:14px
}

.btn{
border:1px solid #ffffff15;
border-radius:13px;
padding:11px 17px;
background:
linear-gradient(
135deg,
#087dff,
#7555ff 55%,
#ffd900
);
color:white;
font-weight:bold;
cursor:pointer;
text-decoration:none
}

.modal-bg{
display:none;
position:fixed;
inset:0;
background:#000c;
align-items:center;
justify-content:center;
padding:15px;
z-index:100
}

.modal{
width:min(650px,100%);
max-height:92vh;
overflow:auto;
background:#0e111b;
border:1px solid #ffffff18;
border-radius:25px;
padding:24px;
box-shadow:0 30px 100px #000
}

.close{
float:left;
cursor:pointer;
font-size:25px;
color:#aaa
}

.section{
background:#ffffff06;
border:1px solid #ffffff0d;
border-radius:18px;
padding:16px;
margin-top:15px
}

.section-title{
display:flex;
justify-content:space-between;
align-items:center;
gap:10px;
margin-bottom:12px
}

.counter{
background:
linear-gradient(
90deg,
#087dff,
#7555ff,
#ffd900
);
padding:5px 10px;
border-radius:20px;
font-size:11px;
font-weight:bold
}

.search{
width:100%;
padding:13px;
border-radius:12px;
border:1px solid #ffffff12;
background:#ffffff08;
color:white;
outline:none;
margin-bottom:10px
}

.picker{
max-height:260px;
overflow:auto;
display:flex;
flex-direction:column;
gap:5px
}

.item{
display:flex;
align-items:center;
gap:10px;
padding:11px;
border-radius:11px;
background:#ffffff04;
cursor:pointer
}

.item:hover{
background:#ffffff0a
}

.item input{
width:18px;
height:18px;
accent-color:#7555ff
}

.item-text{
flex:1
}

.item-type{
font-size:10px;
color:#818696
}

.quick{
display:flex;
gap:7px;
margin-top:10px;
flex-wrap:wrap
}

.quick button{
border:1px solid #ffffff12;
background:#ffffff08;
color:#c9ccd7;
padding:7px 10px;
border-radius:9px;
cursor:pointer
}

.toggle-box{
background:#ffffff06;
border:1px solid #ffffff10;
border-radius:17px;
padding:16px
}

.toggle-row{
display:flex;
align-items:center;
justify-content:space-between;
gap:15px
}

.switch{
position:relative;
width:54px;
height:30px
}

.switch input{
display:none
}

.slider{
position:absolute;
inset:0;
cursor:pointer;
background:#383b47;
border-radius:30px
}

.slider:before{
content:"";
position:absolute;
width:24px;
height:24px;
left:3px;
top:3px;
background:white;
border-radius:50%;
transition:.2s
}

.switch input:checked + .slider{
background:
linear-gradient(
90deg,
#087dff,
#7555ff,
#ffd900
)
}

.switch input:checked + .slider:before{
transform:translateX(24px)
}

.admin-badge{
display:inline-block;
margin-right:8px;
padding:4px 8px;
border-radius:8px;
background:#ffd90018;
color:#ffe978;
font-size:11px
}

.info{
color:#9398a8;
font-size:13px;
line-height:1.7
}

.save{
width:100%;
margin-top:20px
}

.empty{
text-align:center;
color:#858b9a;
padding:25px
}

@media(max-width:600px){

.command{
flex-direction:column;
align-items:stretch
}

.command .btn{
width:100%
}

}

</style>
</head>

<body>

<div class="container">

<nav>

<a class="btn"
href="/server/{{ guild_id }}">
← رجوع
</a>

<div class="logo">
ضياء <span>BOT</span>
</div>

</nav>

<div class="card">

<h1>
🧩 إدارة الأوامر
</h1>

<p class="info">
اختر الأمر ثم حدد حالته والرومات والرتب التي يسمح لها باستخدامه.
</p>

</div>


{% if commands %}

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

{% else %}

<div class="card empty">
لا توجد أوامر محفوظة حالياً.
</div>

{% endif %}

</div>


<div class="modal-bg" id="modalBg">

<div class="modal">

<span class="close"
onclick="closeModal()">
×
</span>

<h2 id="modalTitle">
⚙️ إعداد الأمر
</h2>


<div class="toggle-box">

<div class="toggle-row">

<div>

<b>
حالة الأمر
</b>

<div class="info">
عند إيقاف الأمر لن يستطيع أحد استخدامه.
</div>

</div>

<label class="switch">

<input
type="checkbox"
id="enabledCheck">

<span class="slider"></span>

</label>

</div>

</div>


<div class="section">

<div class="section-title">

<b>
📁 الرومات المسموحة
</b>

<span class="counter"
id="channelCounter">
0 محدد
</span>

</div>

<input
class="search"
id="channelSearch"
placeholder="🔎 ابحث عن روم..."
oninput="filterChannels()"
>

<div class="quick">

<button onclick="selectAllChannels()">
تحديد الكل
</button>

<button onclick="clearAllChannels()">
إلغاء الكل
</button>

</div>

<div class="picker"
id="channelsMenu">

{% if channels %}

{% for channel in channels %}

<label
class="item channel-item"
data-name="{{ channel.name|lower }}">

<input
type="checkbox"
class="channel-check"
value="{{ channel.id }}">

<div class="item-text">
# {{ channel.name }}
</div>

<div class="item-type">
{{ channel.type }}
</div>

</label>

{% endfor %}

{% else %}

<div class="empty">
❌ لم يتم العثور على رومات كتابية.
</div>

{% endif %}

</div>

</div>


<div class="section">

<div class="section-title">

<b>
🎭 الرتب المسموحة
</b>

<span class="counter"
id="roleCounter">
0 محدد
</span>

</div>

<input
class="search"
id="roleSearch"
placeholder="🔎 ابحث عن رتبة..."
oninput="filterRoles()"
>

<div class="quick">

<button onclick="selectAllRoles()">
تحديد الكل
</button>

<button onclick="clearAllRoles()">
إلغاء الكل
</button>

</div>

<div class="picker"
id="rolesMenu">

{% if roles %}

{% for role in roles %}

<label
class="item role-item"
data-name="{{ role.name|lower }}">

<input
type="checkbox"
class="role-check"
value="{{ role.id }}">

<div class="item-text">
{{ role.name }}
</div>

</label>

{% endfor %}

{% else %}

<div class="empty">
❌ لا توجد رتب.
</div>

{% endif %}

</div>

</div>


<p class="info">

💡 إذا تركت الرومات والرتب بدون تحديد،
فإن التقييد من الموقع لن يفرض على الأمر.

</p>


<button
type="button"
class="btn save"
onclick="saveSettings()">

💾 حفظ الإعدادات

</button>

</div>

</div>


<script>

let selectedCommand="";


function updateCounters(){

const channels=
document.querySelectorAll(
".channel-check:checked"
).length;

const roles=
document.querySelectorAll(
".role-check:checked"
).length;

document.getElementById(
"channelCounter"
).innerText=
channels+" محدد";

document.getElementById(
"roleCounter"
).innerText=
roles+" محدد";

}


function openSettings(command){

selectedCommand=command;

document.getElementById(
"modalTitle"
).innerText=
"⚙️ إعداد: "+command;

document.getElementById(
"modalBg"
).style.display="flex";

document.querySelectorAll(
".channel-check"
).forEach(
x=>x.checked=false
);

document.querySelectorAll(
".role-check"
).forEach(
x=>x.checked=false
);

document.getElementById(
"enabledCheck"
).checked=false;

updateCounters();

fetch(
"/api/command-settings?guild={{ guild_id }}&command="
+
encodeURIComponent(command)
)
.then(r=>r.json())
.then(data=>{

if(!data.success){

alert(
"❌ "+
(data.error||"حدث خطأ.")
);

return;

}

(data.channel_ids||[]).forEach(
id=>{

id=String(id);

const box=
document.querySelector(
'.channel-check[value="'+id+'"]'
);

if(box){
box.checked=true;
}

}
);

(data.role_ids||[]).forEach(
id=>{

id=String(id);

const box=
document.querySelector(
'.role-check[value="'+id+'"]'
);

if(box){
box.checked=true;
}

}
);

document.getElementById(
"enabledCheck"
).checked=
data.enabled===true;

updateCounters();

})
.catch(
()=>alert(
"❌ تعذر جلب إعدادات الأمر."
)
);

}


function closeModal(){

document.getElementById(
"modalBg"
).style.display="none";

}


function selectAllChannels(){

document.querySelectorAll(
".channel-item"
).forEach(
item=>{

if(item.style.display==="none")
return;

const box=
item.querySelector(
".channel-check"
);

if(box)
box.checked=true;

}
);

updateCounters();

}


function clearAllChannels(){

document.querySelectorAll(
".channel-check"
).forEach(
box=>box.checked=false
);

updateCounters();

}


function selectAllRoles(){

document.querySelectorAll(
".role-item"
).forEach(
item=>{

if(item.style.display==="none")
return;

const box=
item.querySelector(
".role-check"
);

if(box)
box.checked=true;

}
);

updateCounters();

}


function clearAllRoles(){

document.querySelectorAll(
".role-check"
).forEach(
box=>box.checked=false
);

updateCounters();

}


function filterChannels(){

const value=
document.getElementById(
"channelSearch"
).value.toLowerCase();

document.querySelectorAll(
".channel-item"
).forEach(
item=>{

const name=
item.dataset.name || "";

item.style.display=
name.includes(value)
?"flex"
:"none";

}
);

}


function filterRoles(){

const value=
document.getElementById(
"roleSearch"
).value.toLowerCase();

document.querySelectorAll(
".role-item"
).forEach(
item=>{

const name=
item.dataset.name || "";

item.style.display=
name.includes(value)
?"flex"
:"none";

}
);


}


document.addEventListener(
"change",
function(e){

if(
e.target.classList.contains(
"channel-check"
)
||
e.target.classList.contains(
"role-check"
)
){

updateCounters();

}

}
);


function saveSettings(){

const channels=
Array.from(
document.querySelectorAll(
".channel-check:checked"
)
).map(
x=>String(x.value)
);

const roles=
Array.from(
document.querySelectorAll(
".role-check:checked"
)
).map(
x=>String(x.value)
);

const enabled=
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
r=>r.json()
)
.then(
data=>{

if(data.success){

alert(
"✅ تم حفظ إعدادات الأمر بنجاح."
);

closeModal();

}else{

alert(
"❌ "+
(data.error||"حدث خطأ")
);

}

}
)
.catch(
()=>alert(
"❌ تعذر الاتصال بالموقع."
)
);

}

</script>

</body>
</html>
"""


@app.route("/commands")
def commands_page():

    guild_id = str(
        request.args.get(
            "guild",
            ""
        )
    ).strip()

    if not guild_id:
        return redirect(
            url_for("dashboard")
        )

    if not user_can_control(guild_id):
        return redirect(
            url_for("dashboard")
        )

    discord_channels = get_bot_channels(
        guild_id
    )

    discord_roles = get_bot_roles(
        guild_id
    )

    channels = prepare_channels_for_picker(
        discord_channels
    )

    roles = []

    for role in discord_roles:

        if not isinstance(role, dict):
            continue

        role_id = str(
            role.get(
                "id",
                ""
            )
        )

        if not role_id:
            continue

        # @everyone
        if role_id == guild_id:
            continue

        roles.append({
            "id": role_id,
            "name": str(
                role.get(
                    "name",
                    "رتبة"
                )
            ),
            "position": role.get(
                "position",
                0
            )
        })

    roles.sort(
        key=lambda x: x.get(
            "position",
            0
        ),
        reverse=True
    )

    raw_commands = list(
        commands_collection.find({})
    )

    commands = []

    seen = set()

    for command in raw_commands:

        if not isinstance(command, dict):
            continue

        command_name = str(
            command.get(
                "name",
                command.get(
                    "command_name",
                    ""
                )
            )
        ).strip()

        if not command_name:
            continue

        if command_name in seen:
            continue

        seen.add(command_name)

        commands.append({
            "name": command_name,
            "description": str(
                command.get(
                    "description",
                    "لا يوجد وصف لهذا الأمر."
                )
            ),
            "is_admin_display":
            is_admin_command(command)
        })

    commands.sort(
        key=lambda x:
        x.get("name", "")
    )

    return render_template_string(
        COMMANDS_HTML,
        guild_id=guild_id,
        commands=commands,
        channels=channels,
        roles=roles
    )


# =========================================================
# جلب إعداد الأمر
# =========================================================

@app.route("/api/command-settings")
def command_settings():

    guild_id = request.args.get(
        "guild"
    )

    command_name = request.args.get(
        "command"
    )

    if not guild_id or not command_name:
        return {
            "success": False
        }

    guild_id = str(
        guild_id
    ).strip()

    command_name = str(
        command_name
    ).strip()

    if not user_can_control(guild_id):
        return {
            "success": False,
            "error": "غير مصرح"
        }, 403

    if (
        command_name_is_admin(command_name)
        and not is_bot_owner()
    ):
        return {
            "success": False,
            "error":
            "هذا الأمر متاح لصاحب البوت فقط."
        }, 403

    setting = settings_collection.find_one({
        "guild_id": guild_id,
        "command_name": command_name
    })

    if not setting:

        return {
            "success": True,
            "channel_ids": [],
            "role_ids": [],
            "enabled": True
        }

    return {
        "success": True,
        "channel_ids": [
            str(x)
            for x in setting.get(
                "channel_ids",
                []
            )
        ],
        "role_ids": [
            str(x)
            for x in setting.get(
                "role_ids",
                []
            )
        ],
        "enabled":
        setting.get(
            "enabled",
            True
        )
    }


# =========================================================
# حفظ إعداد الأمر
# =========================================================

@app.route(
    "/save-command",
    methods=["POST"]
)
def save_command():

    data = request.get_json(
        silent=True
    ) or {}

    guild_id = str(
        data.get(
            "guild_id",
            ""
        )
    ).strip()

    command_name = str(
        data.get(
            "command_name",
            ""
        )
    ).strip()

    channel_ids = [
        str(x)
        for x in data.get(
            "channel_ids",
            []
        )
        if str(x).strip()
    ]

    role_ids = [
        str(x)
        for x in data.get(
            "role_ids",
            []
        )
        if str(x).strip()
    ]

    if not guild_id or not command_name:
        return {
            "success": False,
            "error": "بيانات ناقصة"
        }, 400

    if not user_can_control(guild_id):
        return {
            "success": False,
            "error": "غير مصرح لك"
        }, 403

    if (
        command_name_is_admin(command_name)
        and not is_bot_owner()
    ):
        return {
            "success": False,
            "error":
            "هذا الأمر متاح لصاحب البوت فقط."
        }, 403

    settings_collection.update_one(
        {
            "guild_id": guild_id,
            "command_name": command_name
        },
        {
            "$set": {
                "guild_id": guild_id,
                "command_name": command_name,
                "channel_ids": channel_ids,
                "role_ids": role_ids,
                "enabled": bool(
                    data.get(
                        "enabled",
                        True
                    )
                )
            }
        },
        upsert=True
    )

    return {
        "success": True
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

*{
box-sizing:border-box
}

body{
margin:0;
background:
radial-gradient(circle at 10% 10%,#008cff30,transparent 32%),
radial-gradient(circle at 90% 20%,#ffe60020,transparent 30%),
#070910;
color:white;
font-family:Arial
}

.container{
width:min(550px,92%);
margin:80px auto
}

.card{
background:#ffffff08;
border:1px solid #ffffff12;
border-radius:24px;
padding:27px;
backdrop-filter:blur(14px)
}

h1{
margin-top:0
}

input,select{
width:100%;
padding:14px;
margin:8px 0 18px;
box-sizing:border-box;
border-radius:13px;
border:1px solid #ffffff14;
background:#ffffff09;
color:white;
outline:none
}

button{
width:100%;
padding:14px;
border:1px solid #ffffff15;
border-radius:14px;
background:
linear-gradient(
135deg,
#087dff,
#7555ff 55%,
#ffd900
);
color:white;
font-weight:bold;
cursor:pointer
}

.back{
display:block;
margin-top:15px;
color:#aab4ff;
text-align:center;
text-decoration:none
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

<a class="back"
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
    methods=["GET", "POST"]
)
def create_channel():

    guild_id = str(
        request.args.get(
            "guild",
            ""
        )
    ).strip()

    if not guild_id:
        return redirect(
            url_for("dashboard")
        )

    if not user_can_control(guild_id):
        return redirect(
            url_for("dashboard")
        )

    if request.method == "GET":

        return render_template_string(
            CREATE_CHANNEL_HTML,
            guild_id=guild_id
        )

    name = (
        request.form.get(
            "name",
            ""
        ).strip()
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
        "name": name,
        "type":
        0
        if channel_type == "text"
        else 2
    }

    response = discord_request(
        "POST",
        f"{DISCORD_API}/guilds/{guild_id}/channels",
        json=payload
    )

    if response is None:
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

    cache_delete(
        f"bot_channels:{guild_id}"
    )

    return redirect(
        url_for(
            "server_page",
            guild_id=guild_id
        )
    )


# =========================================================
# تسجيل الخروج
# =========================================================

@app.route("/logout")
def logout():

    token = session.get(
        "access_token"
    )

    if token:

        cache_delete(
            "oauth_user:"
            + token[:16]
        )

        cache_delete(
            "oauth_guilds:"
            + token[:16]
        )

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
