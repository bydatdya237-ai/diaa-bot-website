import os
import secrets
import time
import threading
from urllib.parse import urlencode
from datetime import datetime, timezone
import hmac

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

FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY")

if not FLASK_SECRET_KEY:
    raise RuntimeError("FLASK_SECRET_KEY غير موجود - أضفه في Railway Variables")

app.secret_key = FLASK_SECRET_KEY
app.config.update({
    "SESSION_COOKIE_HTTPONLY": True,
    "SESSION_COOKIE_SECURE": True,
    "SESSION_COOKIE_SAMESITE": "Lax",
    "PERMANENT_SESSION_LIFETIME": 3600,
})


# =========================================================
# CSRF protection للطلبات التي تغيّر البيانات
# =========================================================

CSRF_SESSION_KEY = "csrf_token"


def get_csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


@app.context_processor
def inject_template_helpers():
    return {"csrf_token": get_csrf_token}


@app.before_request
def protect_state_changing_requests():
    if request.method != "POST":
        return None

    token = session.get(CSRF_SESSION_KEY, "")
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")

    if not token or not supplied or not hmac.compare_digest(str(token), str(supplied)):
        return {"success": False, "error": "طلب غير صالح. حدّث الصفحة وحاول مرة أخرى."}, 403

    return None


CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
BOT_TOKEN = os.getenv("TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

BASE_URL = os.getenv(
    "WEBSITE_URL",
    "https://diaa-bot-website-production.up.railway.app"
).rstrip("/")

REDIRECT_URI = f"{BASE_URL}/callback"
OAUTH_INSTALL_REDIRECT_URI = f"{BASE_URL}/install-callback"

BOT_INVITE_PERMISSIONS = os.getenv("BOT_INVITE_PERMISSIONS", "144").strip()

OAUTH_STATE_SESSION_KEY = "oauth_state"

BOT_OWNER_ID = "1154374165642620948"

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
aliases_collection = db["website_command_aliases"]
economy_settings_collection = db["economy_settings"]

try:
    settings_collection.create_index(
        [("guild_id", 1), ("command_name", 1)],
        unique=True,
        background=True,
    )
    aliases_collection.create_index(
        [("guild_id", 1), ("alias", 1)],
        unique=True,
        background=True,
    )
    guilds_collection.create_index(
        [("guild_id", 1)],
        unique=True,
        background=True,
    )
except Exception:
    pass


# =========================================================
# Discord API
# =========================================================

DISCORD_API = "https://discord.com/api/v10"

discord_session = requests.Session()

discord_session.headers.update({
    "Authorization": f"Bot {BOT_TOKEN}",
    "Content-Type": "application/json",
    "User-Agent": "DiaaBOT-Website/1.0",
})

discord_rate_lock = threading.Lock()
discord_rate_until = 0.0

CACHE_TTL = 30

_cache_lock = threading.Lock()
_cache = {}


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
            value,
        )


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


def discord_request(
    method,
    url,
    *,
    json=None,
    data=None,
    timeout=10,
    max_attempts=2,
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
                timeout=timeout,
            )

        except requests.RequestException:
            if attempt + 1 >= max_attempts:
                return None

            time.sleep(2)
            continue

        if response.status_code != 429:
            return response

        retry_after = None

        try:
            body = response.json()
            retry_after = float(
                body.get("retry_after", 0)
            )
        except Exception:
            pass

        if retry_after is None or retry_after <= 0:
            try:
                retry_after = float(
                    response.headers.get(
                        "Retry-After",
                        "5"
                    )
                )
            except (TypeError, ValueError):
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
# OAuth - المستخدم الحالي
# =========================================================

def get_user():
    token = session.get("access_token")

    if not token:
        return None

    cache_key = f"oauth_user:{token[:16]}"

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
        ttl=30
    )

    return user


def is_bot_owner():
    user = get_user()

    if not user:
        return False

    return str(user.get("id")) == BOT_OWNER_ID


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
            {"command_name": command_name},
        ]
    })

    if command and is_admin_command(command):
        return True

    return False


# =========================================================
# Discord Guilds الخاصة بالمستخدم
# =========================================================

def get_user_guilds():
    token = session.get("access_token")

    if not token:
        return []

    cache_key = f"oauth_guilds:{token[:16]}"

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
        ttl=30
    )

    return guilds


# =========================================================
# Discord Bot API
# =========================================================

def bot_in_guild(guild_id):
    return get_bot_guild(guild_id) is not None


def get_bot_guild(guild_id):
    guild_id = str(guild_id)

    cache_key = f"bot_guild:{guild_id}"

    cached = cache_get(cache_key)

    if cached is not None:
        return cached

    response = discord_request(
        "GET",
        f"{DISCORD_API}/guilds/{guild_id}",
    )

    if response is None:
        return None

    if response.status_code != 200:
        return None

    guild = response.json()

    cache_set(
        cache_key,
        guild,
        ttl=CACHE_TTL
    )

    return guild


def get_bot_channels(guild_id):
    guild_id = str(guild_id)

    cache_key = f"bot_channels:{guild_id}"

    cached = cache_get(cache_key)

    if cached is not None:
        return cached

    response = discord_request(
        "GET",
        f"{DISCORD_API}/guilds/{guild_id}/channels",
    )

    if response is None:
        return []

    if response.status_code != 200:
        return []

    channels = response.json()

    cache_set(
        cache_key,
        channels,
        ttl=CACHE_TTL
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
        f"{DISCORD_API}/guilds/{guild_id}/roles",
    )

    if response is None:
        return []

    if response.status_code != 200:
        return []

    roles = response.json()

    cache_set(
        cache_key,
        roles,
        ttl=CACHE_TTL
    )

    return roles


# =========================================================
# معالجة أنواع الرومات
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

        channel_type = normalize_channel_type(channel)

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
# التحقق من أن IDs المرسلة تنتمي فعلاً للسيرفر
# =========================================================

def valid_snowflake(value):
    value = str(value or "").strip()
    return value.isdigit() and 15 <= len(value) <= 21


def validate_guild_resources(guild_id, channel_ids=None, role_ids=None):
    guild_id = str(guild_id).strip()

    if not valid_snowflake(guild_id):
        return False, "معرّف السيرفر غير صحيح."

    channel_ids = [str(x).strip() for x in (channel_ids or []) if str(x).strip()]
    role_ids = [str(x).strip() for x in (role_ids or []) if str(x).strip()]

    if any(not valid_snowflake(x) for x in channel_ids):
        return False, "يوجد ID روم غير صحيح."

    if any(not valid_snowflake(x) for x in role_ids):
        return False, "يوجد ID رتبة غير صحيح."

    if channel_ids:
        channels = get_bot_channels(guild_id)
        existing = {
            str(c.get("id"))
            for c in channels
            if isinstance(c, dict)
        }
        missing = [x for x in channel_ids if x not in existing]
        if missing:
            return False, "يوجد روم غير موجود في هذا السيرفر."

    if role_ids:
        roles = get_bot_roles(guild_id)
        existing = {
            str(r.get("id"))
            for r in roles
            if isinstance(r, dict)
        }
        missing = [x for x in role_ids if x not in existing]
        if missing:
            return False, "يوجد رتبة غير موجودة في هذا السيرفر."

    return True, ""


def get_bot_user():
    cached = cache_get("bot_user")
    if cached is not None:
        return cached

    response = discord_request(
        "GET",
        f"{DISCORD_API}/users/@me",
    )

    if response is None or response.status_code != 200:
        return None

    user = response.json()
    cache_set("bot_user", user, ttl=300)
    return user


def find_bot_installer(guild_id):
    bot_user = get_bot_user()
    if not bot_user:
        return None

    response = discord_request(
        "GET",
        f"{DISCORD_API}/guilds/{guild_id}/audit-logs?limit=50&action_type=28",
    )

    if response is None or response.status_code != 200:
        return None

    try:
        entries = response.json().get("audit_log_entries", [])
    except Exception:
        return None

    bot_id = str(bot_user.get("id", ""))

    for entry in entries:
        if str(entry.get("target_id", "")) == bot_id:
            executor = entry.get("user_id")
            if executor:
                return str(executor)

    return None


def ensure_installer_record(guild_id):
    guild_id = str(guild_id).strip()
    existing = guilds_collection.find_one({"guild_id": guild_id})

    if existing and existing.get("installer_verified") is True:
        return str(existing.get("installer_id", "")) or None

    installer_id = find_bot_installer(guild_id)
    if not installer_id:
        return None

    guilds_collection.update_one(
        {"guild_id": guild_id},
        {
            "$set": {
                "guild_id": guild_id,
                "installer_id": installer_id,
                "installer_verified": True,
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )

    return installer_id


# =========================================================
# تصنيف الأوامر
# =========================================================

COMMAND_CATEGORY_ORDER = {
    "إدارية": 1,
    "اقتصاد": 2,
    "ألعاب": 3,
    "عامة": 4,
    "أخرى": 5,
}


def get_command_category(command):
    if not isinstance(command, dict):
        return "أخرى"

    explicit = str(
        command.get("category", command.get("category_name", ""))
    ).strip()

    if explicit:
        aliases = {
            "admin": "إدارية",
            "administration": "إدارية",
            "moderation": "إدارية",
            "إدارة": "إدارية",
            "اقتصادي": "اقتصاد",
            "economy": "اقتصاد",
            "game": "ألعاب",
            "games": "ألعاب",
            "لعبة": "ألعاب",
            "general": "عامة",
        }
        return aliases.get(explicit.lower(), explicit)

    name = str(command.get("name", command.get("command_name", ""))).strip()

    if is_admin_command(command):
        return "إدارية"

    economy_names = {
        "رصيد",
        "توب",
        "اعطي",
        "سحب",
        "توزيع",
        "شرح",
        "شعار",
        "تعطيل",
        "تفعيل",
        "محفظتي",
        "ذهبي"
    }

    game_words = (
        "لعبة",
        "خمن",
        "الغام",
        "محفظتي",
        "ذهبي",
        "ابدا",
        "انشاء-لعبة",
        "تعديل",
        "انهي",
        "دن",
        "ط"
    )

    if name in economy_names:
        return "اقتصاد"

    if any(word in name for word in game_words):
        return "ألعاب"

    return "عامة"


# =========================================================
# صلاحية المستخدم على السيرفر
# =========================================================

def user_can_control(guild_id):
    user = get_user()

    if not user:
        return False

    guild_id = str(guild_id).strip()
    user_id = str(user.get("id", ""))

    if user_id == BOT_OWNER_ID:
        return get_bot_guild(guild_id) is not None

    for guild in get_user_guilds():
        if str(guild.get("id")) != guild_id:
            continue

        if guild.get("owner") is True:
            return get_bot_guild(guild_id) is not None

        break

    guild_data = guilds_collection.find_one({"guild_id": guild_id})

    if not guild_data or guild_data.get("installer_verified") is not True:
        ensure_installer_record(guild_id)
        guild_data = guilds_collection.find_one({"guild_id": guild_id})

    installer_id = str((guild_data or {}).get("installer_id", ""))

    return bool(
        installer_id
        and (guild_data or {}).get("installer_verified") is True
        and user_id == installer_id
        and get_bot_guild(guild_id) is not None
    )


# =========================================================
# HTML
# =========================================================

BASE_STYLE = """
<style>
*{box-sizing:border-box}
body{
    margin:0;
    background:
        radial-gradient(circle at top right,#682cff55,transparent 35%),
        radial-gradient(circle at bottom left,#9b4dff33,transparent 35%),
        #090914;
    color:white;
    font-family:Arial,sans-serif;
    min-height:100vh;
}
.container{width:min(1000px,92%);margin:auto}
nav{
    min-height:75px;
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:10px;
}
.logo{font-size:23px;font-weight:bold}
.logo span{color:#a66cff}
.card{
    background:#ffffff08;
    border:1px solid #ffffff12;
    border-radius:20px;
    padding:22px;
    margin-bottom:15px;
    backdrop-filter:blur(12px);
}
.btn{
    display:inline-block;
    border:0;
    padding:12px 19px;
    border-radius:13px;
    background:linear-gradient(135deg,#7136ff,#ad65ff);
    color:white;
    text-decoration:none;
    font-weight:bold;
    cursor:pointer;
}
.secondary{background:#ffffff0d}
.danger{background:linear-gradient(135deg,#b42323,#e05252)}
.small,.info,.desc{color:#9996a8;font-size:14px;line-height:1.7}
.grid{
    display:grid;
    grid-template-columns:repeat(2,1fr);
    gap:15px;
}
.big{font-size:30px;font-weight:bold;color:#b58aff}
.status{
    display:inline-block;
    padding:7px 12px;
    border-radius:20px;
    margin-top:10px;
    font-size:13px;
}
.status.on{background:#22c55e22;color:#6ee7a0}
.status.off{background:#ef444422;color:#ff8585}
input,select{
    width:100%;
    padding:14px;
    margin:8px 0 18px;
    border-radius:12px;
    border:1px solid #ffffff14;
    background:#ffffff0b;
    color:white;
    outline:none;
}
.command{
    display:flex;
    justify-content:space-between;
    align-items:center;
    gap:15px;
}
.name{font-weight:bold;font-size:18px}
.modal-bg{
    display:none;
    position:fixed;
    inset:0;
    background:#000b;
    align-items:center;
    justify-content:center;
    padding:15px;
}
.modal{
    width:min(600px,100%);
    max-height:90vh;
    overflow:auto;
    background:#11111e;
    border:1px solid #ffffff15;
    border-radius:22px;
    padding:23px;
}
.close{float:left;cursor:pointer;font-size:22px}
.menu{
    display:none;
    margin-top:7px;
    background:#181827;
    border:1px solid #ffffff12;
    border-radius:14px;
    padding:8px;
    max-height:220px;
    overflow:auto;
}
.item{display:block;padding:9px;border-radius:9px}
.item:hover{background:#ffffff09}
.toggle-box{
    background:#ffffff08;
    border:1px solid #ffffff12;
    border-radius:14px;
    padding:14px;
    margin-bottom:15px;
}
.toggle-row{
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:15px;
}
.switch{position:relative;width:52px;height:28px}
.switch input{display:none}
.slider{
    position:absolute;
    inset:0;
    cursor:pointer;
    background:#383847;
    border-radius:30px;
}
.slider:before{
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
.switch input:checked+.slider{background:#7136ff}
.switch input:checked+.slider:before{transform:translateX(24px)}
.admin-badge{
    display:inline-block;
    margin-right:8px;
    padding:4px 8px;
    border-radius:8px;
    background:#7136ff22;
    color:#c7aaff;
    font-size:11px;
}

.alias-box{
    background:#ffffff08;
    border:1px solid #ffffff12;
    border-radius:14px;
    padding:14px;
    margin-top:18px;
}
.alias-row{
    display:flex;
    gap:8px;
    align-items:center;
    margin-bottom:8px;
}
.alias-row .alias-name{
    flex:1;
    padding:10px 12px;
    border-radius:10px;
    background:#ffffff09;
    border:1px solid #ffffff10;
}
.alias-remove{
    border:0;
    border-radius:9px;
    padding:8px 11px;
    cursor:pointer;
    color:white;
    background:#b42323;
}
@media(max-width:650px){
    .grid{grid-template-columns:1fr}
    .command{flex-direction:column;align-items:stretch}
    .command .btn{width:100%;text-align:center}
}
</style>
"""


HOME_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ضياء BOT</title>
__BASE_STYLE__
<style>
.hero{text-align:center;padding:90px 0 70px}
.badge{
    display:inline-block;
    background:#ffffff0d;
    border:1px solid #ffffff15;
    padding:8px 14px;
    border-radius:30px;
    color:#cbb8ff;
}
h1{font-size:clamp(40px,8vw,75px);margin:18px 0}
h1 span{
    background:linear-gradient(90deg,#a66cff,#e0caff);
    -webkit-background-clip:text;
    color:transparent;
}
.hero p{
    color:#aaa7b8;
    font-size:18px;
    line-height:1.8;
    max-width:650px;
    margin:20px auto 30px;
}
.features{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}
.feature h3{margin-top:0}
@media(max-width:700px){.features{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="container">
<nav>
<div class="logo">ضياء <span>BOT</span></div>
<a class="btn" href="/login">تسجيل الدخول</a>
</nav>
<section class="hero">
<div class="badge">Discord Bot Control Panel</div>
<h1>تحكم ببوتك <span>بسهولة.</span></h1>
<p>
لوحة تحكم احترافية لبوت ضياء،
لإدارة الأوامر والرومات والرتب
من مكان واحد وبواجهة بسيطة.
</p>
<a class="btn" href="/login">🚀 دخول لوحة التحكم</a>
</section>
<section class="features">
<div class="card"><h3>⚙️ إدارة الأوامر</h3><p class="info">اختر الرومات والرتب المسموح لها باستخدام كل أمر.</p></div>
<div class="card"><h3>📁 إدارة الرومات</h3><p class="info">أنشئ رومات جديدة من لوحة التحكم.</p></div>
<div class="card"><h3>🔐 تحكم آمن</h3><p class="info">التحكم متاح لصاحب السيرفر أو الشخص الذي أضاف البوت.</p></div>
</section>
</div>
</body>
</html>
""".replace("__BASE_STYLE__", BASE_STYLE)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>لوحة التحكم - ضياء BOT</title>
__BASE_STYLE__
</head>
<body>
<div class="container">
<nav>
<div class="logo">ضياء <span>BOT</span></div>
<a class="btn" href="/logout">تسجيل خروج</a>
</nav>
<h1>سيرفراتك</h1>
{% if guilds %}
{% for guild in guilds %}
<div class="card command">
<div>
<div class="name">{{ guild.name }}</div>
<div class="small">
{% if guild.status == "owner" %}👑 مالك السيرفر
{% elif guild.status == "installer" %}🔑 الشخص الذي أضاف البوت
{% elif guild.status == "bot_owner" %}👑 صاحب البوت
{% else %}🔑 الشخص الذي أضاف البوت{% endif %}
</div>
</div>
<a class="btn" href="/server/{{ guild.id }}">إدارة السيرفر</a>
</div>
{% endfor %}
{% else %}
<div class="card" style="text-align:center;padding:50px 20px">
<h2>لا يوجد سيرفر متاح</h2>
<p class="info">تأكد أن البوت موجود في السيرفر وأنك مالك السيرفر أو الشخص الذي أضاف البوت.</p>
<a class="btn" href="/invite">➕ إضافة البوت</a>
</div>
{% endif %}
</div>
</body>
</html>
""".replace("__BASE_STYLE__", BASE_STYLE)

SERVER_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ guild_name }} - ضياء BOT</title>
__BASE_STYLE__
</head>
<body>
<div class="container">
<nav>
<a class="btn secondary" href="/dashboard">← السيرفرات</a>
<a class="btn secondary" href="/logout">خروج</a>
</nav>

<div class="card">
<h1>⚡ {{ guild_name }}</h1>
<p>لوحة تحكم السيرفر</p>
</div>

<div class="grid">
<div class="card"><div class="big">{{ command_count }}</div><div>الأوامر</div></div>
<div class="card"><div class="big">{{ channel_count }}</div><div>الرومات</div></div>
<div class="card"><div class="big">{{ role_count }}</div><div>الرتب</div></div>
</div>

<div class="card">
<h2>💰 نظام الاقتصاد</h2>
<p class="info">
من هنا تقوم بتفعيل نظام الاقتصاد لهذا السيرفر وتحديد روم الاقتصاد.
تغيير الإعدادات هنا لا يحذف أرصدة اللاعبين ولا يصفر البيانات.
</p>

{% if economy_enabled %}
<div class="status on">🟢 نظام الاقتصاد مفعّل</div>
<p class="info">
روم الاقتصاد الحالي:
<br><b>#{{ economy_room_name }}</b>
<br>ID: {{ economy_room_id }}
</p>
<button class="btn" onclick="changeEconomyRoom()">⚙️ تغيير روم الاقتصاد</button>
<button class="btn danger" onclick="disableEconomy()">🔴 تعطيل نظام الاقتصاد</button>
{% else %}
<div class="status off">🔴 نظام الاقتصاد غير مفعّل</div>
<p class="info">أدخل ID الروم الذي تريد استخدامه للاقتصاد.</p>
<input id="economyRoomId" placeholder="مثال: 1544334212734124174" inputmode="numeric">
<button class="btn" onclick="enableEconomy()">💰 تفعيل نظام الاقتصاد</button>
{% endif %}
<div id="economyMessage" class="card" style="display:none"></div>
</div>

<div class="card">
<h2>🧩 إدارة الأوامر</h2>
<p>تحكم في الرومات والرتب والتفعيل الخاص بكل أمر.</p>
<a class="btn" href="/commands?guild={{ guild_id }}">فتح الأوامر</a>
</div>

<div class="card">
<h2>📁 إنشاء روم</h2>
<p>أنشئ روم جديد داخل السيرفر.</p>
<a class="btn" href="/create-channel?guild={{ guild_id }}">إنشاء روم</a>
</div>
</div>

<script>
function showEconomyMessage(message, success){
    const box=document.getElementById("economyMessage");
    box.innerText=message;
    box.style.display="block";
}

function enableEconomy(){
    const input=document.getElementById("economyRoomId");
    if(!input)return;
    const roomId=input.value.trim();

    if(!/^\\d+$/.test(roomId)){
        showEconomyMessage("❌ أدخل ID روم صحيح.",false);
        return;
    }

    fetch("/api/economy/enable",{
        method:"POST",
        headers:{"Content-Type":"application/json","X-CSRF-Token":"{{ csrf_token() }}"},
        body:JSON.stringify({
            guild_id:"{{ guild_id }}",
            economy_room_id:roomId
        })
    })
    .then(r=>r.json())
    .then(data=>{
        if(data.success){
            alert("✅ تم حفظ وتفعيل نظام الاقتصاد.");
            location.reload();
        }else{
            showEconomyMessage("❌ "+(data.error||"حدث خطأ."),false);
        }
    })
    .catch(()=>showEconomyMessage("❌ تعذر الاتصال بالموقع.",false));
}

function changeEconomyRoom(){
    const roomId=prompt("أدخل ID روم الاقتصاد الجديد:");
    if(!roomId)return;

    const cleanRoomId=roomId.trim();

    if(!/^\\d+$/.test(cleanRoomId)){
        alert("❌ ID الروم غير صحيح.");
        return;
    }

    fetch("/api/economy/enable",{
        method:"POST",
        headers:{"Content-Type":"application/json","X-CSRF-Token":"{{ csrf_token() }}"},
        body:JSON.stringify({
            guild_id:"{{ guild_id }}",
            economy_room_id:cleanRoomId
        })
    })
    .then(r=>r.json())
    .then(data=>{
        if(data.success){
            alert("✅ تم حفظ روم الاقتصاد الجديد.");
            location.reload();
        }else{
            alert("❌ "+(data.error||"حدث خطأ"));
        }
    })
    .catch(()=>alert("❌ تعذر الاتصال بالموقع."));
}

function disableEconomy(){
    if(!confirm("هل أنت متأكد من تعطيل نظام الاقتصاد؟\\n\\nلن يتم حذف أرصدة اللاعبين أو أي بيانات."))return;

    fetch("/api/economy/disable",{
        method:"POST",
        headers:{"Content-Type":"application/json","X-CSRF-Token":"{{ csrf_token() }}"},
        body:JSON.stringify({guild_id:"{{ guild_id }}"})
    })
    .then(r=>r.json())
    .then(data=>{
        if(data.success){
            alert("🔴 تم تعطيل نظام الاقتصاد.");
            location.reload();
        }else{
            alert("❌ "+(data.error||"حدث خطأ"));
        }
    })
    .catch(()=>alert("❌ تعذر الاتصال بالموقع."));
}
</script>
</body>
</html>
""".replace("__BASE_STYLE__", BASE_STYLE)

COMMANDS_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>الأوامر - ضياء BOT</title>
__BASE_STYLE__
<style>
.category-title{{
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:10px;
    margin:28px 0 12px;
}}
.category-title h2{{margin:0}}
.category-count{{color:#aaa7b8;font-size:13px}}
.command-card{{margin-bottom:10px}}
</style>
</head>
<body>
<div class="container">
<nav>
<a class="btn" href="/server/{{ guild_id }}">← رجوع</a>
<h2>الأوامر</h2>
</nav>

{% if categories %}
{% for category in categories %}
<div class="category-title">
<h2>{{ category.title }}</h2>
<div class="category-count">{{ category.commands|length }} أمر</div>
</div>

{% for command in category.commands %}
<div class="card command command-card">
<div>
<div class="name">
{{ command.name }}
{% if command.is_admin_display %}
<span class="admin-badge">👑 إدارة</span>
{% endif %}
</div>
<div class="desc">{{ command.description }}</div>
</div>
<button class="btn" onclick='openSettings({{ command.name|tojson }})'>⚙️ إعداد</button>
</div>
{% endfor %}
{% endfor %}
{% else %}
<div class="card">لا توجد أوامر محفوظة حالياً.</div>
{% endif %}
</div>

<div class="modal-bg" id="modalBg">
<div class="modal">
<span class="close" onclick="closeModal()">×</span>
<h2 id="modalTitle">إعداد الأمر</h2>

<div class="toggle-box">
<div class="toggle-row">
<div>
<b>حالة الأمر</b>
<div class="info">فعّل الأمر أو عطّله من الموقع.</div>
</div>
<label class="switch">
<input type="checkbox" id="enabledCheck">
<span class="slider"></span>
</label>
</div>
</div>

<div class="toggle-box">
<div class="toggle-row">
<div>
<b>📢 جميع الرومات</b>
<div class="info">إذا فعّلتها، سيُسمح للأمر في جميع الرومات. إعداد البوت يجب أن يقرأ all_channels.</div>
</div>
<label class="switch">
<input type="checkbox" id="allChannelsCheck">
<span class="slider"></span>
</label>
</div>
</div>

<h3>📁 الرومات المسموحة</h3>
<button type="button" class="btn secondary" style="width:100%;text-align:right" onclick="togglePicker('channelsMenu')" id="channelButton">📁 اختيار الرومات</button>
<div class="menu" id="channelsMenu">
{% if channels %}
{% for channel in channels %}
<label class="item">
<input type="checkbox" class="channel-check" value="{{ channel.id }}">
#{{ channel.name }}
</label>
{% endfor %}
{% else %}
<div class="info">❌ لم يتم العثور على رومات كتابية.</div>
{% endif %}
</div>

<h3>🎭 الرتب المسموحة</h3>
<button type="button" class="btn secondary" style="width:100%;text-align:right" onclick="togglePicker('rolesMenu')" id="roleButton">🎭 اختيار الرتب</button>
<div class="menu" id="rolesMenu">
{% if roles %}
{% for role in roles %}
<label class="item">
<input type="checkbox" class="role-check" value="{{ role.id }}">
{{ role.name }}
</label>
{% endfor %}
{% else %}
<div class="info">❌ لا توجد رتب.</div>
{% endif %}
</div>

<div class="alias-box">
<h3>🔗 اختصارات الأمر</h3>
<div class="info">أي اختصار تضيفه هنا سيعمل في الديسكورد كاسم بديل لهذا الأمر، مع بقاء إعدادات الرتب والرومات الخاصة بالأمر الأصلي.</div>
<div style="display:flex;gap:8px;margin-top:10px">
<input id="aliasInput" placeholder="مثال: رصيدي" maxlength="50" style="margin:0">
<button type="button" class="btn" onclick="addAlias()">➕ إضافة</button>
</div>
<div id="aliasesList" style="margin-top:12px"></div>
</div>

<button type="button" class="btn" style="width:100%;margin-top:20px" onclick="saveSettings()">💾 حفظ الإعدادات</button>
</div>
</div>

<script>
let selectedCommand="";

function loadAliases(){
    const list=document.getElementById("aliasesList");
    if(!list)return;
    list.innerHTML="<div class=\"info\">جاري التحميل...</div>";

    fetch("/api/command-aliases?guild={{ guild_id }}&command="+encodeURIComponent(selectedCommand))
    .then(r=>r.json())
    .then(data=>{
        if(!data.success){
            list.innerHTML="<div class=\"info\">❌ "+(data.error||"تعذر جلب الاختصارات.")+"</div>";
            return;
        }

        if(!data.aliases || !data.aliases.length){
            list.innerHTML="<div class=\"info\">لا توجد اختصارات لهذا الأمر.</div>";
            return;
        }

        list.innerHTML=data.aliases.map(alias=>
            "<div class=\"alias-row\"><div class=\"alias-name\">"+escapeHtml(alias)+"</div>"+
            "<button type=\"button\" class=\"alias-remove\" onclick=\"removeAlias('"+encodeURIComponent(alias)+"')\">حذف</button></div>"
        ).join("");
    })
    .catch(()=>{list.innerHTML="<div class=\"info\">❌ تعذر الاتصال بالموقع.</div>";});
}

function escapeHtml(value){
    const div=document.createElement("div");
    div.textContent=String(value);
    return div.innerHTML;
}

function addAlias(){
    const input=document.getElementById("aliasInput");
    const alias=(input.value||"").trim();
    if(!alias){alert("❌ اكتب الاختصار أولاً.");return;}

    fetch("/api/command-aliases",{
        method:"POST",
        headers:{"Content-Type":"application/json","X-CSRF-Token":"{{ csrf_token() }}"},
        body:JSON.stringify({
            guild_id:"{{ guild_id }}",
            command_name:selectedCommand,
            alias:alias
        })
    })
    .then(r=>r.json())
    .then(data=>{
        if(data.success){
            input.value="";
            loadAliases();
        }else{
            alert("❌ "+(data.error||"تعذر إضافة الاختصار."));
        }
    })
    .catch(()=>alert("❌ تعذر الاتصال بالموقع."));
}

function removeAlias(encodedAlias){
    const alias=decodeURIComponent(encodedAlias);
    if(!confirm("حذف الاختصار: "+alias+" ؟"))return;

    fetch("/api/command-aliases",{
        method:"POST",
        headers:{"Content-Type":"application/json","X-CSRF-Token":"{{ csrf_token() }}"},
        body:JSON.stringify({
            guild_id:"{{ guild_id }}",
            command_name:selectedCommand,
            alias:alias,
            action:"delete"
        })
    })
    .then(r=>r.json())
    .then(data=>{
        if(data.success){loadAliases();}
        else{alert("❌ "+(data.error||"تعذر حذف الاختصار."));}
    })
    .catch(()=>alert("❌ تعذر الاتصال بالموقع."));
}


function openSettings(command){
    selectedCommand=command;
    document.getElementById("modalTitle").innerText="⚙️ إعداد: "+command;
    document.getElementById("modalBg").style.display="flex";

    document.querySelectorAll(".channel-check").forEach(x=>x.checked=false);
    document.querySelectorAll(".role-check").forEach(x=>x.checked=false);
    document.getElementById("enabledCheck").checked=false;
    document.getElementById("allChannelsCheck").checked=false;
    updateButtonText();

    fetch("/api/command-settings?guild={{ guild_id }}&command="+encodeURIComponent(command))
    .then(r=>r.json())
    .then(data=>{
        if(!data.success){
            alert("❌ "+(data.error||"تعذر جلب الإعدادات."));
            return;
        }

        (data.channel_ids||[]).forEach(id=>{
            const box=document.querySelector('.channel-check[value="'+String(id)+'"]');
            if(box)box.checked=true;
        });

        (data.role_ids||[]).forEach(id=>{
            const box=document.querySelector('.role-check[value="'+String(id)+'"]');
            if(box)box.checked=true;
        });

        document.getElementById("enabledCheck").checked=Boolean(data.enabled);
        document.getElementById("allChannelsCheck").checked=Boolean(data.all_channels);
        updateButtonText();
        loadAliases();
    })
    .catch(()=>alert("❌ تعذر الاتصال بالموقع."));
}

function closeModal(){
    document.getElementById("modalBg").style.display="none";
}

function togglePicker(id){
    const menu=document.getElementById(id);
    menu.style.display=menu.style.display==="block"?"none":"block";
}

function updateButtonText(){
    const channels=document.querySelectorAll(".channel-check:checked").length;
    const roles=document.querySelectorAll(".role-check:checked").length;
    const all=document.getElementById("allChannelsCheck").checked;

    document.getElementById("channelButton").innerText=
        all?"📢 جميع الرومات":(channels?"📁 تم اختيار "+channels+" روم":"📁 اختيار الرومات");

    document.getElementById("roleButton").innerText=
        roles?"🎭 تم اختيار "+roles+" رتبة":"🎭 اختيار الرتب";
}

document.addEventListener("change",function(e){
    if(e.target.classList.contains("channel-check")||e.target.classList.contains("role-check")||e.target.id==="allChannelsCheck"){
        updateButtonText();
    }
});

function saveSettings(){
    const allChannels=document.getElementById("allChannelsCheck").checked;
    const channels=allChannels?[]:Array.from(document.querySelectorAll(".channel-check:checked")).map(x=>String(x.value));
    const roles=Array.from(document.querySelectorAll(".role-check:checked")).map(x=>String(x.value));
    const enabled=document.getElementById("enabledCheck").checked;

    fetch("/save-command",{
        method:"POST",
        headers:{"Content-Type":"application/json","X-CSRF-Token":"{{ csrf_token() }}"},
        body:JSON.stringify({
            guild_id:"{{ guild_id }}",
            command_name:selectedCommand,
            channel_ids:channels,
            role_ids:roles,
            all_channels:allChannels,
            enabled:enabled
        })
    })
    .then(r=>r.json())
    .then(data=>{
        if(data.success){
            alert("✅ تم حفظ إعدادات الأمر");
            closeModal();
        }else{
            alert("❌ "+(data.error||"حدث خطأ"));
        }
    })
    .catch(()=>alert("❌ تعذر الاتصال بالموقع."));
}
</script>
</body>
</html>
""".replace("__BASE_STYLE__", BASE_STYLE)

CREATE_CHANNEL_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>إنشاء روم</title>
__BASE_STYLE__
</head>
<body>
<div class="container" style="max-width:550px;margin:80px auto">
<div class="card">
<h1>📁 إنشاء روم</h1>
<form method="POST">
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
<label>اسم الروم</label>
<input name="name" placeholder="مثال: الأوامر" required maxlength="100">

<label>نوع الروم</label>
<select name="type">
<option value="text">روم كتابي</option>
<option value="voice">روم صوتي</option>
</select>

<button class="btn" style="width:100%">✨ إنشاء الروم</button>
</form>
<a style="display:block;text-align:center;margin-top:15px;color:#b58aff" href="/server/{{ guild_id }}">← الرجوع للسيرفر</a>
</div>
</div>
</body>
</html>
""".replace("__BASE_STYLE__", BASE_STYLE)


# =========================================================
# الصفحة الرئيسية
# =========================================================

@app.route("/")
def home():
    return render_template_string(HOME_HTML)


# =========================================================
# تسجيل الدخول
# =========================================================

@app.route("/login")
def login():
    state = secrets.token_urlsafe(32)
    session[OAUTH_STATE_SESSION_KEY] = state
    session.permanent = True

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": "identify guilds",
        "state": state,
    }

    url = (
        "https://discord.com/oauth2/authorize?"
        + urlencode(params)
    )

    return redirect(url)


@app.route("/callback")
def callback():
    code = request.args.get("code")
    returned_state = request.args.get("state", "")
    expected_state = session.pop(OAUTH_STATE_SESSION_KEY, "")

    if not code:
        return "لم يتم استلام كود تسجيل الدخول.", 400

    if not expected_state or not returned_state or not hmac.compare_digest(
        str(expected_state), str(returned_state)
    ):
        return "جلسة تسجيل الدخول غير صالحة. أعد المحاولة.", 400

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
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "DiaaBOT-Website/1.0",
            },
            timeout=10,
        )
    except requests.RequestException:
        return "تعذر الاتصال بـ Discord.", 500

    if response.status_code != 200:
        return "فشل تسجيل الدخول إلى Discord.", 400

    data = response.json()
    access_token = data.get("access_token")

    if not access_token:
        return "لم يرجع Discord رمز دخول صالح.", 400

    session.clear()
    session["access_token"] = access_token
    session.permanent = True

    return redirect(url_for("dashboard"))


# =========================================================
# رابط إضافة البوت
# =========================================================

@app.route("/invite")
def invite():
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "scope": "bot applications.commands",
        "permissions": BOT_INVITE_PERMISSIONS,
    }

    url = (
        "https://discord.com/oauth2/authorize?"
        + urlencode(params)
    )

    return redirect(url)


# =========================================================
# Dashboard
# =========================================================

@app.route("/dashboard")
def dashboard():
    user = get_user()

    if not user:
        return redirect(url_for("login"))

    user_id = str(user.get("id", ""))
    discord_guilds = get_user_guilds()
    result = []

    for discord_guild in discord_guilds:
        guild_id = str(discord_guild.get("id", "")).strip()
        if not guild_id:
            continue

        bot_guild = get_bot_guild(guild_id)
        if not bot_guild:
            continue

        owner = discord_guild.get("owner") is True
        installer_id = ensure_installer_record(guild_id)
        is_installer = bool(installer_id and user_id == str(installer_id))
        is_bot_owner_user = user_id == BOT_OWNER_ID

        if not (owner or is_installer or is_bot_owner_user):
            continue

        guild_name = bot_guild.get("name", discord_guild.get("name", "سيرفر"))
        owner_id = str(bot_guild.get("owner_id", discord_guild.get("owner_id", "")))

        update_data = {
            "guild_id": guild_id,
            "guild_name": guild_name,
            "owner_id": owner_id,
            "updated_at": datetime.now(timezone.utc),
        }

        if installer_id:
            update_data["installer_id"] = str(installer_id)
            update_data["installer_verified"] = True

        guilds_collection.update_one(
            {"guild_id": guild_id},
            {"$set": update_data},
            upsert=True,
        )

        if is_bot_owner_user:
            status = "bot_owner"
        elif owner:
            status = "owner"
        else:
            status = "installer"

        result.append({
            "id": guild_id,
            "name": guild_name,
            "status": status,
        })

    return render_template_string(DASHBOARD_HTML, guilds=result)


# =========================================================
# صفحة السيرفر
# =========================================================

@app.route("/server/<guild_id>")
def server_page(guild_id):

    guild_id = str(guild_id).strip()

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

    economy_room_name = "غير محدد"

    if economy_room_id:
        for channel in discord_channels:
            if str(channel.get("id")) == economy_room_id:
                economy_room_name = channel.get(
                    "name",
                    "الروم"
                )
                break

        if economy_room_name == "غير محدد":
            economy_room_name = economy_room_id

    guilds_collection.update_one(
        {"guild_id": guild_id},
        {
            "$set": {
                "guild_id": guild_id,
                "guild_name": discord_guild.get(
                    "name",
                    "السيرفر"
                ),
                "channels": discord_channels,
                "roles": discord_roles,
                "updated_at": datetime.now(timezone.utc),
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
        channel_count=len(discord_channels),
        role_count=len(discord_roles),
        economy_enabled=economy_enabled,
        economy_room_id=economy_room_id,
        economy_room_name=economy_room_name,
    )


# =========================================================
# تفعيل / تغيير روم الاقتصاد
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
            "error": "guild_id مفقود.",
        }, 400

    if not economy_room_id:
        return {
            "success": False,
            "error": "ID روم الاقتصاد مفقود.",
        }, 400

    if not economy_room_id.isdigit():
        return {
            "success": False,
            "error": "ID روم الاقتصاد غير صحيح.",
        }, 400

    if not user_can_control(guild_id):
        return {
            "success": False,
            "error": "غير مصرح لك.",
        }, 403

    bot_guild = get_bot_guild(guild_id)

    if not bot_guild:
        return {
            "success": False,
            "error": "البوت غير موجود في هذا السيرفر.",
        }, 404

    channels = get_bot_channels(guild_id)

    if not channels:
        return {
            "success": False,
            "error": "تعذر جلب رومات السيرفر من Discord.",
        }, 400

    selected_channel = None

    for channel in channels:

        if str(channel.get("id")) != economy_room_id:
            continue

        channel_type = channel.get("type")

        if channel_type not in (0, 5):
            return {
                "success": False,
                "error": "روم الاقتصاد يجب أن يكون رومًا كتابيًا.",
            }, 400

        selected_channel = channel
        break

    if selected_channel is None:
        return {
            "success": False,
            "error": "روم الاقتصاد غير موجود في هذا السيرفر.",
        }, 400

    economy_room_name = str(
        selected_channel.get(
            "name",
            "روم الاقتصاد"
        )
    )

    economy_settings_collection.update_one(
        {"guild_id": guild_id},
        {
            "$set": {
                "guild_id": guild_id,
                "currency_enabled": True,
                "economy_room_id": economy_room_id,
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
            "error": "تعذر حفظ إعدادات الاقتصاد في قاعدة البيانات.",
        }, 500

    saved_room_id = str(
        saved.get(
            "economy_room_id",
            ""
        )
    ).strip()

    saved_enabled = (
        saved.get(
            "currency_enabled",
            False
        ) is True
    )

    if (
        saved_room_id != economy_room_id
        or not saved_enabled
    ):
        return {
            "success": False,
            "error": "تم إرسال الحفظ لكن لم يتم التحقق من البيانات المحفوظة.",
        }, 500

    return {
        "success": True,
        "guild_id": guild_id,
        "economy_room_id": saved_room_id,
        "economy_room_name": economy_room_name,
        "currency_enabled": True,
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

    guild_id = str(
        data.get(
            "guild_id",
            ""
        )
    ).strip()

    if not guild_id:
        return {
            "success": False,
            "error": "guild_id مفقود.",
        }, 400

    if not user_can_control(guild_id):
        return {
            "success": False,
            "error": "غير مصرح لك.",
        }, 403

    economy_settings_collection.update_one(
        {"guild_id": guild_id},
        {
            "$set": {
                "guild_id": guild_id,
                "currency_enabled": False,
            }
        },
        upsert=True
    )

    return {
        "success": True,
        "guild_id": guild_id,
    }


# =========================================================
# صفحة الأوامر
# =========================================================

@app.route("/commands")
def commands_page():
    guild_id = str(request.args.get("guild", "")).strip()

    if not guild_id:
        return redirect(url_for("dashboard"))

    if not user_can_control(guild_id):
        return redirect(url_for("dashboard"))

    discord_channels = get_bot_channels(guild_id)
    discord_roles = get_bot_roles(guild_id)

    channels = prepare_channels_for_picker(discord_channels)

    roles = []

    for role in discord_roles:
        if not isinstance(role, dict):
            continue

        role_id = str(role.get("id", ""))

        if not role_id:
            continue

        roles.append({
            "id": role_id,
            "name": str(role.get("name", "رتبة")),
            "position": role.get("position", 0),
        })

    roles.sort(
        key=lambda x: x.get("position", 0),
        reverse=True
    )

    raw_commands = list(
        commands_collection.find({})
    )

    grouped = {}

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

        category = get_command_category(command)

        description = str(
            command.get(
                "description",
                "لا يوجد وصف لهذا الأمر."
            )
        )

        grouped.setdefault(
            category,
            []
        ).append({
            "name": command_name,
            "description": description,
            "is_admin_display": is_admin_command(command),
        })

    categories = []

    for title, category_commands in grouped.items():

        category_commands.sort(
            key=lambda x: x.get(
                "name",
                ""
            )
        )

        categories.append({
            "title": title,
            "commands": category_commands,
        })

    categories.sort(
        key=lambda x: COMMAND_CATEGORY_ORDER.get(
            x["title"],
            99
        )
    )

    return render_template_string(
        COMMANDS_HTML,
        guild_id=guild_id,
        categories=categories,
        channels=channels,
        roles=roles,
    )


# =========================================================
# جلب إعداد أمر
# =========================================================

@app.route("/api/command-settings")
def command_settings():

    guild_id = str(
        request.args.get(
            "guild",
            ""
        )
    ).strip()

    command_name = str(
        request.args.get(
            "command",
            ""
        )
    ).strip()

    if not guild_id or not command_name:
        return {
            "success": False,
            "error": "بيانات ناقصة."
        }, 400

    if not user_can_control(guild_id):
        return {
            "success": False,
            "error": "غير مصرح."
        }, 403

    command = commands_collection.find_one({
        "$or": [
            {"name": command_name},
            {"command_name": command_name},
        ]
    })

    if not command:
        return {
            "success": False,
            "error": "الأمر غير موجود."
        }, 404

    setting = settings_collection.find_one({
        "guild_id": guild_id,
        "command_name": command_name,
    })

    if not setting:
        return {
            "success": True,
            "channel_ids": [],
            "role_ids": [],
            "all_channels": False,
            "enabled": False,
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
        "all_channels": bool(
            setting.get(
                "all_channels",
                False
            )
        ),
        "enabled": bool(
            setting.get(
                "enabled",
                False
            )
        ),
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

    all_channels = bool(
        data.get(
            "all_channels",
            False
        )
    )

    channel_ids = list(
        dict.fromkeys(
            str(x).strip()
            for x in data.get(
                "channel_ids",
                []
            )
            if str(x).strip()
        )
    )

    role_ids = list(
        dict.fromkeys(
            str(x).strip()
            for x in data.get(
                "role_ids",
                []
            )
            if str(x).strip()
        )
    )

    if not guild_id or not command_name:
        return {
            "success": False,
            "error": "بيانات ناقصة."
        }, 400

    if not user_can_control(guild_id):
        return {
            "success": False,
            "error": "غير مصرح لك."
        }, 403

    command = commands_collection.find_one({
        "$or": [
            {"name": command_name},
            {"command_name": command_name},
        ]
    })

    if not command:
        return {
            "success": False,
            "error": "الأمر غير موجود."
        }, 404

    if all_channels:
        channel_ids = []

    else:

        ok, error = validate_guild_resources(
            guild_id,
            channel_ids=channel_ids,
            role_ids=role_ids,
        )

        if not ok:
            return {
                "success": False,
                "error": error
            }, 400

    settings_collection.update_one(
        {
            "guild_id": guild_id,
            "command_name": command_name,
        },
        {
            "$set": {
                "guild_id": guild_id,
                "command_name": command_name,
                "channel_ids": channel_ids,
                "role_ids": role_ids,
                "all_channels": all_channels,
                "enabled": bool(
                    data.get(
                        "enabled",
                        False
                    )
                ),
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )

    return {
        "success": True,
        "guild_id": guild_id,
        "command_name": command_name,
        "all_channels": all_channels,
    }


# =========================================================
# اختصارات الأوامر
# =========================================================

def normalize_alias(value):
    value = str(
        value or ""
    ).strip()

    value = value.lstrip(
        "-./"
    )

    return value[:50].strip()


def command_exists(command_name):
    return commands_collection.find_one({
        "$or": [
            {"name": command_name},
            {"command_name": command_name},
        ]
    }) is not None


@app.route("/api/command-aliases")
def get_command_aliases():

    guild_id = str(
        request.args.get(
            "guild",
            ""
        )
    ).strip()

    command_name = str(
        request.args.get(
            "command",
            ""
        )
    ).strip()

    if not guild_id or not command_name:
        return {
            "success": False,
            "error": "بيانات ناقصة."
        }, 400

    if not user_can_control(guild_id):
        return {
            "success": False,
            "error": "غير مصرح."
        }, 403

    if not command_exists(command_name):
        return {
            "success": False,
            "error": "الأمر غير موجود."
        }, 404

    aliases = list(
        aliases_collection.find(
            {
                "guild_id": guild_id,
                "command_name": command_name,
                "enabled": True,
            },
            {
                "_id": 0,
                "alias": 1
            }
        ).sort(
            "alias",
            1
        )
    )

    return {
        "success": True,
        "aliases": [
            str(
                x.get(
                    "alias",
                    ""
                )
            )
            for x in aliases
            if x.get("alias")
        ],
    }


@app.route(
    "/api/command-aliases",
    methods=["POST"]
)
def save_command_alias():

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

    alias = normalize_alias(
        data.get(
            "alias",
            ""
        )
    )

    action = str(
        data.get(
            "action",
            "add"
        )
    ).strip().lower()

    if not guild_id or not command_name or not alias:
        return {
            "success": False,
            "error": "بيانات ناقصة."
        }, 400

    if not user_can_control(guild_id):
        return {
            "success": False,
            "error": "غير مصرح لك."
        }, 403

    if not command_exists(command_name):
        return {
            "success": False,
            "error": "الأمر غير موجود."
        }, 404

    if len(alias) < 1:
        return {
            "success": False,
            "error": "الاختصار فارغ."
        }, 400

    if " " in alias:
        return {
            "success": False,
            "error": "الاختصار يجب أن يكون كلمة واحدة بدون مسافات."
        }, 400

    if command_name == alias:
        return {
            "success": False,
            "error": "لا يمكنك جعل اسم الأمر نفسه اختصاراً له."
        }, 400

    if command_exists(alias):
        return {
            "success": False,
            "error": "هذا الاسم مستخدم بالفعل كأمر في البوت."
        }, 409

    if action == "delete":

        aliases_collection.delete_one({
            "guild_id": guild_id,
            "command_name": command_name,
            "alias": alias,
        })

        return {
            "success": True,
            "deleted": alias
        }

    existing = aliases_collection.find_one({
        "guild_id": guild_id,
        "alias": alias,
    })

    if existing and str(
        existing.get(
            "command_name",
            ""
        )
    ) != command_name:

        return {
            "success": False,
            "error": "هذا الاختصار مستخدم لأمر آخر في هذا السيرفر."
        }, 409

    aliases_collection.update_one(
        {
            "guild_id": guild_id,
            "alias": alias
        },
        {
            "$set": {
                "guild_id": guild_id,
                "alias": alias,
                "command_name": command_name,
                "enabled": True,
                "updated_at": datetime.now(timezone.utc),
            },
            "$setOnInsert": {
                "created_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )

    return {
        "success": True,
        "alias": alias,
        "command_name": command_name
    }


# =========================================================
# إنشاء روم
# =========================================================

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

    name = request.form.get(
        "name",
        ""
    ).strip()

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
        "type": 0 if channel_type == "text" else 2,
    }

    response = discord_request(
        "POST",
        f"{DISCORD_API}/guilds/{guild_id}/channels",
        json=payload,
    )

    if response is None:
        return (
            "❌ تعذر الاتصال بـ Discord.",
            500
        )

    if response.status_code not in (200, 201):
        return (
            "❌ فشل إنشاء الروم.<br><br>"
            "تأكد أن البوت يملك Manage Channels في السيرفر.",
            403
        )

    cache_delete_prefix(
        f"bot_channels:{guild_id}"
    )

    return redirect(
        url_for(
            "server_page",
            guild_id=guild_id
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
        port=port,
        debug=False,
    )
