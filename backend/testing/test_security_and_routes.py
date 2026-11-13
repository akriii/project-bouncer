import json
import unittest
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import jwt
import time
from datetime import datetime, timezone

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from core.config import SECRET_KEY, INTENT_SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD
from core.tiers import resolve_tiers
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

    def test_resolve_tiers_is_a_mix_not_a_ladder(self):
        self.assertEqual(resolve_tiers("org", {"tier2_waf": "true"}), ["tier2_waf"])
        self.assertEqual(resolve_tiers("org", {"tier3_lottery": "true"}), ["tier3_lottery"])
        self.assertEqual(
            resolve_tiers("org", {"tier1_queue": "true", "tier3_lottery": "true"}),
            ["tier1_queue", "tier3_lottery"],
        )
        self.assertEqual(
            resolve_tiers("org", {"tier1_queue": "true", "tier2_waf": "true", "tier3_lottery": "true"}),
            ["tier1_queue", "tier2_waf", "tier3_lottery"],
        )
        self.assertEqual(resolve_tiers("default_client", {}), ["tier1_queue"])
        self.assertEqual(resolve_tiers("default_client", {"tier2_waf": "true"}), ["tier2_waf"])
        self.assertEqual(resolve_tiers("org", {"secret_key": "x"}), [])

    async def test_join_follows_subscribed_tier_mix(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import auth

        app = FastAPI()
        app.include_router(auth.router)

        def make_redis(config):
            mock_redis = AsyncMock()
            mock_redis.rpush = AsyncMock(return_value=4)
            mock_redis.hset = AsyncMock()
            mock_redis.expire = AsyncMock()
            mock_redis.setex = AsyncMock()
            mock_redis.publish = AsyncMock()
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

    def test_auto_start_scheduler_parsing(self):
        from services.scheduler import parse_start_time, compute_drop_stage

        # 1. Unix timestamp string
        self.assertEqual(parse_start_time("1700000000"), 1700000000.0)

        # 2. ISO 8601 string
        parsed_iso = parse_start_time("2026-11-13T10:00:00Z")
        self.assertGreater(parsed_iso, 1700000000)

        # 3. Drop stage computation
        cfg = {"start_time": "2026-11-13T10:00:00Z", "pre_auth_window_hours": "48"}
        # Far before pre-auth window:
        stage_before = compute_drop_stage(cfg, is_live=False, now_ts=parsed_iso - (50 * 3600))
        self.assertEqual(stage_before["stage"], "Scheduled (Pre-Auth Closed)")
        # Inside pre-auth window:
        stage_inside = compute_drop_stage(cfg, is_live=False, now_ts=parsed_iso - (10 * 3600))
        self.assertEqual(stage_inside["stage"], "Pre-Auth Open")
        # Ready:
        stage_ready = compute_drop_stage(cfg, is_live=False, now_ts=parsed_iso + 10)
        self.assertEqual(stage_ready["stage"], "Ready (Pending Go-Live)")
        # Live:
        stage_live = compute_drop_stage(cfg, is_live=True)
        self.assertEqual(stage_live["stage"], "Live")

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

    async def test_pre_auth_login_window_rejection(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import auth

        app = FastAPI()
        app.include_router(auth.router)
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
        from routers import auth

        app = FastAPI()
        app.include_router(auth.router)
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
