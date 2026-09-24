import time
import threading

import requests

from .config import BOT_TOKEN


# =========================================================
# Discord API
#
# الموقع يستخدم BOT_TOKEN في REST API فقط.
# الموقع لا يشغل bot.run(TOKEN).
# =========================================================

DISCORD_API = "https://discord.com/api/v10"


discord_session = requests.Session()

discord_session.headers.update({
    "Authorization": f"Bot {BOT_TOKEN}",
    "Content-Type": "application/json",
    "User-Agent": "DiaaBOT-Website/1.0",
})


# =========================================================
# Rate Limit
# =========================================================

discord_rate_lock = threading.Lock()

discord_rate_until = 0.0


# =========================================================
# Cache
# =========================================================

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


# =========================================================
# Discord Headers
# =========================================================

def discord_headers():
    return {
        "Authorization": f"Bot {BOT_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "DiaaBOT-Website/1.0",
    }


# =========================================================
# Discord Request
# =========================================================

def discord_request(
    method,
    url,
    *,
    json=None,
    data=None,
    timeout=10,
    max_attempts=2,
):
    """
    طلب Discord REST آمن نسبياً:

    - ينتظر إذا كان لدينا Retry-After سابق.
    - عند 429 يقرأ retry_after.
    - لا يعيد المحاولة بشكل لا نهائي.
    """

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
# Discord Bot - Guild
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
