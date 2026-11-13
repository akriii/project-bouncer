from typing import List
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt

from core.config import SECRET_KEY
from core.tiers import decode_hash, resolve_tiers
from routers.waiting_room import manager
from services.shuffle import execute_grand_shuffle

router = APIRouter(prefix="/admin")
security = HTTPBearer()


async def require_admin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=["HS256"])
        if payload.get("type") != "admin":
            raise HTTPException(status_code=403, detail="Forbidden: Admin privileges required")
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired session")


async def get_all_client_ids(redis) -> List[str]:
    raw_members = await redis.smembers("registered_clients")
    client_ids = set()
    for m in raw_members:
        client_ids.add(m.decode("utf-8") if isinstance(m, bytes) else m)

    # Fallback scanning if set not initialized
    if not client_ids:
        try:
            async for k in redis.scan_iter("config:*"):
                k_str = k.decode("utf-8") if isinstance(k, bytes) else k
                cid = k_str.split("config:", 1)[1]
                client_ids.add(cid)
                await redis.sadd("registered_clients", cid)
        except Exception:
            pass

    return sorted(list(client_ids))


@router.get("/metrics", dependencies=[Depends(require_admin)])
async def get_admin_metrics(request: Request):
    redis = request.app.state.redis
    client_ids = await get_all_client_ids(redis)

    tier_counts = {"tier1": 0, "tier2": 0, "tier3": 0}
    active_live_drops = 0

    for cid in client_ids:
        raw_cfg = await redis.hgetall(f"config:{cid}")
        cfg = decode_hash(raw_cfg)
        tiers = resolve_tiers(cid, cfg)
        if "tier1_queue" in tiers:
            tier_counts["tier1"] += 1
        if "tier2_waf" in tiers:
            tier_counts["tier2"] += 1
        if "tier3_lottery" in tiers:
            tier_counts["tier3"] += 1

        is_live = await redis.get(f"event_active:{cid}")
        if is_live == "true":
            active_live_drops += 1

    cluster_active = await redis.scard("bouncer_active_sessions")
    local_active = len(manager.active_connections)

    return {
        "total_clients": len(client_ids),
        "tier_counts": tier_counts,
        "active_live_drops": active_live_drops,
        "active_sessions": cluster_active if cluster_active > 0 else local_active,
    }


@router.get("/clients", dependencies=[Depends(require_admin)])
async def get_admin_clients(request: Request):
    redis = request.app.state.redis
    client_ids = await get_all_client_ids(redis)
    clients_list = []

    for cid in client_ids:
        raw_cfg = await redis.hgetall(f"config:{cid}")
        cfg = decode_hash(raw_cfg)
        tiers = resolve_tiers(cid, cfg)
        is_live = (await redis.get(f"event_active:{cid}")) == "true"
        start_time = cfg.get("start_time", "")

        if is_live:
            drop_status = "Live"
        elif start_time:
            drop_status = "Scheduled"
        else:
            drop_status = "Idle"

        active_users = await redis.scard(f"bouncer_active_sessions:{cid}")
        auth_count = await redis.llen(f"queue:{cid}:auth_queue")
        fast_count = await redis.llen(f"queue:{cid}:fast_lane")
        pool_count = await redis.scard(f"lottery_pool:{cid}")

        clients_list.append({
            "client_id": cid,
            "redirect_url": cfg.get("redirect_url", ""),
            "tiers": tiers,
            "drop_status": drop_status,
            "start_time": start_time,
            "auto_start": cfg.get("auto_start", "false").lower() == "true",
            "active_users": active_users,
            "queue_count": auth_count + fast_count,
            "holding_pool_count": pool_count,
        })

    return {"clients": clients_list}


@router.post("/clients/{client_id}/go-live", dependencies=[Depends(require_admin)])
async def trigger_client_go_live_admin(client_id: str, request: Request):
    return await execute_grand_shuffle(request.app.state.redis, client_id)


@router.post("/events/{event_id}/go-live", dependencies=[Depends(require_admin)])
async def trigger_event_go_live_admin(event_id: str, request: Request):
    return await execute_grand_shuffle(request.app.state.redis, event_id, event_id=event_id)
