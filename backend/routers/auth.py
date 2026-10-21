import uuid
import secrets
from datetime import datetime, timedelta, timezone
import jwt
from fastapi import APIRouter, Depends, Request, HTTPException, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from core.config import SECRET_KEY, DEV_USERS, INTENT_SECRET_KEY
from core.config import SECRET_KEY, DEV_USERS
from middleware.bot_protection import BotProtection

router = APIRouter()
security = HTTPBearer(auto_error=False)

class LoginRequest(BaseModel):
    student_id: str
    password: str

def verify_credentials(student_id: str, password: str) -> bool:
    expected = DEV_USERS.get(student_id)
    return expected is not None and secrets.compare_digest(expected, password)

@router.post("/login")
async def pre_warm_login(request: LoginRequest):
    if not verify_credentials(request.student_id, request.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    payload = {
        "sub": request.student_id,
        "type": "fast_pass",
        "exp": datetime.now(timezone.utc) + timedelta(hours=48),
    }
    return {"access_token": jwt.encode(payload, SECRET_KEY, algorithm="HS256")}

from middleware.bot_protection import BotProtection

@router.post("/join")
async def join_queue(
    request: Request,
    x_intent_token: str = Header(None, alias="X-Intent-Token"),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    redis = request.app.state.redis
    
    # 1. IDENTIFY THE CLIENT
    try:
        body = await request.json()
    except Exception:
        body = {}
        
    client_id = body.get("client_id", "default_client")
    session_id = str(uuid.uuid4())
    
    # 2. FETCH CLIENT SUBSCRIPTION CONFIGURATION
    raw_config = await redis.hgetall(f"config:{client_id}")
    
    # Safely convert Redis bytes to Python strings
    client_config = {
        k.decode('utf-8') if isinstance(k, bytes) else k: 
        v.decode('utf-8') if isinstance(v, bytes) else v 
        for k, v in raw_config.items()
    }
    
    use_tier2 = client_config.get("tier2_waf") == "true"
    use_tier3 = client_config.get("tier3_lottery") == "true"

    # 3. TIER 2: RUN ANTI-BOT WAF (If Subscribed)
    if use_tier2:
        bot_guard = BotProtection(redis_client=redis, max_requests=5, window_seconds=10)
        await bot_guard.verify_request(request)

    # 4. TIER 3: JWT INTENT & LOTTERY (If Subscribed)
    lane = "auth_queue"
    
    if use_tier3:
        # Validate the Pre-Cart JWT Intent
        if not x_intent_token:
            raise HTTPException(status_code=401, detail="Missing Pre-Cart Intent Token.")
        
        try:
            intent_payload = jwt.decode(x_intent_token, INTENT_SECRET_KEY, algorithms=["HS256"])
            if intent_payload.get("type") != "pre_cart_intent":
                raise HTTPException(status_code=403, detail="Invalid token type.")
            event_id = intent_payload.get("event_id")
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Intent Token expired.")
        except jwt.PyJWTError:
            raise HTTPException(status_code=403, detail="Forged Token.")

        # Check Lottery vs Live Queue
        is_live = await redis.get(f"event_active:{event_id}")
        if is_live != "true":
            await redis.sadd(f"lottery_pool:{event_id}", session_id)
            return {
                "session_id": session_id,
                "lane": "holding_pool",
                "initial_position": "Pending Lottery",
                "event_id": event_id,
                "client_id": client_id,
                "tier": "enterprise"
            }
    
    # 5. FAST PASS CHECK (For both tiers)
    if credentials:
        try:
            payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=["HS256"])
            if payload.get("type") == "fast_pass":
                lane = "fast_lane"
        except jwt.PyJWTError:
            pass

    # 6. STANDARD FIFO QUEUE (Default execution for Tier 1, or live Tier 3)
    position = await redis.rpush(lane, session_id)
    return {
        "session_id": session_id,
        "lane": lane,
        "initial_position": position,
        "client_id": client_id,
        "tier": "enterprise" if use_tier3 else "basic"
    }
class AdminLoginRequest(BaseModel):
    username: str
    password: str

@router.post("/admin/login")
async def admin_login(request: AdminLoginRequest):
    from core.config import ADMIN_USERNAME, ADMIN_PASSWORD
    
    # Securely compare strings to prevent timing attacks
    valid_user = secrets.compare_digest(request.username, ADMIN_USERNAME)
    valid_pass = secrets.compare_digest(request.password, ADMIN_PASSWORD)
    
    if not (valid_user and valid_pass):
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    
    payload = {
        "sub": request.username,
        "type": "admin",
        "exp": datetime.now(timezone.utc) + timedelta(hours=2),
    }
    return {"access_token": jwt.encode(payload, SECRET_KEY, algorithm="HS256")}