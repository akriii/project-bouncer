# backend/core/config.py
import os
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.environ["SECRET_KEY"]
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

VALID_LANES = {"fast_lane", "auth_queue"}
DEV_USERS = {"test_user": "pwd"}