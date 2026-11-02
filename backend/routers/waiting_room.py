import asyncio
import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import redis.asyncio as redis
from core.config import REDIS_URL

logger = logging.getLogger("uvicorn.error")

router = APIRouter()

class ConnectionManager:
    def __init__(self):
        self.active_connections = {}
        self.redis = redis.from_url(REDIS_URL, decode_responses=True)

    def set_redis(self, redis_client):
        self.redis = redis_client

    async def connect(self, session_id: str, websocket: WebSocket, lane: str):
        await websocket.accept()
        self.active_connections[session_id] = {"ws": websocket, "lane": lane}
        try:
            await self.redis.sadd("bouncer_active_sessions", session_id)
        except Exception as e:
            logger.warning(f"Failed to record active session in Redis: {e}")

    async def disconnect(self, session_id: str):
        if session_id in self.active_connections:
            del self.active_connections[session_id]
        try:
            await self.redis.srem("bouncer_active_sessions", session_id)
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
                
                # Queue progress broadcast: decrement position for waiting users in the lane
                elif msg_type == "queue_progress":
                    lane = data.get("lane")
                    progress = data.get("cleared_count", 1)
                    stale_sessions = []
                    for s_id, conn in list(self.active_connections.items()):
                        if conn.get("lane") == lane:
                            try:
                                await conn["ws"].send_json({
                                    "status": "waiting",
                                    "progress": progress
                                })
                            except Exception:
                                stale_sessions.append(s_id)
                    for s_id in stale_sessions:
                        await self.disconnect(s_id)

                # Event live notification for holding pool users
                elif msg_type == "event_live":
                    event_id = data.get("event_id")
                    stale_sessions = []
                    for s_id, conn in list(self.active_connections.items()):
                        if conn.get("lane") == "holding_pool":
                            try:
                                await conn["ws"].send_json({
                                    "status": "waiting",
                                    "message": f"Event {event_id} is now live! Entering queue...",
                                    "lane": "auth_queue"
                                })
                            except Exception:
                                stale_sessions.append(s_id)
                    for s_id in stale_sessions:
                        await self.disconnect(s_id)

manager = ConnectionManager()

@router.websocket("/ws/waiting-room/{lane}/{session_id}")
async def waiting_room_ws(websocket: WebSocket, lane: str, session_id: str):
    await manager.connect(session_id, websocket, lane)
    print(f"🔌 WEBSOCKET CONNECTED FOR: {session_id} (lane: {lane})", flush=True)
    
    try:
        # 1. Check if an entry ticket was already generated
        missed_message = await manager.redis.get(f"ticket:{session_id}")
        if missed_message:
            if isinstance(missed_message, bytes):
                missed_message = missed_message.decode("utf-8")
            await websocket.send_text(missed_message)
        else:
            # 2. Send current position on initial connection
            if lane == "holding_pool":
                await websocket.send_json({
                    "status": "waiting",
                    "position": "Pending Lottery",
                    "lane": "holding_pool"
                })
            else:
                pos = await manager.redis.lpos(lane, session_id)
                initial_pos = (pos + 1) if pos is not None else 1
                await websocket.send_json({
                    "status": "waiting",
                    "position": initial_pos,
                    "lane": lane
                })
            
        while True:
            # Keep socket open and accept any client pings
            await websocket.receive_text()
            
    except WebSocketDisconnect:
        await manager.disconnect(session_id)
    except Exception as e:
        print(f"WEBSOCKET ERROR FOR {session_id}: {str(e)}", flush=True)
        await manager.disconnect(session_id)
