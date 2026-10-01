import os
import time
import secrets
from urllib.parse import urlencode
from datetime import datetime

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
# إعدادات الموقع
# =========================================================

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

FORCED_ECONOMY_GUILD_ID = "1544077828151054537"

DISCORD_API = "https://discord.com/api/v10"

PORT = int(os.getenv("PORT", "8080"))


if not CLIENT_ID:
    raise RuntimeError("DISCORD_CLIENT_ID غير موجود")

if not CLIENT_SECRET:
    raise RuntimeError("DISCORD_CLIENT_SECRET غير موجود")

if not BOT_TOKEN:
    raise RuntimeError("TOKEN غير موجود")

if not MONGO_URI:
    raise RuntimeError("MONGO_URI غير موجود")


# =========================================================
# Flask
# =========================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    secrets.token_hex(32)
)

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# =========================================================
# MongoDB
# =========================================================

mongo = MongoClient(MONGO_URI)

db = mongo["discord_bot_db"]

commands_collection = db["website_commands"]
guilds_collection = db["website_guilds"]
settings_collection = db["website_command_settings"]
economy_settings_collection = db["economy_settings"]
aliases_collection = db["website_command_aliases"]

welcome_settings_collection = db["welcome_settings"]


# =========================================================
# Discord API
# =========================================================

def discord_request(
    method,
    endpoint,
    token=None,
    json=None,
    params=None
):
    token = token or BOT_TOKEN

    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }

    url = (
        endpoint
        if endpoint.startswith("http")
        else f"{DISCORD_API}{endpoint}"
    )

    try:
        response = requests.request(
            method,
            url,
            headers=headers,
            json=json,
            params=params,
            timeout=15,
        )

        if response.status_code == 429:
            try:
                data = response.json()
                retry_after = float(
                    data.get("retry_after", 1)
                )
            except Exception:
                retry_after = 1

            time.sleep(min(retry_after, 5))

            response = requests.request(
                method,
                url,
                headers=headers,
                json=json,
                params=params,
                timeout=15,
            )

        return response

    except requests.RequestException:
        return None


# =========================================================
# Helpers
# =========================================================

def clean_id(value):
    return str(value).strip()


def is_forced_economy_guild(guild_id):
    return (
        clean_id(guild_id)
        == FORCED_ECONOMY_GUILD_ID
    )


def guild_id_variants(guild_id):
    gid = clean_id(guild_id)

    variants = [gid]

    try:
        variants.append(int(gid))
    except Exception:
        pass

    return variants


def get_user():
    access_token = session.get("access_token")

    if not access_token:
        return None

    try:
        response = requests.get(
            f"{DISCORD_API}/users/@me",
            headers={
                "Authorization":
                    f"Bearer {access_token}"
            },
            timeout=15,
        )
    except requests.RequestException:
        return None

    if response.status_code != 200:
        return None

    try:
        return response.json()
    except Exception:
        return None


def is_bot_owner(user_id):
    return clean_id(user_id) == BOT_OWNER_ID


def get_user_guilds():
    access_token = session.get("access_token")

    if not access_token:
        return []

    try:
        response = requests.get(
            f"{DISCORD_API}/users/@me/guilds",
            headers={
                "Authorization":
                    f"Bearer {access_token}"
            },
            timeout=15,
        )
    except requests.RequestException:
        return []

    if response.status_code != 200:
        return []

    try:
        return response.json()
    except Exception:
        return []


def get_bot_guild(guild_id):
    response = discord_request(
        "GET",
        f"/guilds/{clean_id(guild_id)}"
    )

    if not response or response.status_code != 200:
        return None

    try:
        return response.json()
    except Exception:
        return None


def bot_in_guild(guild_id):
    return get_bot_guild(guild_id) is not None


def get_bot_channels(guild_id):
    response = discord_request(
        "GET",
        f"/guilds/{clean_id(guild_id)}/channels"
    )

    if not response or response.status_code != 200:
        return []

    try:
        channels = response.json()
    except Exception:
        return []

    return sorted(
        channels,
        key=lambda x: (
            x.get("position", 0),
            x.get("name", "").lower()
        )
    )


def get_bot_roles(guild_id):
    response = discord_request(
        "GET",
        f"/guilds/{clean_id(guild_id)}/roles"
    )

    if not response or response.status_code != 200:
        return []

    try:
        return response.json()
    except Exception:
        return []


def normalize_channel_type(channel):
    channel_type = channel.get("type")

    if channel_type == 0:
        return "text"

    if channel_type == 2:
        return "voice"

    if channel_type == 4:
        return "category"

    if channel_type == 5:
        return "announcement"

    if channel_type == 13:
        return "stage"

    if channel_type == 15:
        return "forum"

    return "unknown"


def prepare_channels_for_picker(channels):
    result = []

    allowed = {
        0,
        5,
        15,
    }

    for channel in channels:
        if channel.get("type") not in allowed:
            continue

        result.append({
            "id": str(channel.get("id")),
            "name": channel.get(
                "name",
                "بدون اسم"
            ),
            "type": normalize_channel_type(channel),
            "position": channel.get(
                "position",
                0
            ),
        })

    return result


def prepare_roles_for_picker(roles):
    result = []

    for role in roles:
        role_id = str(
            role.get("id", "")
        )

        if role_id == "" or role.get("name") == "@everyone" or role.get("managed"):
            continue

        color_value = role.get("color", 0)

        try:
            color_value = int(color_value)
        except Exception:
            color_value = 0

        color = "#{:06x}".format(color_value) if color_value else "#ffffff"

        result.append({
            "id": role_id,
            "name": role.get("name", "بدون اسم"),
            "color": color,
            "position": role.get("position", 0),
        })

    result.sort(
        key=lambda x: x.get("position", 0),
        reverse=True
    )

    return result


def get_economy_settings(guild_id):
    variants = guild_id_variants(guild_id)
    return economy_settings_collection.find_one({
        "guild_id": {
            "$in": variants
        }
    })


def choose_default_economy_room(channels):
    usable = [
        c for c in channels
        if c.get("type") in (0, 5)
    ]

    if not usable:
        return None

    preferred_names = [
        "الاقتصاد",
        "اقتصاد",
        "economy",
        "ai",
        "currency",
        "general",
        "عام",
    ]

    for preferred in preferred_names:
        for channel in usable:
            if (
                channel.get("name", "").lower()
                == preferred.lower()
            ):
                return str(channel["id"])

    return str(usable[0]["id"])


def ensure_forced_economy(
    guild_id,
    channels=None
):
    guild_id = clean_id(guild_id)

    if not is_forced_economy_guild(
        guild_id
    ):
        return get_economy_settings(
            guild_id
        )

    document = get_economy_settings(
        guild_id
    )

    if document:
        room_id = document.get("economy_room_id")
        update_data = {
            "guild_id": guild_id,
            "currency_enabled": True,
            "updated_at": datetime.utcnow(),
        }

        if room_id:
            update_data["economy_room_id"] = str(room_id)

        economy_settings_collection.update_one(
            {"_id": document["_id"]},
            {
                "$set": update_data
            }
        )

        return economy_settings_collection.find_one(
            {"_id": document["_id"]}
        )

    if channels is None:
        channels = get_bot_channels(guild_id)

    room_id = choose_default_economy_room(channels)

    data = {
        "guild_id": guild_id,
        "currency_enabled": True,
        "economy_room_id": room_id,
        "updated_at": datetime.utcnow(),
    }

    economy_settings_collection.update_one(
        {
            "guild_id": {
                "$in": guild_id_variants(guild_id)
            }
        },
        {
            "$set": data
        },
        upsert=True,
    )

    return get_economy_settings(guild_id)


def user_can_control(guild_id):
    user = get_user()

    if not user:
        return False

    user_id = clean_id(user.get("id"))

    if is_bot_owner(user_id):
        return True

    guilds = get_user_guilds()
    target = None

    for guild in guilds:
        if (
            clean_id(guild.get("id"))
            == clean_id(guild_id)
        ):
            target = guild
            break

    if not target:
        return False

    try:
        permissions = int(
            target.get("permissions", 0)
        )
    except Exception:
        permissions = 0

    ADMINISTRATOR = 0x8
    MANAGE_GUILD = 0x20

    if permissions & ADMINISTRATOR or permissions & MANAGE_GUILD:
        return True

    installer = guilds_collection.find_one({
        "guild_id": clean_id(guild_id),
        "installer_id": user_id,
    })

    return installer is not None


def get_command_setting(
    guild_id,
    command_name
):
    return (
        settings_collection.find_one({
            "guild_id": clean_id(guild_id),
            "command_name": command_name,
        })
        or
        settings_collection.find_one({
            "guild_id": clean_id(guild_id),
            "name": command_name,
        })
    )


def get_welcome_settings(guild_id):
    return welcome_settings_collection.find_one({
        "guild_id": clean_id(guild_id)
    })


# =========================================================
# HTML / CSS
# =========================================================

BASE_STYLE = """
<style>
* { box-sizing: border-box; }
html { width: 100%; overflow-x: hidden; }
body {
    margin: 0; min-height: 100vh; width: 100%;
    background: radial-gradient(circle at 8% 5%, rgba(37,99,235,.24), transparent 30%),
                radial-gradient(circle at 92% 10%, rgba(250,204,21,.14), transparent 27%),
                radial-gradient(circle at 50% 100%, rgba(255,255,255,.055), transparent 30%), #030812;
    color: #f8fafc; font-family: Arial, Tahoma, sans-serif; direction: rtl; overflow-x: hidden;
}
a { color: inherit; text-decoration: none; }
button, input, select, textarea { font: inherit; }
.topbar {
    position: sticky; top: 0; z-index: 100; width: 100%;
    backdrop-filter: blur(22px); background: rgba(3,8,18,.82);
    border-bottom: 1px solid rgba(255,255,255,.08);
    box-shadow: 0 8px 35px rgba(0,0,0,.15);
}
.topbar-inner {
    width: 100%; max-width: 1250px; margin: auto; padding: 14px 20px;
    display: flex; justify-content: space-between; align-items: center; gap: 15px;
}
.brand {
    display: flex; align-items: center; gap: 12px; font-size: 21px; font-weight: 1000;
}
.brand-icon {
    width: 44px; height: 44px; min-width: 44px; border-radius: 14px;
    background: linear-gradient(135deg, #2563eb 0%, #38bdf8 42%, #facc15 100%);
    display: grid; place-items: center; color: #020617; font-weight: 1000;
}
.container { width: 100%; max-width: 1250px; margin: auto; padding: 42px 20px 80px; }
.hero { margin-bottom: 28px; }
.hero h1 { font-size: clamp(30px, 5vw, 48px); margin: 0 0 10px; font-weight: 1000; }
.gradient-text {
    background: linear-gradient(135deg, #ffffff 0%, #60a5fa 30%, #38bdf8 55%, #facc15 100%);
    -webkit-background-clip: text; background-clip: text; color: transparent;
}
.hero p { color: #94a3b8; font-size: 16px; line-height: 1.8; }
.card {
    width: 100%; background: linear-gradient(145deg, rgba(12,28,50,.92), rgba(3,10,21,.93));
    border: 1px solid rgba(255,255,255,.09); border-radius: 24px; padding: 24px;
    margin-bottom: 20px; box-shadow: 0 20px 70px rgba(0,0,0,.27); overflow: hidden;
}
.card-title {
    display: flex; justify-content: space-between; align-items: center; gap: 15px; flex-wrap: wrap; margin-bottom: 20px;
}
.card-title h2, .card-title h3 { margin: 0; }
.grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(min(270px, 100%), 1fr)); gap: 18px;
}
.server-card {
    background: linear-gradient(145deg, rgba(12,30,52,.86), rgba(5,15,29,.9));
    border: 1px solid rgba(255,255,255,.09); border-radius: 22px; padding: 22px; transition: .2s;
}
.server-card:hover {
    transform: translateY(-3px); border-color: rgba(56,189,248,.4);
    box-shadow: 0 18px 45px rgba(0,0,0,.22);
}
.server-name { font-size: 19px; font-weight: 900; margin-bottom: 7px; line-height: 1.5; }
.server-id { font-size: 12px; color: #64748b; direction: ltr; text-align: right; }
.btn {
    border: 0; border-radius: 14px; padding: 12px 17px; cursor: pointer; color: #fff;
    background: linear-gradient(135deg, #2563eb, #38bdf8); font-weight: 900; transition: .2s;
    display: inline-flex; justify-content: center; align-items: center; gap: 8px; text-align: center;
}
.btn:hover { transform: translateY(-2px); box-shadow: 0 10px 30px rgba(37,99,235,.25); }
.btn-yellow { color: #111827; background: linear-gradient(135deg, #facc15, #f59e0b); }
.btn-secondary { background: #101d31; border: 1px solid rgba(255,255,255,.08); }
.btn-danger { background: linear-gradient(135deg, #991b1b, #dc2626); }
.status {
    display: inline-flex; align-items: center; gap: 8px; padding: 7px 12px;
    border-radius: 999px; font-size: 12px; font-weight: 900; border: 1px solid transparent;
}
.status-yellow { background: rgba(250,204,21,.12); color: #fde047; border-color: rgba(250,204,21,.28); }
.status-blue { background: rgba(37,99,235,.13); color: #60a5fa; border-color: rgba(37,99,235,.3); }
.status-white { background: rgba(255,255,255,.07); color: #ffffff; border-color: rgba(255,255,255,.16); }
.form-group { margin-bottom: 18px; }
.form-label { display: block; margin-bottom: 9px; font-size: 13px; font-weight: 900; color: #cbd5e1; }
.input, .textarea {
    width: 100%; padding: 14px 15px; border-radius: 15px;
    border: 1px solid rgba(255,255,255,.09); background: #050f1e; color: white; outline: none;
}
.textarea { min-height: 125px; resize: vertical; line-height: 1.8; }
.select-box {
    width: 100%; min-height: 55px; border-radius: 16px; background: #050f1e;
    border: 1px solid rgba(255,255,255,.09); color: white; padding: 14px 15px; outline: none;
}
.command-card {
    border: 1px solid rgba(255,255,255,.09); background: linear-gradient(145deg, rgba(7,22,40,.9), rgba(3,11,22,.92));
    border-radius: 22px; padding: 18px; margin-bottom: 14px;
}
.command-head { display: flex; justify-content: space-between; align-items: center; gap: 15px; margin-bottom: 17px; flex-wrap: wrap; }
.command-name { font-size: 17px; font-weight: 1000; }
.badge { display: inline-flex; align-items: center; border-radius: 999px; padding: 5px 9px; font-size: 10px; font-weight: 900; }
.badge-admin { color: #fde047; background: rgba(250,204,21,.1); border: 1px solid rgba(250,204,21,.2); }
.badge-normal { color: #93c5fd; background: rgba(37,99,235,.1); border: 1px solid rgba(37,99,235,.2); }
.command-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; width: 100%; }
.picker { width: 100%; border: 1px solid rgba(255,255,255,.09); border-radius: 18px; background: rgba(3,12,24,.75); overflow: hidden; }
.picker-top { padding: 12px; border-bottom: 1px solid rgba(255,255,255,.09); }
.picker-search { width: 100%; padding: 11px 13px; border-radius: 12px; border: 1px solid rgba(255,255,255,.09); background: #081a2f; color: white; outline: none; }
.picker-actions { display: flex; gap: 7px; margin-top: 8px; }
.mini-btn { flex: 1; padding: 8px; border: 1px solid rgba(255,255,255,.09); border-radius: 10px; background: #102640; color: #cbd5e1; cursor: pointer; font-size: 11px; font-weight: 900; }
.options { max-height: 280px; overflow-y: auto; padding: 8px; }
.option { display: grid; grid-template-columns: 20px minmax(0, 1fr); align-items: center; gap: 10px; padding: 10px; border-radius: 12px; cursor: pointer; }
.option:hover { background: rgba(56,189,248,.07); }
.option input { accent-color: #38bdf8; width: 17px; height: 17px; margin: 0; }
.option span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; line-height: 1.5; }
.selected-count { font-size: 11px; color: #94a3b8; margin-top: 8px; }

/* أزرار الأقسام الجديدة */
.sections-tabs { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 20px; }
.section-tab-btn {
    background: #0c1c32; border: 1px solid rgba(255,255,255,.09); color: #cbd5e1;
    padding: 10px 18px; border-radius: 14px; font-weight: 900; cursor: pointer; transition: .2s;
}
.section-tab-btn.active, .section-tab-btn:hover {
    background: linear-gradient(135deg, #2563eb, #38bdf8); color: #fff; border-color: transparent;
}

/* زر جميع الرومات */
.all-channels-toggle-box {
    margin-bottom: 12px; padding: 10px 12px; background: rgba(56,189,248,.06);
    border: 1px solid rgba(56,189,248,.2); border-radius: 12px; display: flex; align-items: center; justify-content: space-between;
}

.roles-picker { width: 100%; border: 1px solid rgba(255,255,255,.09); border-radius: 18px; background: rgba(3,12,24,.75); overflow: hidden; }
.roles-toolbar { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 9px; }
.roles-list { max-height: 310px; overflow-y: auto; padding: 9px; }
.role-card {
    position: relative; display: flex; align-items: center; gap: 10px; width: 100%;
    padding: 11px; margin-bottom: 7px; border-radius: 14px; border: 1px solid rgba(255,255,255,.055);
    background: rgba(255,255,255,.025); cursor: pointer; transition: .15s;
}
.role-card:hover { background: rgba(37,99,235,.08); border-color: rgba(56,189,248,.2); }
.role-card.selected {
    background: linear-gradient(90deg, rgba(37,99,235,.14), rgba(250,204,21,.07));
    border-color: rgba(56,189,248,.35); box-shadow: inset 3px 0 0 #38bdf8;
}
.role-check { width: 18px !important; height: 18px !important; accent-color: #38bdf8; }
.role-color { width: 13px; height: 32px; border-radius: 7px; }
.role-info { min-width: 0; flex: 1; }
.role-name { font-weight: 900; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.role-id { font-size: 9px; color: #64748b; direction: ltr; text-align: right; }
.role-counter { color: #94a3b8; font-size: 11px; margin-top: 7px; }

.switch { position: relative; width: 52px; height: 29px; flex-shrink: 0; }
.switch input { display: none; }
.slider { position: absolute; inset: 0; cursor: pointer; border-radius: 999px; background: #24364d; transition: .2s; }
.slider:before { content: ""; position: absolute; width: 21px; height: 21px; left: 4px; top: 4px; border-radius: 50%; background: white; transition: .2s; }
.switch input:checked + .slider { background: linear-gradient(135deg, #2563eb, #facc15); }
.switch input:checked + .slider:before { transform: translateX(23px); }

.welcome-preview {
    border: 1px solid rgba(255,255,255,.08); background: linear-gradient(135deg, rgba(37,99,235,.08), rgba(250,204,21,.05));
    border-radius: 17px; padding: 15px; margin-top: 15px; line-height: 1.9; color: #cbd5e1;
}
.placeholder { display: inline-block; padding: 2px 7px; margin: 2px 3px; border-radius: 7px; background: rgba(56,189,248,.1); color: #7dd3fc; font-family: monospace; }
.notice { padding: 15px; border-radius: 16px; border: 1px solid rgba(56,189,248,.15); background: linear-gradient(135deg, rgba(37,99,235,.08), rgba(255,255,255,.025)); color: #bae6fd; line-height: 1.8; }
.small { font-size: 12px; color: #94a3b8; line-height: 1.6; }
.empty { padding: 35px; text-align: center; color: #94a3b8; }
.footer { text-align: center; color: #475569; font-size: 12px; margin-top: 35px; }

@media(max-width:950px) { .command-grid { grid-template-columns: 1fr; } }
</style>
"""


# =========================================================
# الصفحة الرئيسية
# =========================================================

@app.route("/")
def home():
    user = get_user()
    if user:
        return redirect(url_for("dashboard"))

    return render_template_string(
        BASE_STYLE + """
        <div class="container">
            <div class="hero" style="text-align:center; padding-top:70px">
                <div class="brand" style="justify-content:center; margin-bottom:25px">
                    <div class="brand-icon">ض</div>
                    <span>ضياء BOT</span>
                </div>
                <h1>لوحة تحكم <span class="gradient-text">ضياء</span></h1>
                <p>إدارة السيرفر، الأوامر، الرتب، الرومات، الترحيب واختصارات الأوامر من مكان واحد.</p>
                <div style="display: flex; justify-content: center; gap: 10px; flex-wrap: wrap; margin-top: 15px;">
                    <a class="btn btn-yellow" href="{{ url_for('login') }}">تسجيل الدخول عبر Discord</a>
                    <a class="btn" href="https://discord.com/oauth2/authorize?client_id=1545572840437186590&permissions=8&integration_type=0&scope=bot" target="_blank">➕ إضافة البوت للسيرفر</a>
                </div>
            </div>
        </div>
        """
    )


# =========================================================
# تسجيل الدخول
# =========================================================

@app.route("/login")
def login():
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds",
    }
    return redirect("https://discord.com/oauth2/authorize?" + urlencode(params))


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return redirect(url_for("home"))

    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }

    try:
        response = requests.post(f"{DISCORD_API}/oauth2/token", data=data, timeout=15)
    except requests.RequestException:
        return "فشل الاتصال بديسكورد", 500

    if response.status_code != 200:
        return "فشل تسجيل الدخول", 400

    token_data = response.json()
    session["access_token"] = token_data.get("access_token")
    return redirect(url_for("dashboard"))


# =========================================================
# Dashboard
# =========================================================

@app.route("/dashboard")
def dashboard():
    user = get_user()
    if not user:
        return redirect(url_for("home"))

    user_id = clean_id(user.get("id"))
    guilds = get_user_guilds()
    visible_guilds = []

    for guild in guilds:
        guild_id = clean_id(guild.get("id"))
        if not bot_in_guild(guild_id):
            continue

        if not user_can_control(guild_id):
            continue

        guilds_collection.update_one(
            {"guild_id": guild_id},
            {
                "$set": {
                    "guild_id": guild_id,
                    "name": guild.get("name"),
                    "icon": guild.get("icon"),
                    "installer_id": user.get("id"),
                    "updated_at": datetime.utcnow(),
                }
            },
            upsert=True,
        )
        visible_guilds.append(guild)

    # إذا كان المستخدم هو مالك البوت ولا توجد سيرفرات ظاهرة لديه، اعرض له كل سيرفرات البوت المتاحة
    if is_bot_owner(user_id) and not visible_guilds:
        # جلب السيرفرات التي يظهر فيها البوت عبر الـ API المباشر
        res = discord_request("GET", "/users/@me/guilds")
        if res and res.status_code == 200:
            for g in res.json():
                visible_guilds.append(g)

    return render_template_string(
        BASE_STYLE + """
        <div class="topbar">
            <div class="topbar-inner">
                <div class="brand">
                    <div class="brand-icon">ض</div>
                    ضياء BOT
                </div>
                <a class="btn btn-secondary" href="{{ url_for('logout') }}">تسجيل الخروج</a>
            </div>
        </div>
        <div class="container">
            <div class="hero">
                <h1>أهلاً بك، <span class="gradient-text">{{ user.get("username", "المستخدم") }}</span></h1>
                <p>اختر السيرفر الذي تريد إدارته.</p>
            </div>
            {% if visible_guilds %}
            <div class="grid">
                {% for guild in visible_guilds %}
                <div class="server-card">
                    <div class="server-name">{{ guild.get("name", "بدون اسم") }}</div>
                    <div class="server-id">{{ guild.get("id") }}</div>
                    <a class="btn" style="width:100%; margin-top:18px" href="{{ url_for('server_page', guild_id=guild.get('id')) }}">إدارة السيرفر</a>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <div class="card empty">لا يوجد سيرفر متاح للإدارة حالياً.</div>
            {% endif %}
        </div>
        """,
        user=user,
        visible_guilds=visible_guilds,
    )


# =========================================================
# Server Page
# =========================================================

@app.route("/server/<guild_id>")
def server_page(guild_id):
    guild_id = clean_id(guild_id)
    if not user_can_control(guild_id):
        return "ليس لديك صلاحية إدارة هذا السيرفر", 403

    guild = get_bot_guild(guild_id)
    if not guild:
        return "البوت غير موجود في هذا السيرفر", 404

    raw_channels = get_bot_channels(guild_id)
    channels = prepare_channels_for_picker(raw_channels)
    roles = prepare_roles_for_picker(get_bot_roles(guild_id))

    if is_forced_economy_guild(guild_id):
        economy = ensure_forced_economy(guild_id, raw_channels)
    else:
        economy = get_economy_settings(guild_id)

    economy_enabled = bool(economy and economy.get("currency_enabled") is True)
    economy_room_id = str(economy.get("economy_room_id")) if economy and economy.get("economy_room_id") else ""
    economy_room_name = "غير محدد"

    for channel in channels:
        if str(channel["id"]) == economy_room_id:
            economy_room_name = channel["name"]
            break

    welcome = get_welcome_settings(guild_id)
    welcome_enabled = bool(welcome and welcome.get("enabled") is True)
    welcome_channel_id = str(welcome.get("channel_id")) if welcome and welcome.get("channel_id") else ""
    welcome_message = welcome.get("message", "مرحباً {user} 👋\nنورت سيرفر {server}!") if welcome else "مرحباً {user} 👋\nنورت سيرفر {server}!"

    if welcome_enabled and welcome_channel_id:
        welcome_status, welcome_status_text = "yellow", "● مفعّل"
    elif welcome:
        welcome_status, welcome_status_text = "blue", "● مضبوط لكنه متوقف"
    else:
        welcome_status, welcome_status_text = "white", "● غير مضبوط"

    return render_template_string(
        BASE_STYLE + """
        <div class="topbar">
            <div class="topbar-inner">
                <div class="brand">
                    <div class="brand-icon">ض</div>
                    {{ guild.get("name", "السيرفر") }}
                </div>
                <a class="btn btn-secondary" href="{{ url_for('dashboard') }}">← رجوع</a>
            </div>
        </div>
        <div class="container">
            <div class="hero">
                <h1>إدارة <span class="gradient-text">السيرفر</span></h1>
                <p>تحكم بالاقتصاد والأوامر والترحيب والاختصارات من مكان واحد.</p>
            </div>

            <!-- الاقتصاد -->
            <div class="card">
                <div class="card-title">
                    <div>
                        <h2>💰 الاقتصاد</h2>
                        {% if forced %}<div class="small" style="margin-top:7px">الاقتصاد إجباري في هذا السيرفر</div>{% endif %}
                    </div>
                    {% if forced %}<span class="status status-blue">🔒 إجباري</span>
                    {% elif economy_enabled %}<span class="status status-yellow">● مفعّل</span>
                    {% else %}<span class="status status-white">● غير مفعّل</span>{% endif %}
                </div>
                {% if forced %}
                <div class="notice">هذا السيرفر محدد من المطور ليععمل فيه نظام الاقتصاد بشكل إجباري. لا يمكن تعطيل الاقتصاد من الموقع.</div>
                {% endif %}
                <div style="margin-top:20px">
                    <div class="form-group">
                        <label class="form-label">روم الاقتصاد</label>
                        <select id="economyRoom" class="select-box">
                            <option value="">اختر روم الاقتصاد</option>
                            {% for channel in channels %}
                            {% if channel.type in ["text", "announcement"] %}
                            <option value="{{ channel.id }}" {% if channel.id == economy_room_id %}selected{% endif %}># {{ channel.name }}</option>
                            {% endif %}
                            {% endfor %}
                        </select>
                    </div>
                    <button class="btn btn-yellow" onclick="saveEconomy()">حفظ روم الاقتصاد</button>
                    {% if not forced and economy_enabled %}
                    <button class="btn btn-danger" style="margin-right:8px" onclick="disableEconomy()">تعطيل الاقتصاد</button>
                    {% endif %}
                    <div class="small" style="margin-top:12px">الروم الحالي: <b>{{ economy_room_name }}</b></div>
                </div>
            </div>

            <!-- نظام الترحيب -->
            <div class="card">
                <div class="card-title">
                    <div>
                        <h2>👋 نظام الترحيب</h2>
                        <div class="small" style="margin-top:7px">رسالة تلقائية عند دخول عضو جديد.</div>
                    </div>
                    <span class="status status-{{ welcome_status }}">{{ welcome_status_text }}</span>
                </div>
                <div class="form-group">
                    <label class="form-label">🏠 روم الترحيب</label>
                    <select id="welcomeChannel" class="select-box">
                        <option value="">بدون تحديد</option>
                        {% for channel in channels %}
                        {% if channel.type in ["text", "announcement", "forum"] %}
                        <option value="{{ channel.id }}" {% if channel.id == welcome_channel_id %}selected{% endif %}># {{ channel.name }}</option>
                        {% endif %}
                        {% endfor %}
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">💬 رسالة الترحيب</label>
                    <textarea id="welcomeMessage" class="textarea" maxlength="2000" placeholder="اكتب رسالة الترحيب هنا...">{{ welcome_message }}</textarea>
                </div>
                <div style="display:flex; align-items:center; justify-content:space-between; gap:15px; padding:14px; border-radius:15px; background:rgba(255,255,255,.025); border:1px solid rgba(255,255,255,.07);">
                    <div>
                        <div style="font-weight:900; margin-bottom:4px">تشغيل الترحيب</div>
                        <div class="small">عند التفعيل سيرسل البوت الرسالة تلقائياً عند دخول عضو جديد.</div>
                    </div>
                    <label class="switch">
                        <input type="checkbox" id="welcomeEnabled" {% if welcome_enabled %}checked{% endif %}>
                        <span class="slider"></span>
                    </label>
                </div>
                <div style="display:flex; gap:9px; flex-wrap:wrap; margin-top:17px">
                    <button class="btn btn-yellow" onclick="saveWelcome()">💾 حفظ نظام الترحيب</button>
                    <button class="btn btn-secondary" onclick="disableWelcome()">إيقاف الترحيب</button>
                </div>
            </div>

            <!-- إدارة الأوامر -->
            <div class="card">
                <div class="card-title">
                    <div>
                        <h2>⚙️ إدارة الأوامر</h2>
                        <div class="small" style="margin-top:7px">الرتب والرومات والتحكم في كل أمر حسب الأقسام.</div>
                    </div>
                </div>
                <a class="btn" href="{{ url_for('commands_page', guild_id=guild_id) }}">فتح لوحة إدارة الأوامر مقسمة</a>
            </div>

            <!-- اختصارات -->
            <div class="card">
                <div class="card-title">
                    <div>
                        <h2>⚡ اختصارات الأوامر</h2>
                        <div class="small" style="margin-top:7px">مثال: -ذهبي ← -ذ</div>
                    </div>
                </div>
                <a class="btn btn-yellow" href="{{ url_for('aliases_page', guild_id=guild_id) }}">إدارة الاختصارات</a>
            </div>

            <div class="footer">ضياء BOT • لوحة الإدارة</div>
        </div>

        <script>
        async function saveEconomy() {
            const room = document.getElementById("economyRoom").value;
            if (!room) { alert("اختر روم الاقتصاد أولاً"); return; }
            const response = await fetch("{{ url_for('enable_economy') }}", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ guild_id: "{{ guild_id }}", room_id: room })
            });
            const data = await response.json();
            alert(data.message || "تم الحفظ");
            if (data.success) { location.reload(); }
        }

        async function disableEconomy() {
            if (!confirm("هل تريد تعطيل الاقتصاد؟")) { return; }
            const response = await fetch("{{ url_for('disable_economy') }}", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ guild_id: "{{ guild_id }}" })
            });
            const data = await response.json();
            alert(data.message || "تم التنفيذ");
            if (data.success) { location.reload(); }
        }

        async function saveWelcome() {
            const channel = document.getElementById("welcomeChannel").value;
            const message = document.getElementById("welcomeMessage").value.trim();
            const enabled = document.getElementById("welcomeEnabled").checked;
            if (enabled && !channel) { alert("اختر روم الترحيب أولاً"); return; }
            if (enabled && !message) { alert("اكتب رسالة الترحيب أولاً"); return; }
            try {
                const response = await fetch("{{ url_for('save_welcome') }}", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ guild_id: "{{ guild_id }}", channel_id: channel, message: message, enabled: enabled })
                });
                const data = await response.json();
                alert(data.message || "تم حفظ الترحيب");
                if (data.success) { location.reload(); }
            } catch (error) { alert("حدث خطأ أثناء حفظ الترحيب"); }
        }

        async function disableWelcome() {
            try {
                const response = await fetch("{{ url_for('disable_welcome') }}", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ guild_id: "{{ guild_id }}" })
                });
                const data = await response.json();
                alert(data.message || "تم إيقاف الترحيب");
                if (data.success) { location.reload(); }
            } catch (error) { alert("حدث خطأ أثناء إيقاف الترحيب"); }
        }
        </script>
        """,
        guild=guild, guild_id=guild_id, channels=channels, roles=roles,
        economy=economy, economy_enabled=economy_enabled, economy_room_id=economy_room_id,
        economy_room_name=economy_room_name, forced=is_forced_economy_guild(guild_id),
        welcome=welcome, welcome_enabled=welcome_enabled, welcome_channel_id=welcome_channel_id,
        welcome_message=welcome_message, welcome_status=welcome_status, welcome_status_text=welcome_status_text,
    )


# =========================================================
# الاقتصاد & الترحيب API
# =========================================================

@app.route("/api/economy/enable", methods=["POST"])
def enable_economy():
    data = request.get_json(silent=True) or {}
    guild_id = clean_id(data.get("guild_id", ""))
    room_id = clean_id(data.get("room_id", ""))
    if not guild_id or not room_id:
        return {"success": False, "message": "بيانات ناقصة"}, 400
    if not user_can_control(guild_id):
        return {"success": False, "message": "ليس لديك صلاحية"}, 403

    economy_settings_collection.update_one(
        {"guild_id": {"$in": guild_id_variants(guild_id)}},
        {"$set": {"guild_id": guild_id, "currency_enabled": True, "economy_room_id": room_id, "updated_at": datetime.utcnow()}},
        upsert=True,
    )
    return {"success": True, "message": "تم تفعيل الاقتصاد وحفظ روم الاقتصاد"}


@app.route("/api/economy/disable", methods=["POST"])
def disable_economy():
    data = request.get_json(silent=True) or {}
    guild_id = clean_id(data.get("guild_id", ""))
    if not guild_id:
        return {"success": False, "message": "معرف السيرفر ناقص"}, 400
    if is_forced_economy_guild(guild_id):
        return {"success": False, "message": "لا يمكن تعطيل الاقتصاد في هذا السيرفر لأنه إجباري"}, 403
    if not user_can_control(guild_id):
        return {"success": False, "message": "ليس لديك صلاحية"}, 403

    economy_settings_collection.update_one(
        {"guild_id": {"$in": guild_id_variants(guild_id)}},
        {"$set": {"guild_id": guild_id, "currency_enabled": False, "updated_at": datetime.utcnow()}},
        upsert=True,
    )
    return {"success": True, "message": "تم تعطيل الاقتصاد"}


@app.route("/api/welcome/save", methods=["POST"])
def save_welcome():
    data = request.get_json(silent=True) or {}
    guild_id = clean_id(data.get("guild_id", ""))
    channel_id = clean_id(data.get("channel_id", ""))
    message = str(data.get("message", "")).strip()
    enabled = bool(data.get("enabled", False))

    if not guild_id:
        return {"success": False, "message": "معرف السيرفر ناقص"}, 400
    if not user_can_control(guild_id):
        return {"success": False, "message": "ليس لديك صلاحية"}, 403
    if enabled and (not channel_id or not message):
        return {"success": False, "message": "املأ الحقول المطلوبة"}, 400

    now = datetime.utcnow()
    welcome_settings_collection.update_one(
        {"guild_id": guild_id},
        {"$set": {"guild_id": guild_id, "channel_id": channel_id, "message": message, "enabled": enabled, "updated_at": now},
         "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return {"success": True, "message": "تم حفظ نظام الترحيب بنجاح"}


@app.route("/api/welcome/disable", methods=["POST"])
def disable_welcome():
    data = request.get_json(silent=True) or {}
    guild_id = clean_id(data.get("guild_id", ""))
    if not guild_id or not user_can_control(guild_id):
        return {"success": False, "message": "غير مسموح"}, 400

    welcome_settings_collection.update_one(
        {"guild_id": guild_id},
        {"$set": {"enabled": False, "updated_at": datetime.utcnow()}},
        upsert=True,
    )
    return {"success": True, "message": "تم إيقاف نظام الترحيب"}


# =========================================================
# Commands Page (محدثة مع الأقسام وخيار جميع الرومات)
# =========================================================

@app.route("/server/<guild_id>/commands")
def commands_page(guild_id):
    guild_id = clean_id(guild_id)
    if not user_can_control(guild_id):
        return "ليس لديك صلاحية", 403

    guild = get_bot_guild(guild_id)
    if not guild:
        return "السيرفر غير موجود", 404

    raw_channels = get_bot_channels(guild_id)
    channels = prepare_channels_for_picker(raw_channels)
    roles = prepare_roles_for_picker(get_bot_roles(guild_id))
    commands = list(commands_collection.find({}))

    saved_settings = {}
    for setting in settings_collection.find({"guild_id": guild_id}):
        name = setting.get("command_name") or setting.get("name")
        if not name:
            continue
        saved_settings[name] = {
            "enabled": bool(setting.get("enabled", True)),
            "all_channels": bool(setting.get("all_channels", False)),
            "channel_ids": [str(x) for x in setting.get("channel_ids", [])],
            "role_ids": [str(x) for x in setting.get("role_ids", [])],
        }

    return render_template_string(
        BASE_STYLE + """
        <div class="topbar">
            <div class="topbar-inner">
                <div class="brand">
                    <div class="brand-icon">ض</div>
                    إدارة الأوامر
                </div>
                <a class="btn btn-secondary" href="{{ url_for('server_page', guild_id=guild_id) }}">← رجوع</a>
            </div>
        </div>
        <div class="container">
            <div class="hero">
                <h1>إدارة <span class="gradient-text">الأوامر</span></h1>
                <p>تحكم بشكل دقيق مقسم حسب الأقسام (إدارية، عامة، اقتصادية، وغيرها) مع دعم تشغيل الأوامر بكل الرومات القديمة والجديدة تلقائياً.</p>
            </div>

            <!-- أزرار أقسام الأوامر -->
            <div class="sections-tabs">
                <button class="section-tab-btn active" onclick="filterSection('all', this)">📂 كل الأوامر</button>
                <button class="section-tab-btn" onclick="filterSection('admin', this)">🔐 الأوامر الإدارية</button>
                <button class="section-tab-btn" onclick="filterSection('normal', this)">👤 الأوامر العامة</button>
                <button class="section-tab-btn" onclick="filterSection('economy', this)">💰 أوامر الاقتصاد</button>
            </div>

            <div class="card">
                <input class="input" id="commandSearch" placeholder="🔎 ابحث عن أمر معين..." oninput="filterCommands()">
            </div>

            <div id="commandsList">
            {% for command in commands %}
            {% set command_name = command.get("name") or command.get("command_name") or "بدون اسم" %}
            {% set is_admin = command.get("is_admin_command", False) %}
            {% set is_econ = "economy" in command_name|lower or "bank" in command_name|lower or "فلوس" in command_name|lower or "راتب" in command_name|lower %}
            {% set sec_type = "admin" if is_admin else ("economy" if is_econ else "normal") %}

            {% set saved = saved_settings.get(command_name, {}) %}
            {% set saved_channels = saved.get("channel_ids", []) %}
            {% set saved_roles = saved.get("role_ids", []) %}
            {% set saved_enabled = saved.get("enabled", True) %}
            {% set saved_all_channels = saved.get("all_channels", False) %}

            <div class="command-card" data-command="{{ command_name|lower }}" data-section="{{ sec_type }}">
                <div class="command-head">
                    <div>
                        <div class="command-name">-{{ command_name }}</div>
                        <div style="margin-top:7px">
                            {% if is_admin %}
                            <span class="badge badge-admin">🔐 إداري</span>
                            {% elif is_econ %}
                            <span class="badge badge-normal" style="color:#facc15; background:rgba(250,204,21,.1);">💰 اقتصاد</span>
                            {% else %}
                            <span class="badge badge-normal">👤 عادي</span>
                            {% endif %}
                        </div>
                    </div>
                    <label class="switch">
                        <input type="checkbox" class="command-enabled" {% if saved_enabled %}checked{% endif %}>
                        <span class="slider"></span>
                    </label>
                </div>

                <!-- خيار العمل في كل الرومات الجديدة والقديمة -->
                <div class="all-channels-toggle-box">
                    <div>
                        <div style="font-weight:900; font-size:13px;">🌐 تشغيل الامر بكل الرومات (القديمة والجديدة)</div>
                        <div class="small">عند التفعيل، سيعمل الأمر في كافة الرومات الحالية وأي روم جديد تضيفه مستقبلاً دون الحاجة للتحديث.</div>
                    </div>
                    <label class="switch">
                        <input type="checkbox" class="all-channels-checkbox" {% if saved_all_channels %}checked{% endif %} onchange="toggleAllChannels(this)">
                        <span class="slider"></span>
                    </label>
                </div>

                <div class="command-grid" style="{% if saved_all_channels %}opacity: 0.4; pointer-events: none;{% endif %}">
                    <!-- الرومات -->
                    <div>
                        <label class="form-label">🎯 الرومات المسموح بها</label>
                        <div class="picker">
                            <div class="picker-top">
                                <input class="picker-search" placeholder="🔎 ابحث عن روم..." oninput="filterOptions(this)">
                                <div class="picker-actions">
                                    <button type="button" class="mini-btn" onclick="selectAllInside(this, '.channel-check')">تحديد الكل</button>
                                    <button type="button" class="mini-btn" onclick="clearAllInside(this, '.channel-check')">إلغاء الكل</button>
                                </div>
                            </div>
                            <div class="options">
                                {% for channel in channels %}
                                <label class="option" data-search="{{ channel.name|lower }}">
                                    <input type="checkbox" value="{{ channel.id }}" class="channel-check" {% if channel.id|string in saved_channels %}checked{% endif %}>
                                    <span># {{ channel.name }}</span>
                                </label>
                                {% endfor %}
                            </div>
                        </div>
                        <div class="selected-count channel-count"></div>
                    </div>

                    <!-- الرتب -->
                    <div>
                        <label class="form-label">🛡️ الرتب المسموح بها</label>
                        <div class="roles-picker">
                            <div class="picker-top">
                                <input class="picker-search" placeholder="🔎 ابحث عن رتبة..." oninput="filterOptions(this)">
                                <div class="roles-toolbar">
                                    <button type="button" class="mini-btn" onclick="selectAllInside(this, '.role-check')">👑 تحديد الكل</button>
                                    <button type="button" class="mini-btn" onclick="clearAllInside(this, '.role-check')">مسح الكل</button>
                                </div>
                            </div>
                            <div class="roles-list">
                                {% for role in roles %}
                                <label class="role-card {% if role.id in saved_roles %}selected{% endif %}" data-search="{{ role.name|lower }}">
                                    <input type="checkbox" value="{{ role.id }}" class="role-check" {% if role.id in saved_roles %}checked{% endif %}>
                                    <span class="role-color" style="background: {{ role.color }};"></span>
                                    <span class="role-info">
                                        <span class="role-name">{{ role.name }}</span>
                                        <span class="role-id">{{ role.id }}</span>
                                    </span>
                                </label>
                                {% endfor %}
                            </div>
                        </div>
                        <div class="role-counter"></div>
                    </div>
                </div>

                <button type="button" class="btn btn-yellow" style="margin-top:18px" onclick="saveCommand('{{ command_name }}', this)">💾 حفظ إعدادات الأمر</button>
            </div>
            {% endfor %}
            </div>

            {% if not commands %}
            <div class="card empty">لا توجد أوامر مسجلة في الموقع.</div>
            {% endif %}
            <div class="footer">ضياء BOT • نظام إدارة الأوامر</div>
        </div>

        <script>
        let currentSection = 'all';

        function filterSection(section, btn) {
            currentSection = section;
            document.querySelectorAll('.section-tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            applyFilters();
        }

        function filterCommands() {
            applyFilters();
        }

        function applyFilters() {
            const query = document.getElementById("commandSearch").value.toLowerCase().trim();
            document.querySelectorAll(".command-card").forEach(card => {
                const name = card.dataset.command || "";
                const sec = card.dataset.section || "";
                const matchesSection = (currentSection === 'all' || sec === currentSection);
                const matchesQuery = name.includes(query);
                card.style.display = (matchesSection && matchesQuery) ? "" : "none";
            });
        }

        function toggleAllChannels(checkbox) {
            const card = checkbox.closest('.command-card');
            const grid = card.querySelector('.command-grid');
            if (checkbox.checked) {
                grid.style.opacity = '0.4';
                grid.style.pointerEvents = 'none';
            } else {
                grid.style.opacity = '1';
                grid.style.pointerEvents = 'auto';
            }
        }

        function filterOptions(input) {
            const value = input.value.toLowerCase().trim();
            const picker = input.closest(".picker, .roles-picker");
            if (!picker) return;
            picker.querySelectorAll(".option, .role-card").forEach(item => {
                const search = item.dataset.search || "";
                item.style.display = search.includes(value) ? (item.classList.contains("role-card") ? "flex" : "grid") : "none";
            });
        }

        function selectAllInside(button, selector) {
            const picker = button.closest(".picker, .roles-picker");
            if (!picker) return;
            picker.querySelectorAll(selector).forEach(input => {
                input.checked = true;
                const role = input.closest(".role-card");
                if (role) role.classList.add("selected");
            });
            updateCounts();
        }

        function clearAllInside(button, selector) {
            const picker = button.closest(".picker, .roles-picker");
            if (!picker) return;
            picker.querySelectorAll(selector).forEach(input => {
                input.checked = false;
                const role = input.closest(".role-card");
                if (role) role.classList.remove("selected");
            });
            updateCounts();
        }

        function updateCounts() {
            document.querySelectorAll(".command-card").forEach(card => {
                const channels = card.querySelectorAll(".channel-check:checked").length;
                const roles = card.querySelectorAll(".role-check:checked").length;
                const channelCounter = card.querySelector(".channel-count");
                const roleCounter = card.querySelector(".role-counter");
                if (channelCounter) channelCounter.textContent = channels ? "✓ تم تحديد " + channels + " روم" : "لم يتم تحديد رومات";
                if (roleCounter) roleCounter.textContent = roles ? "✓ تم تحديد " + roles + " رتبة" : "لم يتم تحديد رتب";
            });
        }

        async function saveCommand(commandName, button) {
            const card = button.closest(".command-card");
            if (!card) return;

            const allChannelsChecked = card.querySelector(".all-channels-checkbox").checked;
            const channels = allChannelsChecked ? [] : [...card.querySelectorAll(".channel-check:checked")].map(x => x.value);
            const roles = [...card.querySelectorAll(".role-check:checked")].map(x => x.value);
            const enabled = card.querySelector(".command-enabled").checked;
            const oldText = button.innerHTML;

            button.disabled = true;
            button.innerHTML = "⏳ جاري الحفظ...";

            try {
                const response = await fetch("{{ url_for('save_command') }}", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        guild_id: "{{ guild_id }}",
                        command_name: commandName,
                        all_channels: allChannelsChecked,
                        channel_ids: channels,
                        role_ids: roles,
                        enabled: enabled
                    })
                });
                const data = await response.json();
                if (data.success) {
                    button.innerHTML = "✓ تم الحفظ";
                    setTimeout(() => { button.innerHTML = oldText; }, 1600);
                } else {
                    alert(data.message || "فشل الحفظ");
                    button.innerHTML = oldText;
                }
            } catch (error) {
                alert("حدث خطأ أثناء حفظ الإعدادات");
                button.innerHTML = oldText;
            }
            button.disabled = false;
        }

        document.addEventListener("change", function(event) {
            if (event.target.matches(".role-check")) {
                const role = event.target.closest(".role-card");
                if (role) role.classList.toggle("selected", event.target.checked);
            }
            updateCounts();
        });

        updateCounts();
        </script>
        """,
        guild=guild, guild_id=guild_id, channels=channels, roles=roles,
        commands=commands, saved_settings=saved_settings,
    )


# =========================================================
# Save Command API
# =========================================================

@app.route("/save-command", methods=["POST"])
def save_command():
    data = request.get_json(silent=True) or {}
    guild_id = clean_id(data.get("guild_id", ""))
    command_name = str(data.get("command_name", "")).strip()
    all_channels = bool(data.get("all_channels", False))
    channel_ids = list({str(x).strip() for x in data.get("channel_ids", []) if str(x).strip()})
    role_ids = list({str(x).strip() for x in data.get("role_ids", []) if str(x).strip()})
    enabled = bool(data.get("enabled", True))

    if not guild_id or not command_name:
        return {"success": False, "message": "بيانات ناقصة"}, 400
    if not user_can_control(guild_id):
        return {"success": False, "message": "ليس لديك صلاحية"}, 403

    now = datetime.utcnow()
    settings_collection.update_one(
        {"guild_id": guild_id, "command_name": command_name},
        {
            "$set": {
                "guild_id": guild_id,
                "command_name": command_name,
                "all_channels": all_channels,
                "channel_ids": [] if all_channels else channel_ids,
                "role_ids": role_ids,
                "enabled": enabled,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now}
        },
        upsert=True,
    )
    return {"success": True, "message": f"تم حفظ إعدادات -{command_name} بنجاح"}


# =========================================================
# Aliases Page
# =========================================================

@app.route("/server/<guild_id>/aliases")
def aliases_page(guild_id):
    guild_id = clean_id(guild_id)
    if not user_can_control(guild_id):
        return "ليس لديك صلاحية", 403

    guild = get_bot_guild(guild_id)
    if not guild:
        return "السيرفر غير موجود", 404

    commands = list(commands_collection.find({}))
    aliases = list(aliases_collection.find({"guild_id": guild_id}).sort("created_at", -1))

    return render_template_string(
        BASE_STYLE + """
        <div class="topbar">
            <div class="topbar-inner">
                <div class="brand">
                    <div class="brand-icon">ض</div>
                    اختصارات الأوامر
                </div>
                <a class="btn btn-secondary" href="{{ url_for('server_page', guild_id=guild_id) }}">← رجوع</a>
            </div>
        </div>
        <div class="container">
            <div class="hero">
                <h1>اختصارات <span class="gradient-text">الأوامر</span></h1>
                <p>اصنع اختصاراً قصيراً لأي أمر. مثال: اجعل <b>-ذ</b> ينفذ <b>-ذهبي</b>.</p>
            </div>
            <div class="card">
                <div style="display:grid; grid-template-columns: 1fr 1fr auto; gap:10px; align-items:end;">
                    <div>
                        <label class="form-label">الأمر الأصلي</label>
                        <select id="originalCommand" class="select-box">
                            {% for command in commands %}
                            {% set name = command.get("name") or command.get("command_name") %}
                            {% if name %}<option value="{{ name }}">-{{ name }}</option>{% endif %}
                            {% endfor %}
                        </select>
                    </div>
                    <div>
                        <label class="form-label">الاختصار</label>
                        <input id="alias" class="input" maxlength="30" placeholder="مثال: ذ">
                    </div>
                    <button type="button" class="btn btn-yellow" onclick="createAlias()">إضافة الاختصار</button>
                </div>
            </div>
            <div class="card">
                <div class="card-title"><h2>📋 الاختصارات الحالية</h2></div>
                <div class="alias-list">
                {% for item in aliases %}
                    <div style="display:flex; justify-content:space-between; align-items:center; padding:13px; border-radius:14px; background:#081a2f; border:1px solid rgba(255,255,255,.09); margin-bottom:8px;">
                        <div>
                            <div style="direction:ltr; font-family:monospace; color:#fde68a; font-weight:900;">-{{ item.get("alias") }}</div>
                            <div class="small" style="margin-top:5px">ينفذ: <b>-{{ item.get("command_name") }}</b></div>
                        </div>
                        <button type="button" class="btn btn-danger" onclick="deleteAlias('{{ item.get("alias") }}')">حذف</button>
                    </div>
                {% else %}
                    <div class="empty">لا توجد اختصارات حالياً.</div>
                {% endfor %}
                </div>
            </div>
            <div class="footer">ضياء BOT</div>
        </div>
        <script>
        async function createAlias() {
            const command = document.getElementById("originalCommand").value;
            let alias = document.getElementById("alias").value.trim().replace(/^[-.]/, "");
            if (!command || !alias) { alert("املأ البيانات المطلوبة"); return; }
            try {
                const response = await fetch("{{ url_for('create_alias') }}", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ guild_id: "{{ guild_id }}", command_name: command, alias: alias })
                });
                const data = await response.json();
                alert(data.message || "تم التنفيذ");
                if (data.success) { location.reload(); }
            } catch (error) { alert("حدث خطأ"); }
        }

        async function deleteAlias(alias) {
            if (!confirm("هل تريد حذف هذا الاختصار؟")) return;
            try {
                const response = await fetch("{{ url_for('delete_alias') }}", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ guild_id: "{{ guild_id }}", alias: alias })
                });
                const data = await response.json();
                alert(data.message || "تم التنفيذ");
                if (data.success) { location.reload(); }
            } catch (error) { alert("حدث خطأ"); }
        }
        </script>
        """,
        guild=guild, guild_id=guild_id, commands=commands, aliases=aliases,
    )


@app.route("/api/aliases/create", methods=["POST"])
def create_alias():
    data = request.get_json(silent=True) or {}
    guild_id = clean_id(data.get("guild_id", ""))
    command_name = str(data.get("command_name", "")).strip()
    alias = str(data.get("alias", "")).strip().lstrip("-.").lower()

    if not guild_id or not command_name or not alias:
        return {"success": False, "message": "البيانات ناقصة"}, 400
    if not user_can_control(guild_id):
        return {"success": False, "message": "ليس لديك صلاحية"}, 403

    existing = aliases_collection.find_one({"guild_id": guild_id, "alias": alias})
    if existing:
        return {"success": False, "message": "هذا الاختصار مستخدم بالفعل"}, 400

    aliases_collection.insert_one({
        "guild_id": guild_id,
        "alias": alias,
        "command_name": command_name,
        "created_at": datetime.utcnow(),
    })
    return {"success": True, "message": f"تم إنشاء الاختصار -{alias} ← -{command_name}"}


@app.route("/api/aliases/delete", methods=["POST"])
def delete_alias():
    data = request.get_json(silent=True) or {}
    guild_id = clean_id(data.get("guild_id", ""))
    alias = str(data.get("alias", "")).strip().lower()

    if not guild_id or not alias or not user_can_control(guild_id):
        return {"success": False, "message": "خطأ في البيانات"}, 400

    aliases_collection.delete_one({"guild_id": guild_id, "alias": alias})
    return {"success": True, "message": "تم حذف الاختصار"}


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/health")
def health():
    return "OK"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
