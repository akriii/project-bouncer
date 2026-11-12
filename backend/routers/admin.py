import json
import random
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt

from core.config import SECRET_KEY, VALID_LANES
from core.tiers import decode_hash, resolve_tiers
from routers.waiting_room import manager
from services.tickets import publish_entry_ticket

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

@router.get("/metrics", dependencies=[Depends(require_admin)])
async def get_admin_metrics(request: Request):
    redis = request.app.state.redis
    cluster_active = await redis.scard("bouncer_active_sessions")
    local_active = len(manager.active_connections)
    return {
        "fast_lane_count": await redis.llen("fast_lane"),
        "auth_queue_count": await redis.llen("auth_queue"),
        "active_sessions": cluster_active if cluster_active > 0 else local_active,
    }

@router.post("/flush/{lane}", dependencies=[Depends(require_admin)])
async def flush_queue(lane: str, request: Request):
    if lane not in VALID_LANES:
        raise HTTPException(status_code=400, detail="Invalid lane")
    await request.app.state.redis.delete(lane)
    return {"message": f"Successfully cleared {lane}"}

@router.post("/events/{event_id}/go-live")
async def trigger_lottery_and_go_live(
    event_id: str,
    request: Request,
    admin_data: dict = Depends(require_admin)
):
    redis = request.app.state.redis
    pool_key = f"lottery_pool:{event_id}"
    
    # 1. Grab everyone from the holding pool
    waiting_users = await redis.smembers(pool_key)
    waiting_users = list(waiting_users)
    
    if not waiting_users:
        # If the pool is empty, just mark the event live
        await redis.set(f"event_active:{event_id}", "true")
        return {"status": "success", "message": f"Event {event_id} is live (0 users in pool)."}

    # 2. THE SHUFFLE (Scramble the list)
    random.shuffle(waiting_users)

    # 3. Only clients who bought the waiting room enter the FIFO.
    #    Everyone else is released in shuffled order with a ticket.
    queued = []
    direct = []
    for session_id in waiting_users:
        session = decode_hash(await redis.hgetall(f"session:{session_id}"))
        client_id = session.get("client_id") or "default_client"
        client_config = decode_hash(await redis.hgetall(f"config:{client_id}"))
        if "tier1_queue" in resolve_tiers(client_id, client_config):
            queued.append(session_id)
        else:
            direct.append(session_id)

    pipeline = redis.pipeline()
    if queued:
        pipeline.rpush("auth_queue", *queued)
    pipeline.delete(pool_key)
    await pipeline.execute()
    
    # 4. FLIP THE SWITCH: Future users bypass the lottery
    await redis.set(f"event_active:{event_id}", "true")
    
    # 5. Broadcast to WebSockets that event is live
    await redis.publish("bouncer_channel", json.dumps({
        "type": "event_live",
        "event_id": event_id
    }))

    for session_id in direct:
        await publish_entry_ticket(redis, session_id)

    return {
        "status": "success",
        "message": f"Event {event_id} is now LIVE.",
        "users_shuffled": len(waiting_users),
        "users_queued": len(queued),
        "users_released": len(direct),
    }
