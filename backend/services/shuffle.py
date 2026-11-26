import json
import random
import logging
from typing import Optional

from core.tiers import decode_hash, resolve_tiers
from services.tickets import publish_entry_ticket

logger = logging.getLogger("uvicorn.error")


async def execute_grand_shuffle(redis_client, client_id: str, event_id: Optional[str] = None) -> dict:
    """Scramble the pre-drop holding pool and transition users.

    Users with tier1_queue are pushed to queue:{session_client}:auth_queue in randomized order.
    Users without tier1_queue are directly issued signed entry tickets.
    Future shoppers bypass the holding pool.
    """
    pool_keys = [f"lottery_pool:{client_id}"]
    if event_id and event_id != client_id:
        pool_keys.append(f"lottery_pool:{event_id}")

    waiting_users_set = set()
    for pk in pool_keys:
        members = await redis_client.smembers(pk)
        for m in members:
            waiting_users_set.add(m.decode("utf-8") if isinstance(m, bytes) else m)

    waiting_users = list(waiting_users_set)

    # Mark client and event drops as live
    await redis_client.set(f"event_active:{client_id}", "true")
    if event_id:
        await redis_client.set(f"event_active:{event_id}", "true")

    if not waiting_users:
        # Broadcast event live even if pool is empty
        await redis_client.publish(
            "bouncer_channel",
            json.dumps({"type": "event_live", "client_id": client_id, "event_id": event_id or client_id}),
        )
        return {
            "status": "success",
            "message": f"Drop {client_id} is live (0 users in pool).",
            "users_shuffled": 0,
            "users_queued": 0,
            "users_released": 0,
        }

    # 1. THE SHUFFLE: Scramble user list to neutralize bot camping
    random.shuffle(waiting_users)

    # 2. Separate into Tier 1 (FIFO queue) vs Direct (Instant Ticket)
    queued = []
    direct = []

    pipeline = redis_client.pipeline()

    for session_id in waiting_users:
        session = decode_hash(await redis_client.hgetall(f"session:{session_id}"))
        session_client = session.get("client_id") or client_id

        s_cfg = decode_hash(await redis_client.hgetall(f"config:{session_client}"))
        s_tiers = resolve_tiers(session_client, s_cfg)

        if "tier1_queue" in s_tiers:
            queued.append(session_id)
            pipeline.rpush(f"queue:{session_client}:auth_queue", session_id)
        else:
            direct.append(session_id)

    # Also push to legacy auth_queue for backwards compatibility with tests and old workers
    if queued:
        pipeline.rpush("auth_queue", *queued)

    for pk in pool_keys:
        pipeline.delete(pk)

    await pipeline.execute()

    # 3. Broadcast to WebSockets that event is live
    await redis_client.publish(
        "bouncer_channel",
        json.dumps({
            "type": "event_live",
            "client_id": client_id,
            "event_id": event_id or client_id,
        }),
    )

    # 4. Release direct ticket holders
    for session_id in direct:
        await publish_entry_ticket(redis_client, session_id)
        session = decode_hash(await redis_client.hgetall(f"session:{session_id}"))
        session_client = session.get("client_id") or client_id
        await redis_client.incr(f"metrics:{session_client}:admitted_users")

    return {
        "status": "success",
        "message": f"Drop {client_id} is now LIVE.",
        "users_shuffled": len(waiting_users),
        "users_queued": len(queued),
        "users_released": len(direct),
    }
