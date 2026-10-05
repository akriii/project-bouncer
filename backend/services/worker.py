import asyncio
from datetime import datetime, timedelta, timezone
import jwt
from core.config import SECRET_KEY

async def process_queue(redis, queue_name: str, target_rate_per_sec: float):
    ticks_per_sec = 10
    batch_size = max(1, int(target_rate_per_sec / ticks_per_sec))
    interval = 1.0 / ticks_per_sec

    while True:
        try:
            session_ids = await redis.lpop(queue_name, batch_size)
            
            if session_ids:
                if isinstance(session_ids, str):
                    session_ids = [session_ids]

                pipe = redis.pipeline()
                now = datetime.now(timezone.utc)
                exp = now + timedelta(minutes=3)

                for session_id in session_ids:
                    payload = {
                        "sub": session_id,
                        "type": "entry_ticket",
                        "exp": exp,
                    }
                    ticket = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
                    pipe.set(f"ticket:{session_id}", ticket, ex=180)

                await pipe.execute()

            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Worker error in {queue_name}: {e}")
            await asyncio.sleep(1)