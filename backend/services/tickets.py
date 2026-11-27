import json
import time
import jwt

from core.config import SECRET_KEY
from core.redis_utils import decode_hash


async def publish_entry_ticket(redis_client, session_id: str) -> str:
    session_info = decode_hash(await redis_client.hgetall(f"session:{session_id}"))
    client_id = session_info.get("client_id") or "default_client"
    client_config = decode_hash(await redis_client.hgetall(f"config:{client_id}"))
    client_secret = client_config.get("secret_key") or SECRET_KEY

    payload = {
        "session_id": session_id,
        "client_id": client_id,
        "type": "entry_ticket",
        "exp": int(time.time() + 300),
    }
    event_id = session_info.get("event_id")
    if event_id:
        payload["event_id"] = event_id

    entry_ticket = jwt.encode(payload, client_secret, algorithm="HS256")
    message_json = json.dumps({
        "session_id": session_id,
        "status": "cleared",
        "entry_ticket": entry_ticket,
    })
    await redis_client.setex(f"ticket:{session_id}", 60, message_json)
    await redis_client.publish("bouncer_channel", message_json)
    return entry_ticket
