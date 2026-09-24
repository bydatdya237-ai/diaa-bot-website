import requests
from flask import session

from .config import (
    BOT_OWNER_ID,
    ADMIN_COMMAND_NAMES,
)

from .database import (
    commands_collection,
    guilds_collection,
)

from .discord_api import (
    DISCORD_API,
    cache_get,
    cache_set,
)


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


# =========================================================
# صاحب البوت
# =========================================================

def is_bot_owner():
    user = get_user()

    if not user:
        return False

    return str(
        user.get("id")
    ) == BOT_OWNER_ID


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

    for guild in get_user_guilds():

        if str(
            guild.get("id")
        ) != guild_id:
            continue

        if guild.get(
            "owner",
            False
        ) is True:
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

        # Administrator
        if permissions & 0x8:
            return True

        # Manage Guild
        if permissions & 0x20:
            return True

        installer = guilds_collection.find_one({
            "guild_id": guild_id,
            "user_id": user_id,
        })

        if installer:
            return True

        installer = guilds_collection.find_one({
            "guild_id": guild_id,
            "installer_id": user_id,
        })

        if installer:
            return True

        break

    return False
