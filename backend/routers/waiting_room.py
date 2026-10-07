import asyncio
import json
from contextlib import asynccontextmanager
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import redis.asyncio as redis

redis_client = redis.from_url("redis://redis:6379", decode_responses=True)

class ConnectionManager:
    def __init__(self):
        self.active_connections = {}

    async def connect(self, session_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[session_id] = websocket

    def disconnect(self, session_id: str):
        if session_id in self.active_connections:
            del self.active_connections[session_id]

    async def listen_to_redis(self):
        pubsub = redis_client.pubsub()
        await pubsub.subscribe("bouncer_channel")
        
        async for message in pubsub.listen():
            if message["type"] == "message":
                data = json.loads(message["data"])
                target_session = data.get("session_id")
                
                if target_session in self.active_connections:
                    ws = self.active_connections[target_session]
                    try:
                        await ws.send_json(data)
                    except Exception:
                        self.disconnect(target_session)

manager = ConnectionManager()

@asynccontextmanager
async def router_lifespan(router: APIRouter):
    # Startup: Create the background task
    listener_task = asyncio.create_task(manager.listen_to_redis())
    yield
    # Shutdown: Cleanly cancel the task when the server stops
    listener_task.cancel()

# Pass the lifespan function when creating the router
router = APIRouter(lifespan=router_lifespan)

@router.websocket("/ws/waiting-room/{lane}/{session_id}")
async def waiting_room_ws(websocket: WebSocket, lane: str, session_id: str):
    await manager.connect(session_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(session_id)