import json
import unittest
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import jwt

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from core.config import SECRET_KEY


class TestAdmin(unittest.IsolatedAsyncioTestCase):

    async def test_admin_endpoints_and_go_live(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import admin

        app = FastAPI()
        app.include_router(admin.router)
        mock_redis = AsyncMock()
        mock_redis.llen = AsyncMock(return_value=12)
        mock_redis.scard = AsyncMock(return_value=5)

        async def smembers(key):
            if str(key) == "registered_clients":
                return {"client_alpha", "client_beta"}
            return {"user-1", "user-2"}

        mock_redis.smembers = AsyncMock(side_effect=smembers)
        mock_pipeline = MagicMock()
        mock_pipeline.execute = AsyncMock(return_value=True)
        mock_redis.pipeline = MagicMock(return_value=mock_pipeline)
        mock_redis.publish = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.delete = AsyncMock()

        configs = {
            "client_alpha": {"tier1_queue": "true", "redirect_url": "https://alpha.com"},
            "client_beta": {"tier2_waf": "true", "tier3_lottery": "true"},
        }
        sessions = {
            "user-1": {"client_id": "client_alpha"},
            "user-2": {"client_id": "client_alpha"},
        }

        async def hgetall(key):
            key = str(key)
            if key.startswith("config:"):
                return configs.get(key.split(":", 1)[1], {})
            if key.startswith("session:"):
                return sessions.get(key.split(":", 1)[1], {})
            return {}

        mock_redis.hgetall = AsyncMock(side_effect=hgetall)
        app.state.redis = mock_redis

        client = TestClient(app)

        # Admin token
        admin_token = jwt.encode(
            {"sub": "admin", "type": "admin", "exp": int(time.time()) + 3600},
            SECRET_KEY,
            algorithm="HS256"
        )
        headers = {"Authorization": f"Bearer {admin_token}"}

        # 1. Platform Metrics (Total clients, tier breakdown, active drops)
        metrics_res = client.get("/admin/metrics", headers=headers)
        self.assertEqual(metrics_res.status_code, 200)
        self.assertEqual(metrics_res.json()["total_clients"], 2)
        self.assertEqual(metrics_res.json()["tier_counts"]["tier1"], 1)
        self.assertEqual(metrics_res.json()["tier_counts"]["tier2"], 1)
        self.assertEqual(metrics_res.json()["tier_counts"]["tier3"], 1)
        self.assertEqual(metrics_res.json()["active_sessions"], 5)

        # 2. Clients Table
        clients_res = client.get("/admin/clients", headers=headers)
        self.assertEqual(clients_res.status_code, 200)
        c_list = clients_res.json()["clients"]
        self.assertEqual(len(c_list), 2)
        self.assertEqual(c_list[0]["client_id"], "client_alpha")

        # 3. Verify Admin FLUSH is removed from operator (404)
        flush_res = client.post("/admin/flush/fast_lane", headers=headers)
        self.assertEqual(flush_res.status_code, 404)

        # 4. Go-live Grand Shuffle
        golive_res = client.post("/admin/events/drop_999/go-live", headers=headers)
        self.assertEqual(golive_res.status_code, 200)
        self.assertEqual(golive_res.json()["status"], "success")
        self.assertEqual(golive_res.json()["users_shuffled"], 2)
        self.assertEqual(golive_res.json()["users_queued"], 2)
        self.assertEqual(golive_res.json()["users_released"], 0)

    async def test_go_live_queues_only_tier1_clients(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import admin

        app = FastAPI()
        app.include_router(admin.router)
        mock_redis = AsyncMock()
        mock_redis.smembers = AsyncMock(return_value={"user-queue", "user-direct"})
        mock_pipeline = MagicMock()
        mock_pipeline.execute = AsyncMock(return_value=True)
        mock_redis.pipeline = MagicMock(return_value=mock_pipeline)
        mock_redis.publish = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.setex = AsyncMock()

        sessions = {
            "user-queue": {"client_id": "org_queue"},
            "user-direct": {"client_id": "org_direct", "event_id": "drop_999"},
        }
        configs = {
            "org_queue": {"tier1_queue": "true", "tier3_lottery": "true"},
            "org_direct": {"tier3_lottery": "true", "secret_key": "direct-secret"},
        }

        async def hgetall(key):
            key = str(key)
            if key.startswith("session:"):
                return sessions[key.split(":", 1)[1]]
            if key.startswith("config:"):
                return configs[key.split(":", 1)[1]]
            return {}

        mock_redis.hgetall = AsyncMock(side_effect=hgetall)
        app.state.redis = mock_redis

        admin_token = jwt.encode(
            {"sub": "admin", "type": "admin", "exp": int(time.time()) + 3600},
            SECRET_KEY,
            algorithm="HS256",
        )
        client = TestClient(app)
        res = client.post(
            "/admin/events/drop_999/go-live",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["users_queued"], 1)
        self.assertEqual(res.json()["users_released"], 1)
        self.assertEqual(mock_pipeline.rpush.call_args[0], ("auth_queue", "user-queue"))
        ticket_body = json.loads(mock_redis.setex.await_args.args[2])
        ticket = jwt.decode(ticket_body["entry_ticket"], "direct-secret", algorithms=["HS256"])
        self.assertEqual(ticket["client_id"], "org_direct")


if __name__ == "__main__":
    unittest.main()
