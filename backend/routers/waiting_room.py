import asyncio
from fastapi import APIRouter, WebSocket
from core.config import VALID_LANES

router = APIRouter()

# Shared state to track active connections across the app
active_websockets: set[WebSocket] = set()

@router.websocket("/ws/waiting-room/{lane}/{session_id}")
async def waiting_room(websocket: WebSocket, lane: str, session_id: str):
    await websocket.accept()

    if lane not in VALID_LANES:
        await websocket.send_json({"status": "error", "message": "Invalid lane."})
        await websocket.close()
        return

    active_websockets.add(websocket)
    redis = websocket.app.state.redis

    async def send_cleared(ticket: str):
        await websocket.send_json({
            "status": "cleared",
            "message": "It is your turn!",
            "entry_ticket": ticket,
            "redirect_url": f"https://results.university.edu?token={ticket}",
        })
        await websocket.close()

    try:
        while True:
            ticket = await redis.get(f"ticket:{session_id}")
            if ticket:
                await send_cleared(ticket)
                break

            pos = await redis.lpos(lane, session_id)
            if pos is not None:
                await websocket.send_json({"status": "waiting", "position": pos + 1})
            else:
                await asyncio.sleep(0.3)
                ticket = await redis.get(f"ticket:{session_id}")
                if ticket:
                    await send_cleared(ticket)
                else:
                    await websocket.send_json({
                        "status": "expired",
                        "message": "Your place in line is no longer valid. Please rejoin the queue.",
                    })
                    await websocket.close()
                break

            await asyncio.sleep(1)
    except Exception:
        pass
    finally:
        active_websockets.discard(websocket)