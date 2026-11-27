import unittest
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import jwt

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from core.config import SECRET_KEY, ADMIN_USERNAME
from middleware.bot_protection import BotProtection, is_trusted_proxy


class TestSecurity(unittest.IsolatedAsyncioTestCase):

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

    async def test_bot_protection_increments_rejected_bots_counter(self):
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock()
        guard = BotProtection(redis_client=mock_redis)

        # Scraper request with client_id passed
        request_scraper = MagicMock()
        request_scraper.headers = {"user-agent": "python-requests/2.28.1"}
        request_scraper.client = MagicMock(host="142.250.190.46")

        with self.assertRaises(Exception):
            await guard.verify_request(request_scraper, client_id="client_target")

        mock_redis.incr.assert_awaited_with("metrics:client_target:rejected_bots")


if __name__ == "__main__":
    unittest.main()
