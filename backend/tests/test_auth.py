import unittest
import sys
from pathlib import Path
from unittest.mock import AsyncMock
import jwt
from datetime import datetime, timezone

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from core.config import SECRET_KEY


class TestAuth(unittest.IsolatedAsyncioTestCase):

    async def test_pre_auth_login_window_rejection(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import shopper_auth

        app = FastAPI()
        app.include_router(shopper_auth.router)
        mock_redis = AsyncMock()

        # Drop starts 10 hours from now
        future_start = datetime.now(timezone.utc).timestamp() + (10 * 3600)
        future_start_iso = datetime.fromtimestamp(future_start, tz=timezone.utc).isoformat()

        # Config: Pre-auth window only opens 2 hours before start
        mock_redis.hgetall = AsyncMock(return_value={
            "tier1_queue": "true",
            "start_time": future_start_iso,
            "pre_auth_window_hours": "2"
        })
        app.state.redis = mock_redis

        tc = TestClient(app)

        # Login attempted now (10 hours before start, while window opens 2 hours before) -> REJECTED (400)
        res_rejected = tc.post("/login", json={
            "student_id": "test_user",
            "password": "pwd",
            "client_id": "client_strict"
        })
        self.assertEqual(res_rejected.status_code, 400)
        self.assertIn("Pre-authentication window is not open yet", res_rejected.json()["detail"])

        # Config: Window opens 24 hours before start (now is inside the window) -> ACCEPTED (200)
        mock_redis.hgetall = AsyncMock(return_value={
            "tier1_queue": "true",
            "start_time": future_start_iso,
            "pre_auth_window_hours": "24"
        })
        res_accepted = tc.post("/login", json={
            "student_id": "test_user",
            "password": "pwd",
            "client_id": "client_strict"
        })
        self.assertEqual(res_accepted.status_code, 200)
        self.assertIn("access_token", res_accepted.json())

    async def test_fast_pass_client_isolation(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import queue

        app = FastAPI()
        app.include_router(queue.router)
        mock_redis = AsyncMock()
        mock_redis.rpush = AsyncMock(return_value=1)
        mock_redis.hset = AsyncMock()
        mock_redis.expire = AsyncMock()
        mock_redis.sadd = AsyncMock()

        async def hgetall(key):
            return {"tier1_queue": "true"}

        mock_redis.hgetall = AsyncMock(side_effect=hgetall)
        app.state.redis = mock_redis
        tc = TestClient(app)

        # Fast pass token issued specifically for client_alpha
        token_alpha = jwt.encode(
            {"sub": "user_1", "client_id": "client_alpha", "type": "fast_pass"},
            SECRET_KEY,
            algorithm="HS256"
        )

        # 1. User attempts to use client_alpha token on client_beta -> Stays in standard auth_queue!
        res_cross = tc.post(
            "/join",
            json={"client_id": "client_beta", "return_to": "http://localhost:8001/checkout"},
            headers={"Authorization": f"Bearer {token_alpha}"}
        )
        self.assertEqual(res_cross.status_code, 200)
        self.assertEqual(res_cross.json()["lane"], "auth_queue")

        # 2. User uses client_alpha token on client_alpha -> Enters fast_lane!
        res_valid = tc.post(
            "/join",
            json={"client_id": "client_alpha", "return_to": "http://localhost:8001/checkout"},
            headers={"Authorization": f"Bearer {token_alpha}"}
        )
        self.assertEqual(res_valid.status_code, 200)
        self.assertEqual(res_valid.json()["lane"], "fast_lane")


if __name__ == "__main__":
    unittest.main()
