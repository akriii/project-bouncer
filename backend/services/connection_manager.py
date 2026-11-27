import json
import logging
from fastapi import WebSocket
import redis.asyncio as redis
from core.config import REDIS_URL

logger = logging.getLogger("uvicorn.error")


class ConnectionManager:
    def __init__(self):
        self.active_connections = {}
        self.redis = redis.from_url(REDIS_URL, decode_responses=True)

    def set_redis(self, redis_client):
        self.redis = redis_client

    async def connect(self, session_id: str, websocket: WebSocket, lane: str, client_id: str = "default_client"):
        await websocket.accept()
        self.active_connections[session_id] = {
            "ws": websocket,
            "lane": lane,
            "client_id": client_id,
        }
        try:
            await self.redis.sadd("bouncer_active_sessions", session_id)
            await self.redis.sadd(f"bouncer_active_sessions:{client_id}", session_id)
        except Exception as e:
            logger.warning(f"Failed to record active session in Redis: {e}")

    async def disconnect(self, session_id: str):
        client_id = "default_client"
        if session_id in self.active_connections:
            client_id = self.active_connections[session_id].get("client_id", "default_client")
            del self.active_connections[session_id]
        try:
            await self.redis.srem("bouncer_active_sessions", session_id)
            await self.redis.srem(f"bouncer_active_sessions:{client_id}", session_id)
        except Exception as e:
            logger.warning(f"Failed to remove active session from Redis: {e}")

    async def listen_to_redis(self):
        pubsub = self.redis.pubsub()
        await pubsub.subscribe("bouncer_channel")

        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                except Exception:
                    continue

                msg_type = data.get("type")
                target_session = data.get("session_id")

                # Direct session message (e.g. clearance ticket)
                if target_session and target_session in self.active_connections:
                    ws = self.active_connections[target_session]["ws"]
                    try:
                        await ws.send_json(data)
                    except Exception:
                        await self.disconnect(target_session)

                # Queue progress broadcast: decrement position for waiting users in the matching client & lane
                elif msg_type == "queue_progress":
                    msg_client = data.get("client_id")
                    lane = data.get("lane")
                    progress = data.get("cleared_count", 1)
                    stale_sessions = []
                    for s_id, conn in list(self.active_connections.items()):
                        if conn.get("lane") == lane:
                            if not msg_client or conn.get("client_id") == msg_client:
                                try:
                                    await conn["ws"].send_json({
                                        "status": "waiting",
                                        "progress": progress,
                                    })
                                except Exception:
                                    stale_sessions.append(s_id)
                    for s_id in stale_sessions:
                        await self.disconnect(s_id)

                # Event live notification for holding pool users
                elif msg_type == "event_live":
                    msg_client = data.get("client_id")
                    event_id = data.get("event_id") or msg_client
                    stale_sessions = []
                    for s_id, conn in list(self.active_connections.items()):
                        if conn.get("lane") == "holding_pool":
                            if not msg_client or conn.get("client_id") == msg_client:
                                try:
                                    await conn["ws"].send_json({
                                        "status": "waiting",
                                        "message": f"Event {event_id} is now live! Entering queue...",
                                        "lane": "auth_queue",
                                    })
                                except Exception:
                                    stale_sessions.append(s_id)
                    for s_id in stale_sessions:
                        await self.disconnect(s_id)


manager = ConnectionManager()
