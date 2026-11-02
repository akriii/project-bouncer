# backend/core/config.py
import os
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.environ.get("SECRET_KEY", "super_secret_jwt_key_bouncer_2026")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

VALID_LANES = {"fast_lane", "auth_queue", "holding_pool"}
DEV_USERS = {"test_user": "pwd"}

INTENT_SECRET_KEY = os.environ.get("INTENT_SECRET_KEY", "bouncer-shared-intent-secret-key-2026")
