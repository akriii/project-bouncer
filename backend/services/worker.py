import asyncio
import json
import jwt
import time
import os

SHARED_SECRET_KEY = os.getenv("SECRET_KEY", "super_secret_jwt_key_bouncer_2026")

# Accept the 3 arguments that main.py is passing
async def process_queue(redis_client, queue_name: str, process_rate: float):
    """Background task that clears the queue and broadcasts via Pub/Sub"""
    
    # Calculate how long to sleep based on the allowed rate
    sleep_time = 1.0 / process_rate if process_rate > 0 else 1.0

    while True:
        # 1. Pop the next user from the dynamically named queue
        next_user = await redis_client.lpop(queue_name)
        
        if next_user:
            session_id = next_user
            
            # 2. Generate their Entry Ticket JWT
            payload = {
                "session_id": session_id,
                "type": "entry_ticket",
                "exp": time.time() + 300  # 5 minutes to use the ticket
            }
            entry_ticket = jwt.encode(payload, SHARED_SECRET_KEY, algorithm="HS256")
            
            # 3. THE BROADCAST: Shout the ticket to all 3 FastAPI containers
            message = {
                "session_id": session_id,
                "status": "cleared",
                "entry_ticket": entry_ticket
            }
            await redis_client.publish("bouncer_channel", json.dumps(message))
            
        # Control the flow rate
        await asyncio.sleep(sleep_time)