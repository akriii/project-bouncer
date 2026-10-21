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
                
                payload = {
                    "session_id": session_id,
                    "type": "entry_ticket",
                    "exp": time.time() + 300 
                }
                entry_ticket = jwt.encode(payload, SHARED_SECRET_KEY, algorithm="HS256")
                
                message = {
                    "session_id": session_id,
                    "status": "cleared",
                    "entry_ticket": entry_ticket
                }
                message_json = json.dumps(message)
                
                await redis_client.setex(f"ticket:{session_id}", 60, message_json)
                await redis_client.publish("bouncer_channel", message_json)
                
                print(f"WORKER BROADCASTED TICKET FOR: {session_id}", flush=True)
                
        except Exception as e:
            print(f"WORKER ERROR: {str(e)}", flush=True)
            
        await asyncio.sleep(sleep_time)