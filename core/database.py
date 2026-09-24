from pymongo import MongoClient

from .config import MONGO_URI


# =========================================================
# MongoDB
# =========================================================

mongo = MongoClient(MONGO_URI)

db = mongo["discord_bot_db"]


# =========================================================
# Collections
#
# مهم:
# لا نغير أي اسم من أسماء الـ Collections الحالية.
# =========================================================

commands_collection = db["website_commands"]

guilds_collection = db["website_guilds"]

settings_collection = db["website_command_settings"]

economy_settings_collection = db["economy_settings"]
