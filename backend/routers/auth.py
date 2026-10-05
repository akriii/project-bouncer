import uuid
import secrets
from datetime import datetime, timedelta, timezone
import jwt
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from core.config import SECRET_KEY, DEV_USERS

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

@router.post("/join")
async def join_queue(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    session_id = str(uuid.uuid4())
    redis = request.app.state.redis
    lane = "auth_queue"

    if credentials:
        try:
            payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=["HS256"])
            if payload.get("type") == "fast_pass":
                lane = "fast_lane"
        except jwt.PyJWTError:
            pass

    position = await redis.rpush(lane, session_id)
    return {"session_id": session_id, "lane": lane, "initial_position": position}

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