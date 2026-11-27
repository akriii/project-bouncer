import asyncio
from contextlib import asynccontextmanager
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import REDIS_URL
from services.worker import run_multi_tenant_dispatcher
from services.scheduler import run_auto_start_scheduler
from services.connection_manager import manager
from routers import shopper_auth, queue, waiting_room, admin, client


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

    # Wire shared Redis connection to WebSocket manager
    manager.set_redis(app.state.redis)

    dispatcher_task = asyncio.create_task(run_multi_tenant_dispatcher(app.state.redis))
    scheduler_task = asyncio.create_task(run_auto_start_scheduler(app.state.redis))
    listener_task = asyncio.create_task(manager.listen_to_redis())

    yield

    listener_task.cancel()
    dispatcher_task.cancel()
    scheduler_task.cancel()
    await asyncio.gather(dispatcher_task, scheduler_task, listener_task, return_exceptions=True)
    await app.state.redis.aclose()


app = FastAPI(title="Project Bouncer", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_origins=["http://localhost:5173", "http://localhost:5174", "http://localhost:8001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Connect all the separated routing files to the main app
app.include_router(shopper_auth.router)
app.include_router(queue.router)
app.include_router(waiting_room.router)
app.include_router(admin.router)
app.include_router(client.router)
