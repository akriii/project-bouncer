import asyncio
import json
import jwt
import time
import os

SHARED_SECRET_KEY = os.getenv("SECRET_KEY", "super_secret_jwt_key_bouncer_2026")

async def process_queue(redis_client, queue_name: str, process_rate: float):
    print(f"WORKER STARTED for {queue_name} at {process_rate}/sec", flush=True)
    sleep_time = 1.0 / process_rate if process_rate > 0 else 1.0

    while True:
        try:
            next_user = await redis_client.lpop(queue_name)
            
            if next_user:
                session_id = next_user.decode("utf-8") if isinstance(next_user, bytes) else next_user
                print(f"✅ WORKER POPPED USER: {session_id}", flush=True)
                
                # Fetch session details stored during /join
                session_info = await redis_client.hgetall(f"session:{session_id}")
                client_id = session_info.get("client_id", "default_client") if session_info else "default_client"

                # Fetch tenant-specific secret if configured
                client_config = await redis_client.hgetall(f"config:{client_id}")
                client_secret = client_config.get("secret_key") or SHARED_SECRET_KEY

                payload = {
                    "session_id": session_id,
                    "client_id": client_id,
                    "type": "entry_ticket",
                    "exp": int(time.time() + 300)
                }
                if session_info and "event_id" in session_info:
                    payload["event_id"] = session_info["event_id"]

                entry_ticket = jwt.encode(payload, client_secret, algorithm="HS256")
                
                # Direct clearance message to the specific user
                message = {
                    "session_id": session_id,
                    "status": "cleared",
                    "entry_ticket": entry_ticket
                }
                message_json = json.dumps(message)
                
                await redis_client.setex(f"ticket:{session_id}", 60, message_json)
                await redis_client.publish("bouncer_channel", message_json)
                
                # Broadcast progress update so waiting users can decrease their queue position
                progress_broadcast = json.dumps({
                    "type": "queue_progress",
                    "lane": queue_name,
                    "cleared_count": 1
                })
                await redis_client.publish("bouncer_channel", progress_broadcast)
                
                print(f"WORKER BROADCASTED TICKET FOR: {session_id}", flush=True)
                
        except Exception as e:
            print(f"WORKER ERROR: {str(e)}", flush=True)
            
        await asyncio.sleep(sleep_time)
