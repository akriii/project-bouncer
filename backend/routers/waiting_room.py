from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from core.redis_utils import decode_hash
from services.connection_manager import manager

router = APIRouter()


@router.websocket("/ws/waiting-room/{lane}/{session_id}")
async def waiting_room_ws(websocket: WebSocket, lane: str, session_id: str):
    # Lookup session info from Redis to determine tenant client_id
    raw_session = await manager.redis.hgetall(f"session:{session_id}")
    session_info = decode_hash(raw_session)
    client_id = session_info.get("client_id") or "default_client"

    await manager.connect(session_id, websocket, lane, client_id=client_id)
    print(f"🔌 WEBSOCKET CONNECTED FOR: {session_id} (client: {client_id}, lane: {lane})", flush=True)

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
                    "lane": "holding_pool",
                    "client_id": client_id,
                })
            else:
                pos = await manager.redis.lpos(f"queue:{client_id}:{lane}", session_id)
                if pos is None:
                    pos = await manager.redis.lpos(lane, session_id)
                initial_pos = (pos + 1) if pos is not None else 1
                await websocket.send_json({
                    "status": "waiting",
                    "position": initial_pos,
                    "lane": lane,
                    "client_id": client_id,
                })

        while True:
            # Keep socket open and accept any client pings
            await websocket.receive_text()

    except WebSocketDisconnect:
        await manager.disconnect(session_id)
    except Exception as e:
        print(f"WEBSOCKET ERROR FOR {session_id}: {str(e)}", flush=True)
        await manager.disconnect(session_id)
