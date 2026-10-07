from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt

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