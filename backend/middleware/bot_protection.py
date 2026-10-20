import time
from fastapi import Request, HTTPException, status
import redis.asyncio as redis

# Known headless scraper / bot user-agents
BLOCKED_USER_AGENTS = [
    "python-requests",
    "aiohttp",
    "curl",
    "postmanruntime",
    "go-http-client",
    "scrapy",
    "headlesschrome",
    "puppeteer"
]

class BotProtection:
    def __init__(self, redis_client: redis.Redis, max_requests: int = 5, window_seconds: int = 10):
        self.redis = redis_client
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def verify_request(self, request: Request):
        # 1. Header Validation (Scraper Check)
        user_agent = request.headers.get("user-agent", "").lower()
        if not user_agent or any(bot in user_agent for bot in BLOCKED_USER_AGENTS):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bot signature detected. Access denied."
            )

        # Ensure request originates from a real browser context
        sec_fetch_mode = request.headers.get("sec-fetch-mode")
        if sec_fetch_mode and sec_fetch_mode not in ["navigate", "cors", "same-origin"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid request context."
            )

        # 2. IP Rate Limiting (Redis Sliding Window)
        client_ip = request.client.host if request.client else "unknown"
        # Respect Nginx real IP header if present
        if "x-real-ip" in request.headers:
            client_ip = request.headers["x-real-ip"]

        current_time = int(time.time())
        key = f"rate_limit:{client_ip}:{current_time // self.window_seconds}"

        # Increment count for current window
        request_count = await self.redis.incr(key)
        if request_count == 1:
            await self.redis.expire(key, self.window_seconds)

        if request_count > self.max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many request attempts. Scalper rate-limit triggered."
            )