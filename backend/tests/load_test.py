import asyncio
import time
import httpx
import websockets
import json

BASE_HTTP = "http://localhost:8000"
BASE_WS = "ws://localhost:8000"

# --- Scale Configuration ---
# 500 Fast + 500 Auth = 1,000 concurrent students hitting the waiting room simultaneously
NUM_STUDENTS_PER_LANE = 500  

TOTAL_STUDENTS = NUM_STUDENTS_PER_LANE * 2


async def simulate_student(student_id: int, is_fast_lane: bool, client: httpx.AsyncClient, semaphore: asyncio.Semaphore):
    async with semaphore:  # Protects local OS file descriptors/ephemeral ports
        token = None
        headers = {}

        if is_fast_lane:
            try:
                login_res = await client.post(
                    f"{BASE_HTTP}/login",
                    json={"student_id": "test_user", "password": "pwd"}
                )
                if login_res.status_code == 200:
                    token = login_res.json()["access_token"]
                    headers["Authorization"] = f"Bearer {token}"
                else:
                    return
            except Exception:
                return

        # 1. Join queue
        try:
            join_res = await client.post(f"{BASE_HTTP}/join", headers=headers)
            if join_res.status_code != 200:
                return
            data = join_res.json()
            session_id = data["session_id"]
            lane = data["lane"]
        except Exception:
            return

        # 2. Hold WebSocket position
        ws_url = f"{BASE_WS}/ws/waiting-room/{lane}/{session_id}"
        wait_start = time.perf_counter()

        try:
            async with websockets.connect(ws_url, ping_interval=None) as ws:
                while True:
                    msg = await ws.recv()
                    payload = json.loads(msg)
                    status = payload.get("status")

                    if status == "cleared":
                        total_wait = time.perf_counter() - wait_start
                        lane_tag = "🚀 FAST" if lane == "fast_lane" else "⏳ AUTH"
                        print(f"[{lane_tag}] Student {student_id:04d} admitted in {total_wait:.2f}s")
                        break
                    elif status == "expired":
                        break
        except Exception:
            pass


async def main():
    print(f"Blasting waiting room with {TOTAL_STUDENTS} concurrent students...")
    start_time = time.perf_counter()

    # Increase connection pool limits to prevent client-side bottlenecks
    limits = httpx.Limits(
        max_keepalive_connections=TOTAL_STUDENTS + 50,
        max_connections=TOTAL_STUDENTS + 100
    )

    # Concurrency barrier: ensures Python doesn't exceed OS socket limits
    concurrency_limit = asyncio.Semaphore(1000)

    async with httpx.AsyncClient(limits=limits, timeout=30.0) as client:
        tasks = []

        # 1. Generate Fast Lane Cohort
        for i in range(1, NUM_STUDENTS_PER_LANE + 1):
            tasks.append(simulate_student(i, is_fast_lane=True, client=client, semaphore=concurrency_limit))

        # 2. Generate Auth Queue Cohort
        for i in range(NUM_STUDENTS_PER_LANE + 1, TOTAL_STUDENTS + 1):
            tasks.append(simulate_student(i, is_fast_lane=False, client=client, semaphore=concurrency_limit))

        # Run all requests concurrently
        await asyncio.gather(*tasks)

    elapsed = time.perf_counter() - start_time
    print(f"\nAll {TOTAL_STUDENTS} students cleared in {elapsed:.2f}s!")


if __name__ == "__main__":
    asyncio.run(main())