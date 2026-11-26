import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from pydantic import BaseModel, Field

from core.config import SECRET_KEY, VALID_LANES
from core.tiers import decode_hash, resolve_tiers, TIER_FLAGS
from services.shuffle import execute_grand_shuffle
from services.scheduler import compute_drop_stage

router = APIRouter(prefix="/client")
security = HTTPBearer()


class ClientLoginRequest(BaseModel):
    client_id: str
    secret_key: str


class ClientConfigUpdateRequest(BaseModel):
    redirect_url: Optional[str] = None
    allowed_origins: Optional[str] = None
    start_time: Optional[str] = None
    auto_start: Optional[bool] = None
    process_rate: Optional[float] = None
    pre_auth_window_hours: Optional[int] = Field(
        None, ge=1, le=120, description="Pre-auth collection window in hours before drop start (1-120 hrs, max 5 days)"
    )


async def require_client(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=["HS256"])
        if payload.get("type") != "client" or not payload.get("client_id"):
            raise HTTPException(status_code=403, detail="Forbidden: Client credentials required")
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired client session")


@router.post("/login")
async def client_login(req: ClientLoginRequest, request: Request):
    redis = request.app.state.redis
    raw_cfg = await redis.hgetall(f"config:{req.client_id}")

    if not raw_cfg and req.client_id != "default_client":
        raise HTTPException(status_code=404, detail="Client not found")

    cfg = decode_hash(raw_cfg)
    expected_secret = cfg.get("secret_key") or SECRET_KEY

    if not secrets.compare_digest(req.secret_key, expected_secret):
        raise HTTPException(status_code=401, detail="Invalid client secret key")

    payload = {
        "sub": req.client_id,
        "client_id": req.client_id,
        "type": "client",
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
    }
    return {
        "access_token": jwt.encode(payload, SECRET_KEY, algorithm="HS256"),
        "client_id": req.client_id,
    }


@router.get("/config")
async def get_client_config(request: Request, client_data: dict = Depends(require_client)):
    client_id = client_data["client_id"]
    redis = request.app.state.redis

    raw_cfg = await redis.hgetall(f"config:{client_id}")
    cfg = decode_hash(raw_cfg)
    is_live = (await redis.get(f"event_active:{client_id}")) == "true"
    tiers = resolve_tiers(client_id, cfg)
    stage_info = compute_drop_stage(cfg, is_live)

    return {
        "client_id": client_id,
        "redirect_url": cfg.get("redirect_url", "http://localhost:8001/checkout"),
        "allowed_origins": cfg.get("allowed_origins", ""),
        "start_time": cfg.get("start_time", ""),
        "auto_start": cfg.get("auto_start", "false").lower() == "true",
        "process_rate": float(cfg.get("process_rate", 20.0)),
        "pre_auth_window_hours": stage_info["pre_auth_window_hours"],
        "pre_auth_opens_at": stage_info["pre_auth_opens_at"],
        "drop_stage": stage_info["stage"],
        "is_live": is_live,
        "tiers": tiers,
        "tier_flags": {f: cfg.get(f, "false").lower() == "true" for f in TIER_FLAGS},
    }


@router.put("/config")
async def update_client_config(
    update_data: ClientConfigUpdateRequest,
    request: Request,
    client_data: dict = Depends(require_client),
):
    client_id = client_data["client_id"]
    redis = request.app.state.redis

    mapping = {}
    if update_data.redirect_url is not None:
        mapping["redirect_url"] = update_data.redirect_url
    if update_data.allowed_origins is not None:
        mapping["allowed_origins"] = update_data.allowed_origins
    if update_data.start_time is not None:
        mapping["start_time"] = update_data.start_time
    if update_data.auto_start is not None:
        mapping["auto_start"] = "true" if update_data.auto_start else "false"
    if update_data.process_rate is not None:
        mapping["process_rate"] = str(update_data.process_rate)
    if update_data.pre_auth_window_hours is not None:
        mapping["pre_auth_window_hours"] = str(update_data.pre_auth_window_hours)

    if mapping:
        await redis.hset(f"config:{client_id}", mapping=mapping)
        await redis.sadd("registered_clients", client_id)

    return await get_client_config(request, client_data)


@router.post("/go-live")
async def trigger_client_go_live(request: Request, client_data: dict = Depends(require_client)):
    client_id = client_data["client_id"]
    redis = request.app.state.redis
    result = await execute_grand_shuffle(redis, client_id)
    return result


@router.post("/flush/{lane}")
async def emergency_client_flush(lane: str, request: Request, client_data: dict = Depends(require_client)):
    client_id = client_data["client_id"]
    if lane not in VALID_LANES:
        raise HTTPException(status_code=400, detail="Invalid lane")

    redis = request.app.state.redis
    if lane == "holding_pool":
        await redis.delete(f"lottery_pool:{client_id}")
    else:
        # Flush only this client's queue
        await redis.delete(f"queue:{client_id}:{lane}")

    return {"message": f"Successfully cleared {lane} for client {client_id}"}


@router.get("/metrics")
async def get_client_metrics(request: Request, client_data: dict = Depends(require_client)):
    client_id = client_data["client_id"]
    redis = request.app.state.redis

    raw_cfg = await redis.hgetall(f"config:{client_id}")
    cfg = decode_hash(raw_cfg)
    is_live = (await redis.get(f"event_active:{client_id}")) == "true"
    stage_info = compute_drop_stage(cfg, is_live)

    holding_pool_count = await redis.scard(f"lottery_pool:{client_id}")
    auth_queue_count = await redis.llen(f"queue:{client_id}:auth_queue")
    fast_lane_count = await redis.llen(f"queue:{client_id}:fast_lane")

    def safe_int(v, default=0):
        try:
            return int(v)
        except (ValueError, TypeError):
            return default

    rejected_bots = safe_int(await redis.get(f"metrics:{client_id}:rejected_bots"))
    admitted_users = safe_int(await redis.get(f"metrics:{client_id}:admitted_users"))
    active_sessions = await redis.scard(f"bouncer_active_sessions:{client_id}")

    return {
        "client_id": client_id,
        "holding_pool_count": holding_pool_count,
        "auth_queue_count": auth_queue_count,
        "fast_lane_count": fast_lane_count,
        "queue_count": auth_queue_count + fast_lane_count,
        "rejected_bots": rejected_bots,
        "admitted_users": admitted_users,
        "active_sessions": active_sessions,
        "is_live": is_live,
        "drop_stage": stage_info["stage"],
        "pre_auth_window_hours": stage_info["pre_auth_window_hours"],
        "pre_auth_opens_at": stage_info["pre_auth_opens_at"],
    }
