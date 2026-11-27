import unittest
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import jwt
import time

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from core.config import SECRET_KEY


class TestClientDashboard(unittest.IsolatedAsyncioTestCase):

    async def test_client_endpoints_and_queue_isolation(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import client

        app = FastAPI()
        app.include_router(client.router)
        mock_redis = AsyncMock()

        config_db = {
            "org_sneakers": {
                "secret_key": "sneaker_secret_key_123",
                "tier1_queue": "true",
                "tier3_lottery": "true",
                "redirect_url": "https://sneakers.store/checkout",
                "process_rate": "30.0",
                "start_time": "2026-12-01T10:00:00Z",
                "auto_start": "true",
            }
        }

        async def hgetall(key):
            key = str(key)
            if key.startswith("config:"):
                return config_db.get(key.split("config:", 1)[1], {})
            return {}

        async def hset(key, mapping=None):
            cid = str(key).split("config:", 1)[1]
            if cid in config_db:
                config_db[cid].update(mapping)
            else:
                config_db[cid] = mapping
            return True

        mock_redis.hgetall = AsyncMock(side_effect=hgetall)
        mock_redis.hset = AsyncMock(side_effect=hset)
        mock_redis.sadd = AsyncMock()
        mock_redis.delete = AsyncMock()
        mock_redis.get = AsyncMock(return_value="false")
        mock_redis.scard = AsyncMock(return_value=15)
        mock_redis.llen = AsyncMock(return_value=8)
        mock_pipeline = MagicMock()
        mock_pipeline.execute = AsyncMock(return_value=True)
        mock_redis.pipeline = MagicMock(return_value=mock_pipeline)
        mock_redis.publish = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.smembers = AsyncMock(return_value={"user-alpha"})
        app.state.redis = mock_redis

        test_client = TestClient(app)

        # 1. Invalid login
        bad_login = test_client.post("/client/login", json={"client_id": "org_sneakers", "secret_key": "wrong_key"})
        self.assertEqual(bad_login.status_code, 401)

        # 2. Valid login
        good_login = test_client.post("/client/login", json={"client_id": "org_sneakers", "secret_key": "sneaker_secret_key_123"})
        self.assertEqual(good_login.status_code, 200)
        token = good_login.json()["access_token"]
        auth_headers = {"Authorization": f"Bearer {token}"}

        # 3. Get config
        cfg_res = test_client.get("/client/config", headers=auth_headers)
        self.assertEqual(cfg_res.status_code, 200)
        self.assertEqual(cfg_res.json()["client_id"], "org_sneakers")
        self.assertEqual(cfg_res.json()["process_rate"], 30.0)
        self.assertTrue(cfg_res.json()["auto_start"])
        self.assertEqual(cfg_res.json()["tiers"], ["tier1_queue", "tier3_lottery"])

        # 4. Update config
        update_res = test_client.put(
            "/client/config",
            json={"process_rate": 50.0, "auto_start": False},
            headers=auth_headers,
        )
        self.assertEqual(update_res.status_code, 200)
        self.assertEqual(update_res.json()["process_rate"], 50.0)
        self.assertFalse(update_res.json()["auto_start"])

        # 5. Emergency Flush - Flushes ONLY own queue: queue:org_sneakers:auth_queue
        flush_res = test_client.post("/client/flush/auth_queue", headers=auth_headers)
        self.assertEqual(flush_res.status_code, 200)
        mock_redis.delete.assert_awaited_with("queue:org_sneakers:auth_queue")

        # 6. Client Metrics
        metrics_res = test_client.get("/client/metrics", headers=auth_headers)
        self.assertEqual(metrics_res.status_code, 200)
        self.assertEqual(metrics_res.json()["client_id"], "org_sneakers")
        self.assertEqual(metrics_res.json()["holding_pool_count"], 15)
        self.assertEqual(metrics_res.json()["queue_count"], 16) # 8 auth + 8 fast

        # 7. Manual Go-Live
        golive_res = test_client.post("/client/go-live", headers=auth_headers)
        self.assertEqual(golive_res.status_code, 200)
        self.assertEqual(golive_res.json()["status"], "success")

    async def test_pre_auth_window_bounds(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import client

        app = FastAPI()
        app.include_router(client.router)
        mock_redis = AsyncMock()
        mock_redis.hgetall = AsyncMock(return_value={"tier1_queue": "true", "secret_key": "sec"})
        mock_redis.hset = AsyncMock()
        mock_redis.sadd = AsyncMock()
        mock_redis.get = AsyncMock(return_value="false")
        app.state.redis = mock_redis

        token = jwt.encode({"sub": "client_drop", "client_id": "client_drop", "type": "client"}, SECRET_KEY, algorithm="HS256")
        headers = {"Authorization": f"Bearer {token}"}
        tc = TestClient(app)

        # 1. Valid: 120 hours (5 days)
        res_ok = tc.put("/client/config", json={"pre_auth_window_hours": 120}, headers=headers)
        self.assertEqual(res_ok.status_code, 200)

        # 2. Invalid: 121 hours (> 5 days) fails validation
        res_over = tc.put("/client/config", json={"pre_auth_window_hours": 121}, headers=headers)
        self.assertEqual(res_over.status_code, 422)

        # 3. Invalid: 0 hours (< 1 hour) fails validation
        res_under = tc.put("/client/config", json={"pre_auth_window_hours": 0}, headers=headers)
        self.assertEqual(res_under.status_code, 422)


if __name__ == "__main__":
    unittest.main()
