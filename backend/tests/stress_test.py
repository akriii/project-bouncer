import asyncio
import aiohttp
import time

async def hit_bouncer(session, url, semaphore):
    # The semaphore ensures Windows only opens a safe number of sockets at once
    async with semaphore:
        try:
            async with session.post(url) as response:
                return await response.json()
        except Exception as e:
            return e

async def main():
    url = "http://localhost:8000/join"
    total_users = 5000
    
    # Limit Windows to 200 concurrent active connections at any given millisecond
    concurrent_limit = 200
    semaphore = asyncio.Semaphore(concurrent_limit)

    print(f"🚀 Launching Thundering Herd: {total_users} users (Batched at {concurrent_limit} concurrent)...")
    start_time = time.time()

    connector = aiohttp.TCPConnector(limit=concurrent_limit)
    
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [hit_bouncer(session, url, semaphore) for _ in range(total_users)]
        results = await asyncio.gather(*tasks)

    end_time = time.time()
    duration = end_time - start_time
    
    success_count = sum(1 for r in results if isinstance(r, dict) and "lane" in r)
    error_count = total_users - success_count

    print("\n--- 📊 STRESS TEST RESULTS ---")
    print(f"Total Execution Time  : {duration:.2f} seconds")
    print(f"Requests Per Second   : {total_users / duration:.0f} req/s")
    print(f"Successfully Queued   : {success_count} users")
    print(f"Client Errors         : {error_count} errors")
    
    if success_count > 0:
        valid_results = [r for r in results if isinstance(r, dict) and "initial_position" in r]
        if valid_results:
            print(f"Final queue length    : {valid_results[-1].get('initial_position')} users")

if __name__ == "__main__":
    asyncio.run(main())