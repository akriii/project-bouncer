import asyncio
import json

from services.tickets import publish_entry_ticket

async def process_queue(redis_client, queue_name: str, process_rate: float):
    print(f"WORKER STARTED for {queue_name} at {process_rate}/sec", flush=True)
    sleep_time = 1.0 / process_rate if process_rate > 0 else 1.0

    while True:
        try:
            next_user = await redis_client.lpop(queue_name)
            
            if next_user:
                session_id = next_user.decode("utf-8") if isinstance(next_user, bytes) else next_user
                print(f"✅ WORKER POPPED USER: {session_id}", flush=True)
                await publish_entry_ticket(redis_client, session_id)

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
