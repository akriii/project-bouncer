import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import jwt
import time

# Ensure backend root is on the path for all tests
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from core.config import SECRET_KEY


def make_admin_token():
    """Generate a valid admin JWT token for testing."""
    return jwt.encode(
        {"sub": "admin", "type": "admin", "exp": int(time.time()) + 3600},
        SECRET_KEY,
        algorithm="HS256",
    )


def make_client_token(client_id: str):
    """Generate a valid client JWT token for testing."""
    return jwt.encode(
        {"sub": client_id, "client_id": client_id, "type": "client"},
        SECRET_KEY,
        algorithm="HS256",
    )


def make_mock_redis(**overrides):
    """Create a pre-configured AsyncMock Redis client for testing."""
    mock = AsyncMock()
    mock.hgetall = overrides.get("hgetall", AsyncMock(return_value={}))
    mock.get = overrides.get("get", AsyncMock(return_value=None))
    mock.set = overrides.get("set", AsyncMock(return_value=True))
    mock.sadd = overrides.get("sadd", AsyncMock())
    mock.scard = overrides.get("scard", AsyncMock(return_value=0))
    mock.llen = overrides.get("llen", AsyncMock(return_value=0))
    mock.smembers = overrides.get("smembers", AsyncMock(return_value=set()))
    mock.hset = overrides.get("hset", AsyncMock())
    mock.expire = overrides.get("expire", AsyncMock())
    mock.delete = overrides.get("delete", AsyncMock())
    mock.rpush = overrides.get("rpush", AsyncMock(return_value=1))
    mock.setex = overrides.get("setex", AsyncMock())
    mock.publish = overrides.get("publish", AsyncMock())
    mock.incr = overrides.get("incr", AsyncMock())

    mock_pipeline = MagicMock()
    mock_pipeline.execute = AsyncMock(return_value=[1, True])
    mock.pipeline = MagicMock(return_value=mock_pipeline)

    return mock
