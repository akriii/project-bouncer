# 🛡️ Project Bouncer 

**Project Bouncer** is a high-performance, multi-tenant virtual waiting room engineered to protect high-demand drops (concert tickets, limited sneaker releases, flash sales) from scalper bots, thundering herds, and downstream server crashes.

Acting as a digital dam, Bouncer intercepts raw traffic spikes, filters out automated scrapers, mathematically neutralizes time-based scalper camping via cryptographic holding pools, and admits legitimate users to checkout in a steady, database-safe stream.

---

## ⚡ The 3 Security & Routing Tiers

Bouncer routes inbound traffic dynamically based on each client's subscription tier:

```
                      ┌────────────────────────────────────────┐
                      │          Inbound Traffic (/join)       │
                      └───────────────────┬────────────────────┘
                                          │
                        [Tenant Lookup: config:{client_id}]
                                          │
                 ┌────────────────────────┴────────────────────────┐
                 ▼                                                 ▼
        [Tier 2 WAF Enabled?]                             [Tier 2 Disabled]
         ├── Bot signature check (User-Agent)                      │
         ├── Browser context validation (Sec-Fetch-Mode)           │
         └── Trusted-proxy-aware IP rate limit                     │
                 │                                                 │
                 └────────────────────────┬────────────────────────┘
                                          │
                 ┌────────────────────────┴────────────────────────┐
                 ▼                                                 ▼
        [Tier 3 Lottery Enabled?]                         [Tier 1 Standard FIFO]
         ├── Cryptographic Intent Token (JWT)                      │
         ├── Single-use nonce (jti) replay defense                 │
         └── Event Active check:                                   │
              ├── NOT LIVE: ──► [Holding Pool (Unordered Set)]     │
              └── LIVE:     ──► [Active Queue (FIFO)] ◄────────────┘
                                          │
                                 [Worker Batch Pop]
                                          │
                     [Signs Entry Ticket with Tenant's Secret]
                                          │
                       [WebSocket Broadcast / Redirect]
```

### 1. Tier 1: High-Concurrency Core (The Digital Dam)
* **Dual-Lane Routing:** Separates standard attendees (`auth_queue`) from users with pre-authenticated sessions (`fast_lane`).
* **Real-Time WebSockets:** Live bidirectional connection pushing real-time queue position decrements and estimated wait times.
* **Controlled Batch Draining:** Asynchronous background workers continuously pop queues and issue short-lived, signed JWT entry tickets.

### 2. Tier 2: Anti-Bot WAF (The Shield)
* **Header & Scraper Detection:** Blocks known headless crawlers (`python-requests`, `aiohttp`, `curl`, `puppeteer`, etc.) and enforces valid browser contexts via `Sec-Fetch-Mode`.
* **Trusted-Proxy Rate Limiting:** Verifies upstream proxies before inspecting `X-Real-IP` to prevent IP spoofing, using atomic Redis pipelines to enforce strict sliding-window request limits.

### 3. Tier 3: Intent & Lottery (The Scalper Killer)
* **Cryptographic Pre-Cart Intent:** Requires an `X-Intent-Token` signed by the client's private backend, verifying identity and drop target before queue admission.
* **Single-Use Nonce Defense:** Enforces unique token IDs (`jti`) in Redis to eliminate token reuse and replay attacks.
* **The Holding Pool:** Users arriving before drop time are placed in an unordered Redis Set (`lottery_pool:{event_id}`).
* **The Grand Shuffle:** When an administrator triggers the Go-Live API, Bouncer scrambles the holding pool randomly and flushes all waiting users into the active queue simultaneously, completely eliminating the millisecond advantage of early camping bots.

---


## 🛠️ Tech Stack

* **Backend:** Python 3.13, FastAPI, Redis (`redis.asyncio`), PyJWT, Uvicorn, WebSockets.
* **Frontend:** React 19, Vite, Tailwind CSS v4, Oxlint.
* **Infrastructure:** Docker, Docker Compose, Nginx (load balancing 3 backend replicas with WebSocket upgrade support).

---

## 🚀 Getting Started

### 1. Prerequisites
* [Docker Desktop](https://www.docker.com/products/docker-desktop/) (with Docker Compose)
* Git

### 2. Configure Environment Variables
In the `backend/` directory, verify or create your `.env` file:

```env
# backend/.env
REDIS_URL=redis://redis:6379
SECRET_KEY=super_secret_jwt_key_bouncer_2026
INTENT_SECRET_KEY=bouncer-shared-intent-secret-key-2026

# Admin Dashboard Credentials
ADMIN_USERNAME=admin
ADMIN_PASSWORD=secure_admin_password_2026
ADMIN_API_KEY=super_secure_admin_key_123
```

In the `frontend/` directory, set the backend API endpoint:

```env
# frontend/.env
VITE_API_URL=http://localhost:8000
```

### 3. Build & Run the Cluster
From the root repository directory:

```bash
docker-compose up --build
```

The stack initializes:
* **Nginx Reverse Proxy:** `http://localhost:8000` (load balancing 3 backend instances)
* **Frontend UI:** `http://localhost:5173`
* **Redis Engine:** `localhost:6379`

---

## 🎮 How to Test

### 1. Normal Queue Flow (Tier 1)
1. Open your browser and navigate to `http://localhost:5173/`.
2. You are assigned to the **Auth Queue** and given a live queue position.
3. The queue counter decrements in real time as background workers drain the line.
4. Upon reaching the front, you are granted a signed entry ticket and safely redirected to the verified return URL.

### 2. Fast Pass Priority Flow
1. Obtain a Fast Pass JWT via `POST /login` with student credentials (`test_user` / `pwd`).
2. Pass the token as `?token=<JWT>` in the waiting room URL.
3. You will be placed in the **Fast Queue (50 ops/sec)** and cleared with priority.

### 3. Tier 3 Holding Pool & Grand Shuffle
1. Configure client drop in Redis:
   ```bash
   hset config:org_drop tier3_lottery true secret_key my_tenant_secret
   ```
2. Users join with an `X-Intent-Token` and enter the **Holding Pool (Pending Lottery)**.
3. Open the **Admin Dashboard** at `http://localhost:5173/admin` and log in (`admin` / `secure_admin_password_2026`).
4. Enter the `event_id` under the **Tier 3 Grand Shuffle** panel and click **Trigger Grand Shuffle** (or `POST /admin/events/{event_id}/go-live`).
5. All holding pool members are randomized and piped into the live queue with instant WebSocket broadcast notifications.

---

## 🧪 Automated Testing & Verification

### Security & Route Unit Tests
Run the comprehensive automated test suite (verifying redirect validation, bot signature blocks, trusted proxy evaluation, tenant ticket signing, and replay defense):

```bash
python backend/testing/test_security_and_routes.py
```

### High-Concurrency Spike & Stress Tests
Simulate thousands of concurrent users hammering the queue:

```bash
# Concurrent student simulation with WebSockets
python backend/testing/load_test.py

# 5,000-user thundering herd benchmark
python backend/testing/stress_test.py
```

### Frontend Code Quality
```bash
cd frontend
npm run lint    # Oxlint verification (0 warnings, 0 errors)
npm run build   # Production Vite bundle build
```

---

## 🗺️ Project Roadmap

- [x] **Phase 1:** Core FIFO Queue Engine & WebSockets.
- [x] **Phase 2:** Tier 2 Bot Shield (WAF, rate limiting, scraper filtering).
- [x] **Phase 3:** Tier 3 Intent Tokens, Pre-drop Holding Pool & Grand Shuffle API.
- [x] **Security Hardening:** Open Redirect whitelisting, tenant key isolation, and token replay defense.
- [ ] **Phase 4:** Next.js App Router frontend migration with Supabase Auth.
- [ ] **Phase 5:** Multi-tenant Client Dashboard (API key generation, event toggles, real-time analytics).
- [ ] **Phase 6:** `useBouncer` React client SDK for zero-friction drop integration.
- [ ] **Phase 7:** "God Mode" split-screen interactive demo for prospective clients.

