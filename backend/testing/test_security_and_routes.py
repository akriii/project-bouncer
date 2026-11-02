import unittest
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import jwt
import time

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from core.config import SECRET_KEY, INTENT_SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD
from routers.auth import is_safe_return_url
from middleware.bot_protection import BotProtection, is_trusted_proxy

class TestSecurityAndRoutes(unittest.IsolatedAsyncioTestCase):

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

    def test_trusted_proxy_detection(self):
        self.assertTrue(is_trusted_proxy("127.0.0.1"))
        self.assertTrue(is_trusted_proxy("::1"))
        self.assertTrue(is_trusted_proxy("172.18.0.2"))
        self.assertTrue(is_trusted_proxy("10.0.0.5"))
        self.assertTrue(is_trusted_proxy("192.168.1.100"))
        self.assertFalse(is_trusted_proxy("8.8.8.8"))
        self.assertFalse(is_trusted_proxy("142.250.190.46"))

    async def test_bot_protection_blocks_scrapers(self):
        mock_redis = AsyncMock()
        guard = BotProtection(redis_client=mock_redis)

        # Scraper user-agent should raise 403
        request_scraper = MagicMock()
        request_scraper.headers = {"user-agent": "python-requests/2.28.1"}
        request_scraper.client = MagicMock(host="142.250.190.46")
        
        with self.assertRaises(Exception) as ctx:
            await guard.verify_request(request_scraper)
        self.assertEqual(ctx.exception.status_code, 403)

        # Legitimate browser user-agent should pass scraper check
        request_browser = MagicMock()
        request_browser.headers = {
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
            "sec-fetch-mode": "cors"
        }
        request_browser.client = MagicMock(host="142.250.190.46")
        
        # Pipeline in redis-py buffers synchronously and executes asynchronously
        mock_pipeline = MagicMock()
        mock_pipeline.execute = AsyncMock(return_value=[1, True])
        mock_redis.pipeline = MagicMock(return_value=mock_pipeline)
        
        await guard.verify_request(request_browser)

    def test_admin_jwt_claims(self):
        # Admin payload verification
        payload = {"sub": ADMIN_USERNAME, "type": "admin", "exp": int(time.time()) + 3600}
        token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
        
        decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        self.assertEqual(decoded["type"], "admin")
        self.assertEqual(decoded["sub"], ADMIN_USERNAME)

        # Non-admin token should fail type check
        user_token = jwt.encode({"sub": "student", "type": "user"}, SECRET_KEY, algorithm="HS256")
        user_decoded = jwt.decode(user_token, SECRET_KEY, algorithms=["HS256"])
        self.assertNotEqual(user_decoded.get("type"), "admin")

    def test_client_ticket_verification_with_tenant_secret(self):
        # Simulate ticket signing by worker with tenant's secret
        tenant_secret = "sk_live_sneaker_drop_991"
        session_id = "test-session-123"
        client_id = "org_flash_tix_991"

        payload = {
            "session_id": session_id,
            "client_id": client_id,
            "type": "entry_ticket",
            "exp": int(time.time() + 300)
        }
        entry_ticket = jwt.encode(payload, tenant_secret, algorithm="HS256")

        # Client code decodes using their own secret
        client_decoded = jwt.decode(entry_ticket, tenant_secret, algorithms=["HS256"])
        self.assertEqual(client_decoded["session_id"], session_id)
        self.assertEqual(client_decoded["client_id"], client_id)
        self.assertEqual(client_decoded["type"], "entry_ticket")

        # Ensure forged secret fails
        with self.assertRaises(jwt.InvalidSignatureError):
            jwt.decode(entry_ticket, "wrong_secret_key", algorithms=["HS256"])

    async def test_join_rejects_open_redirect(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import auth

        app = FastAPI()
        app.include_router(auth.router)
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
        from routers import auth

        app = FastAPI()
        app.include_router(auth.router)
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
        from routers import auth

        app = FastAPI()
        app.include_router(auth.router)
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

        # Second request with SAME token (replay attack) fails with 403
        res2 = client.post(
            "/join",
            json={"client_id": "org_flash_tix", "return_to": "http://localhost:8001/checkout"},
            headers={"X-Intent-Token": token}
        )
        self.assertEqual(res2.status_code, 403)
        self.assertIn("already been used", res2.json()["detail"])

    async def test_admin_endpoints_and_go_live(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import admin

        app = FastAPI()
        app.include_router(admin.router)
        mock_redis = AsyncMock()
        mock_redis.llen = AsyncMock(return_value=12)
        mock_redis.scard = AsyncMock(return_value=5)
        mock_redis.smembers = AsyncMock(return_value={"user-1", "user-2"})
        mock_pipeline = MagicMock()
        mock_pipeline.execute = AsyncMock(return_value=True)
        mock_redis.pipeline = MagicMock(return_value=mock_pipeline)
        mock_redis.publish = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.delete = AsyncMock()
        app.state.redis = mock_redis

        client = TestClient(app)

        # Admin token
        admin_token = jwt.encode(
            {"sub": "admin", "type": "admin", "exp": int(time.time()) + 3600},
            SECRET_KEY,
            algorithm="HS256"
        )
        headers = {"Authorization": f"Bearer {admin_token}"}

        # 1. Metrics
        metrics_res = client.get("/admin/metrics", headers=headers)
        self.assertEqual(metrics_res.status_code, 200)
        self.assertEqual(metrics_res.json()["active_sessions"], 5)

        # 2. Flush queue
        flush_res = client.post("/admin/flush/fast_lane", headers=headers)
        self.assertEqual(flush_res.status_code, 200)

        # 3. Go-live Grand Shuffle
        golive_res = client.post("/admin/events/drop_999/go-live", headers=headers)
        self.assertEqual(golive_res.status_code, 200)
        self.assertEqual(golive_res.json()["status"], "success")
        self.assertEqual(golive_res.json()["users_shuffled"], 2)

if __name__ == "__main__":
    unittest.main()
