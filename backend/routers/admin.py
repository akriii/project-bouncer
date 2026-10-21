from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import random

from core.config import SECRET_KEY, VALID_LANES
from routers.waiting_room import manager

router = APIRouter(prefix="/admin")
security = HTTPBearer()

async def require_admin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=["HS256"])
        if payload.get("type") != "admin":
            raise HTTPException(status_code=403, detail="Forbidden: Admin privileges required")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

@router.get("/metrics", dependencies=[Depends(require_admin)])
async def get_admin_metrics(request: Request):
    redis = request.app.state.redis
    return {
        "fast_lane_count": await redis.llen("fast_lane"),
        "auth_queue_count": await redis.llen("auth_queue"),
        "active_sessions": len(manager.active_connections),
    }

@router.post("/flush/{lane}", dependencies=[Depends(require_admin)])
async def flush_queue(lane: str, request: Request):
    if lane not in VALID_LANES:
        raise HTTPException(status_code=400, detail="Invalid lane")
    await request.app.state.redis.delete(lane)
    return {"message": f"Successfully cleared {lane}"}

@router.post("/admin/events/{event_id}/go-live")
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
    
    # 3. PIPELINE: Push shuffled users to the active queue and destroy the holding pool
    pipeline = redis.pipeline()
    pipeline.rpush("auth_queue", *waiting_users)
    pipeline.delete(pool_key)
    await pipeline.execute()
    
    # 4. FLIP THE SWITCH: Future users bypass the lottery
    await redis.set(f"event_active:{event_id}", "true")
    
    return {
        "status": "success",
        "message": f"Event {event_id} is now LIVE.",
        "users_shuffled": len(waiting_users)
    }