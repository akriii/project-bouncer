from typing import Optional
from pydantic import BaseModel, Field


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
