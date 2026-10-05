import asyncio
from contextlib import asynccontextmanager
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import REDIS_URL
from services.worker import process_queue
from routers import auth, waiting_room, admin

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = aioredis.from_url(REDIS_URL, decode_responses=True)

    for attempt in range(15):
        try:
            await app.state.redis.ping()
            print("Connected to Redis successfully.")
            break
        except Exception as e:
            print(f"Waiting for Redis... ({attempt + 1}/15): {e}")
            await asyncio.sleep(1)
    else:
        raise RuntimeError("Could not connect to Redis after multiple attempts.")

    fast_task = asyncio.create_task(process_queue(app.state.redis, "fast_lane", 50.0))
    auth_task = asyncio.create_task(process_queue(app.state.redis, "auth_queue", 20.0))

    yield

    fast_task.cancel()
    auth_task.cancel()
    await asyncio.gather(fast_task, auth_task, return_exceptions=True)
    await app.state.redis.aclose()


app = FastAPI(title="Project Bouncer", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Connect all the separated routing files to the main app
app.include_router(auth.router)
app.include_router(waiting_room.router)
app.include_router(admin.router)