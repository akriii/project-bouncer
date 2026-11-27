import unittest
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import jwt

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from core.config import SECRET_KEY, INTENT_SECRET_KEY
from routers.queue import is_safe_return_url


class TestQueue(unittest.IsolatedAsyncioTestCase):

    def test_open_redirect_protection(self):
        # 1. Reject malicious external URLs when not in allowed list
        self.assertFalse(is_safe_return_url("https://evil.com/phishing", None))
        self.assertFalse(is_safe_return_url("javascript:alert(1)", None))
        self.assertFalse(is_safe_return_url("//evil.com", None))
        self.assertFalse(is_safe_return_url("data:text/html,evil", None))

        # 2. Accept local dev origins by default
        self.assertTrue(is_safe_return_url("http://localhost:8001/checkout", None))
        self.assertTrue(is_safe_return_url("http://127.0.0.1:3000/callback", None))

        # 3. Accept specifically whitelisted client origins
        allowed = "https://mystore.com, https://drop.sneakers.io"
        self.assertTrue(is_safe_return_url("https://mystore.com/checkout", allowed))
        self.assertTrue(is_safe_return_url("https://drop.sneakers.io/success", allowed))
        self.assertFalse(is_safe_return_url("https://attacker.com/steal", allowed))

    async def test_join_rejects_open_redirect(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import queue

        app = FastAPI()
        app.include_router(queue.router)
        app.state.redis = AsyncMock()
        # Mock empty config
        app.state.redis.hgetall = AsyncMock(return_value={})

        client = TestClient(app)

        # Attacker trying to supply evil redirect URL
        res = client.post("/join", json={
            "client_id": "default_client",
            "return_to": "https://evil-scalper.com/phish"
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("Invalid or unauthorized return_to URL", res.json()["detail"])

    async def test_join_rejects_unregistered_client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import queue

        app = FastAPI()
        app.include_router(queue.router)
        app.state.redis = AsyncMock()
        # Unregistered client has no redis config
        app.state.redis.hgetall = AsyncMock(return_value={})

        client = TestClient(app)

        res = client.post("/join", json={
            "client_id": "fake_unregistered_client",
            "return_to": "http://localhost:8001/checkout"
        })
        self.assertEqual(res.status_code, 404)
        self.assertIn("Invalid or unregistered client_id", res.json()["detail"])

    async def test_tier3_intent_token_and_replay_prevention(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import queue

        app = FastAPI()
        app.include_router(queue.router)
        mock_redis = AsyncMock()
        # Mock Tier 3 subscription config
        mock_redis.hgetall = AsyncMock(return_value={"tier3_lottery": "true"})
        mock_redis.get = AsyncMock(return_value="false") # Event not live yet
        # First time JTI is fresh -> True. Second time -> False (already seen)
        mock_redis.set = AsyncMock(side_effect=[True, False])
        mock_redis.sadd = AsyncMock()
        mock_redis.hset = AsyncMock()
        mock_redis.expire = AsyncMock()
        app.state.redis = mock_redis

        client = TestClient(app)

        # 1. Valid intent token
        payload = {
            "type": "pre_cart_intent",
            "client_id": "org_flash_tix",
            "event_id": "drop_123",
            "jti": "unique-nonce-1"
        }
        token = jwt.encode(payload, INTENT_SECRET_KEY, algorithm="HS256")

        # First request succeeds
        res1 = client.post(
            "/join",
            json={"client_id": "org_flash_tix", "return_to": "http://localhost:8001/checkout"},
            headers={"X-Intent-Token": token}
        )
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.json()["lane"], "holding_pool")
        self.assertEqual(res1.json()["tiers"], ["tier3_lottery"])
        self.assertEqual(res1.json()["access"], "holding_pool")

        # Second request with SAME token (replay attack) fails with 403
        res2 = client.post(
            "/join",
            json={"client_id": "org_flash_tix", "return_to": "http://localhost:8001/checkout"},
            headers={"X-Intent-Token": token}
        )
        self.assertEqual(res2.status_code, 403)
        self.assertIn("already been used", res2.json()["detail"])

    async def test_join_follows_subscribed_tier_mix(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import queue

        app = FastAPI()
        app.include_router(queue.router)

        def make_redis(config):
            mock_redis = AsyncMock()
            mock_redis.rpush = AsyncMock(return_value=4)
            mock_redis.hset = AsyncMock()
            mock_redis.expire = AsyncMock()
            mock_redis.setex = AsyncMock()
            mock_redis.publish = AsyncMock()
            mock_redis.sadd = AsyncMock()
            mock_pipeline = MagicMock()
            mock_pipeline.execute = AsyncMock(return_value=[1, True])
            mock_redis.pipeline = MagicMock(return_value=mock_pipeline)

            async def hgetall(key):
                if str(key).startswith("session:"):
                    return {"client_id": "org_mix", "lane": "direct", "return_to": "http://localhost:8001/checkout"}
                return config

            mock_redis.hgetall = AsyncMock(side_effect=hgetall)
            return mock_redis

        client_app = TestClient(app)

        app.state.redis = make_redis({"tier1_queue": "true"})
        queued = client_app.post("/join", json={
            "client_id": "org_mix",
            "return_to": "http://localhost:8001/checkout",
        })
        self.assertEqual(queued.status_code, 200)
        self.assertEqual(queued.json()["access"], "queue")
        self.assertEqual(queued.json()["lane"], "auth_queue")
        self.assertEqual(queued.json()["tiers"], ["tier1_queue"])
        app.state.redis.rpush.assert_awaited()

        app.state.redis = make_redis({"tier2_waf": "true", "secret_key": "shield-secret"})
        direct = client_app.post(
            "/join",
            json={"client_id": "org_mix", "return_to": "http://localhost:8001/checkout"},
            headers={"User-Agent": "Mozilla/5.0", "Sec-Fetch-Mode": "cors"},
        )
        self.assertEqual(direct.status_code, 200)
        self.assertEqual(direct.json()["access"], "direct")
        self.assertEqual(direct.json()["tiers"], ["tier2_waf"])
        self.assertNotIn("initial_position", direct.json())
        ticket = jwt.decode(direct.json()["entry_ticket"], "shield-secret", algorithms=["HS256"])
        self.assertEqual(ticket["type"], "entry_ticket")
        self.assertEqual(ticket["client_id"], "org_mix")
        app.state.redis.rpush.assert_not_called()

        app.state.redis = make_redis({"secret_key": "only-a-secret"})
        missing = client_app.post("/join", json={
            "client_id": "org_mix",
            "return_to": "http://localhost:8001/checkout",
        })
        self.assertEqual(missing.status_code, 403)
        self.assertIn("no active subscription tiers", missing.json()["detail"])


if __name__ == "__main__":
    unittest.main()
