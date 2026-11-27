import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, HTTPException
import jwt

from core.config import SECRET_KEY, DEV_USERS
from core.redis_utils import decode_hash
from core.tiers import resolve_tiers
from core.schemas import LoginRequest
from services.scheduler import parse_start_time

router = APIRouter()


def verify_credentials(student_id: str, password: str) -> bool:
    expected = DEV_USERS.get(student_id)
    return expected is not None and secrets.compare_digest(expected, password)


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
