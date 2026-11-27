import asyncio
import logging
from typing import Optional
from datetime import datetime, timezone
from core.redis_utils import decode_hash
from services.shuffle import execute_grand_shuffle

logger = logging.getLogger("uvicorn.error")


def parse_start_time(start_time_str: str) -> float:
    """Parse start_time string from ISO format or unix timestamp to epoch seconds."""
    try:
        return float(start_time_str)
    except ValueError:
        # Try ISO 8601 parsing
        clean_str = start_time_str.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()


def compute_drop_stage(client_config: dict, is_live: bool, now_ts: Optional[float] = None) -> dict:
    """Compute the current lifecycle stage for a client drop."""
    if now_ts is None:
        now_ts = datetime.now(timezone.utc).timestamp()

    start_time_val = client_config.get("start_time")
    try:
        pre_auth_hours = int(client_config.get("pre_auth_window_hours", 48))
    except (ValueError, TypeError):
        pre_auth_hours = 48

    if is_live:
        return {
            "stage": "Live",
            "pre_auth_window_hours": pre_auth_hours,
            "pre_auth_opens_at": None,
            "start_time_ts": None,
        }

    if not start_time_val:
        return {
            "stage": "Idle",
            "pre_auth_window_hours": pre_auth_hours,
            "pre_auth_opens_at": None,
            "start_time_ts": None,
        }

    try:
        start_ts = parse_start_time(start_time_val)
        opens_at_ts = start_ts - (pre_auth_hours * 3600)
        opens_at_iso = datetime.fromtimestamp(opens_at_ts, tz=timezone.utc).isoformat()

        if now_ts < opens_at_ts:
            stage = "Scheduled (Pre-Auth Closed)"
        elif opens_at_ts <= now_ts < start_ts:
            stage = "Pre-Auth Open"
        else:
            stage = "Ready (Pending Go-Live)"

        return {
            "stage": stage,
            "pre_auth_window_hours": pre_auth_hours,
            "pre_auth_opens_at": opens_at_iso,
            "start_time_ts": start_ts,
        }
    except Exception:
        return {
            "stage": "Scheduled",
            "pre_auth_window_hours": pre_auth_hours,
            "pre_auth_opens_at": None,
            "start_time_ts": None,
        }


async def run_auto_start_scheduler(redis_client):
    """Background task running every 1 second to trigger scheduled drops."""
    print("⏰ AUTO-START DROP SCHEDULER STARTED", flush=True)

    while True:
        try:
            raw_clients = await redis_client.smembers("registered_clients")
            client_set = {c.decode("utf-8") if isinstance(c, bytes) else c for c in raw_clients}

            now_ts = datetime.now(timezone.utc).timestamp()

            for client_id in client_set:
                is_live = await redis_client.get(f"event_active:{client_id}")
                if is_live == "true":
                    continue

                raw_cfg = await redis_client.hgetall(f"config:{client_id}")
                cfg = decode_hash(raw_cfg)

                if cfg.get("auto_start", "false").lower() != "true":
                    continue

                start_time_val = cfg.get("start_time")
                if not start_time_val:
                    continue

                try:
                    target_ts = parse_start_time(start_time_val)
                    if now_ts >= target_ts:
                        print(f"🎯 AUTO-TRIGGERING GRAND SHUFFLE FOR CLIENT: {client_id}", flush=True)
                        await execute_grand_shuffle(redis_client, client_id)
                except Exception as e:
                    logger.error(f"Failed to evaluate schedule for client {client_id}: {e}")

        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")

        await asyncio.sleep(1.0)
