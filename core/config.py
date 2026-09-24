import os
import secrets


# =========================================================
# إعدادات الموقع
# =========================================================

FLASK_SECRET_KEY = os.getenv(
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


# =========================================================
# التحقق من المتغيرات
# =========================================================

if not CLIENT_ID:
    raise RuntimeError("DISCORD_CLIENT_ID غير موجود")

if not CLIENT_SECRET:
    raise RuntimeError("DISCORD_CLIENT_SECRET غير موجود")

if not BOT_TOKEN:
    raise RuntimeError("TOKEN غير موجود")

if not MONGO_URI:
    raise RuntimeError("MONGO_URI غير موجود")
