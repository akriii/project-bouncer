import asyncio
import json
import logging
from core.redis_utils import decode_hash
from core.tiers import resolve_tiers
from services.tickets import publish_entry_ticket

logger = logging.getLogger("uvicorn.error")


async def run_multi_tenant_dispatcher(redis_client):
    """Dynamic multi-tenant queue dispatcher:

    Pops users from isolated per-client queues:
      - queue:{client_id}:fast_lane
      - queue:{client_id}:auth_queue
    Increments metrics:{client_id}:admitted_users and emits scoped progress broadcasts.
    """
    print("🚀 MULTI-TENANT QUEUE DISPATCHER STARTED", flush=True)

    while True:
        try:
            raw_clients = await redis_client.smembers("registered_clients")
            client_set = {c.decode("utf-8") if isinstance(c, bytes) else c for c in raw_clients}
            client_set.add("default_client")

            for client_id in client_set:
                raw_cfg = await redis_client.hgetall(f"config:{client_id}")
                cfg = decode_hash(raw_cfg)
                tiers = resolve_tiers(client_id, cfg)

                if "tier1_queue" not in tiers:
                    continue

                try:
                    process_rate = float(cfg.get("process_rate", 20.0))
                except (ValueError, TypeError):
                    process_rate = 20.0

                # Fast lane takes priority over standard auth queue
                for lane in ("fast_lane", "auth_queue"):
                    queue_key = f"queue:{client_id}:{lane}"
                    next_user = await redis_client.lpop(queue_key)

                    if next_user:
                        session_id = next_user.decode("utf-8") if isinstance(next_user, bytes) else next_user
                        await publish_entry_ticket(redis_client, session_id)
                        await redis_client.incr(f"metrics:{client_id}:admitted_users")

                        progress_broadcast = json.dumps({
                            "type": "queue_progress",
                            "client_id": client_id,
                            "lane": lane,
                            "cleared_count": 1,
                        })
                        await redis_client.publish("bouncer_channel", progress_broadcast)

            # Drain legacy unpartitioned queues if anything lands there
            for legacy_lane in ("fast_lane", "auth_queue"):
                legacy_user = await redis_client.lpop(legacy_lane)
                if legacy_user:
                    s_id = legacy_user.decode("utf-8") if isinstance(legacy_user, bytes) else legacy_user
                    await publish_entry_ticket(redis_client, s_id)
                    await redis_client.publish(
                        "bouncer_channel",
                        json.dumps({"type": "queue_progress", "lane": legacy_lane, "cleared_count": 1}),
                    )

        except Exception as e:
            logger.error(f"Multi-tenant dispatcher error: {e}")

        await asyncio.sleep(0.05)
