import uuid
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse
import jwt
from fastapi import APIRouter, Depends, Request, HTTPException, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from core.config import SECRET_KEY, DEV_USERS, INTENT_SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD
from core.tiers import decode_hash, resolve_tiers
from middleware.bot_protection import BotProtection
from services.tickets import publish_entry_ticket
from services.scheduler import parse_start_time

router = APIRouter()
security = HTTPBearer(auto_error=False)

class LoginRequest(BaseModel):
    student_id: str
    password: str
    client_id: Optional[str] = "default_client"

class JoinRequest(BaseModel):
    client_id: str = "default_client"
    return_to: Optional[str] = None

class AdminLoginRequest(BaseModel):
    username: str
    password: str

def verify_credentials(student_id: str, password: str) -> bool:
    expected = DEV_USERS.get(student_id)
    return expected is not None and secrets.compare_digest(expected, password)

def is_safe_return_url(return_url: str, allowed_origins_str: Optional[str]) -> bool:
    try:
        parsed = urlparse(return_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return False
        
        # Local development defaults
        if not allowed_origins_str:
            hostname = parsed.hostname or ""
            return hostname in ("localhost", "127.0.0.1")
        
        allowed_list = [o.strip().lower() for o in allowed_origins_str.split(",") if o.strip()]
        target_origin = f"{parsed.scheme}://{parsed.netloc}".lower()
        target_host = (parsed.hostname or "").lower()
        
        return any(
            allowed == target_origin or allowed == target_host or allowed == "*"
            for allowed in allowed_list
        )
    except Exception:
        return False

@router.post("/login")
async def pre_warm_login(login_req: LoginRequest, request: Request):
    if not verify_credentials(login_req.student_id, login_req.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    client_id = login_req.client_id or "default_client"
    redis = request.app.state.redis
    raw_cfg = await redis.hgetall(f"config:{client_id}")
    cfg = decode_hash(raw_cfg)
    tiers = resolve_tiers(client_id, cfg)

    # If client subscribed to Tier 1, enforce pre-auth collection window
    if "tier1_queue" in tiers:
        start_time_val = cfg.get("start_time")
        if start_time_val:
            try:
                start_ts = parse_start_time(start_time_val)
                pre_auth_hours = int(cfg.get("pre_auth_window_hours", 48))
                window_opens_ts = start_ts - (pre_auth_hours * 3600)
                now_ts = datetime.now(timezone.utc).timestamp()

                if now_ts < window_opens_ts:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Pre-authentication window is not open yet. It opens {pre_auth_hours} hours before drop start.",
                    )
            except HTTPException:
                raise
            except Exception:
                pass

    payload = {
        "sub": login_req.student_id,
        "client_id": client_id,
        "type": "fast_pass",
        "exp": datetime.now(timezone.utc) + timedelta(hours=48),
    }
    return {"access_token": jwt.encode(payload, SECRET_KEY, algorithm="HS256")}

@router.post("/join")
async def join_queue(
    request: Request,
    body: Optional[JoinRequest] = None,
    x_intent_token: str = Header(None, alias="X-Intent-Token"),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    redis = request.app.state.redis
    
    # 1. PARSE REQUEST DATA
    if body is None:
        try:
            raw_body = await request.json()
            body = JoinRequest(**raw_body)
        except Exception:
            body = JoinRequest()

    client_id = body.client_id
    session_id = str(uuid.uuid4())
    
    # 2. FETCH CLIENT SUBSCRIPTION CONFIGURATION
    raw_config = await redis.hgetall(f"config:{client_id}")
    if not raw_config and client_id != "default_client":
        raise HTTPException(status_code=404, detail="Invalid or unregistered client_id")

    client_config = decode_hash(raw_config)
    await redis.sadd("registered_clients", client_id)

    # 3. VALIDATE RETURN URL (Prevent Open Redirect / Ticket Theft)
    return_to = body.return_to or client_config.get("redirect_url") or "http://localhost:8001/checkout"
    allowed_origins = client_config.get("allowed_origins")
    if not is_safe_return_url(return_to, allowed_origins):
        raise HTTPException(status_code=400, detail="Invalid or unauthorized return_to URL")

    tiers = resolve_tiers(client_id, client_config)
    if not tiers:
        raise HTTPException(status_code=403, detail="Client has no active subscription tiers.")

    # 4. TIER 2: RUN ANTI-BOT WAF (If Subscribed)
    if "tier2_waf" in tiers:
        bot_guard = BotProtection(redis_client=redis, max_requests=5, window_seconds=10)
        await bot_guard.verify_request(request, client_id=client_id)

    # 5. TIER 3: JWT INTENT & LOTTERY (If Subscribed)
    event_id = None

    if "tier3_lottery" in tiers:
        if not x_intent_token:
            raise HTTPException(status_code=401, detail="Missing Pre-Cart Intent Token.")
        
        try:
            intent_payload = jwt.decode(x_intent_token, INTENT_SECRET_KEY, algorithms=["HS256"])
            if intent_payload.get("type") != "pre_cart_intent":
                raise HTTPException(status_code=403, detail="Invalid token type.")
            
            # Check client_id alignment if provided in token
            token_client = intent_payload.get("client_id")
            if token_client and token_client != client_id:
                raise HTTPException(status_code=403, detail="Intent Token does not match client_id.")

            # Single-use Nonce (jti) check to prevent replay attacks
            jti = intent_payload.get("jti")
            if jti:
                is_fresh = await redis.set(f"intent_jti:{jti}", "1", ex=3600, nx=True)
                if not is_fresh:
                    raise HTTPException(status_code=403, detail="Intent Token has already been used.")

            event_id = intent_payload.get("event_id")
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Intent Token expired.")
        except jwt.PyJWTError:
            raise HTTPException(status_code=403, detail="Forged Token.")

        # Check Lottery vs Live Queue (client drop status)
        is_live = await redis.get(f"event_active:{client_id}")
        if is_live != "true" and event_id:
            is_live = await redis.get(f"event_active:{event_id}")

        if is_live != "true":
            lane = "holding_pool"
            await redis.sadd(f"lottery_pool:{client_id}", session_id)
            if event_id and event_id != client_id:
                await redis.sadd(f"lottery_pool:{event_id}", session_id)
            
            session_data = {
                "client_id": client_id,
                "lane": lane,
                "event_id": event_id or "",
                "return_to": return_to
            }
            await redis.hset(f"session:{session_id}", mapping=session_data)
            await redis.expire(f"session:{session_id}", 3600)

            return {
                "session_id": session_id,
                "lane": "holding_pool",
                "access": "holding_pool",
                "initial_position": "Pending Lottery",
                "event_id": event_id,
                "client_id": client_id,
                "validated_return_to": return_to,
                "tiers": tiers,
            }

    # 6. TIER 1: WAITING ROOM. Fast pass is part of this queue, not a separate product.
    if "tier1_queue" in tiers:
        lane = "auth_queue"
        if credentials:
            try:
                payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=["HS256"])
                if payload.get("type") == "fast_pass":
                    token_client = payload.get("client_id")
                    # Tenant isolation: fast pass from Client A cannot be used on Client B
                    if not token_client or token_client == client_id:
                        lane = "fast_lane"
            except jwt.PyJWTError:
                pass

        session_data = {
            "client_id": client_id,
            "lane": lane,
            "event_id": event_id or "",
            "return_to": return_to,
        }
        await redis.hset(f"session:{session_id}", mapping=session_data)
        await redis.expire(f"session:{session_id}", 3600)
        position = await redis.rpush(f"queue:{client_id}:{lane}", session_id)
        return {
            "session_id": session_id,
            "lane": lane,
            "access": "queue",
            "initial_position": position,
            "client_id": client_id,
            "validated_return_to": return_to,
            "tiers": tiers,
        }

    # 7. SUBSCRIPTION WITHOUT THE WAITING ROOM. Shield and/or a live lottery already ran.
    session_data = {
        "client_id": client_id,
        "lane": "direct",
        "event_id": event_id or "",
        "return_to": return_to,
    }
    await redis.hset(f"session:{session_id}", mapping=session_data)
    await redis.expire(f"session:{session_id}", 3600)
    entry_ticket = await publish_entry_ticket(redis, session_id)
    return {
        "session_id": session_id,
        "lane": "direct",
        "access": "direct",
        "entry_ticket": entry_ticket,
        "client_id": client_id,
        "validated_return_to": return_to,
        "tiers": tiers,
    }

@router.post("/admin/login")
async def admin_login(request: AdminLoginRequest):
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
