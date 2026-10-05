# 🛡️ Project Bouncer 1.0

**Project Bouncer** is a high-performance, real-time virtual waiting room designed to protect downstream database and authentication servers from being overwhelmed during massive traffic spikes (e.g., university course registration). 

It acts as a cryptographic gateway, absorbing thousands of concurrent users, holding them in a lightweight WebSocket queue, and releasing them to the main application in controlled, database-safe batches using Redis and JSON Web Tokens (JWT).

## ✨ Key Features

* **Dual-Lane Routing:** Automatically separates unauthenticated guests (Auth Queue) from users with valid session tokens (Fast Lane).
* **Real-Time WebSockets:** Provides users with live updates on their queue position and estimated wait times without polling the server.
* **Optimized Batch Processing:** Backend background workers drain Redis queues using pipeline batching, easily handling hundreds of users per second.
* **Secure Admin Dashboard:** A protected monitoring UI to view live throughput, track active connections, and perform emergency queue flushes.
* **Dockerized Infrastructure:** Frontend, backend, and Redis cache are fully containerized for one-click deployment.

## 🛠️ Tech Stack

* **Backend:** Python, FastAPI, Redis (aioredis), PyJWT
* **Frontend:** React, Vite, Tailwind CSS
* **Infrastructure:** Docker, Docker Compose

---

## 🚀 Getting Started

### 1. Prerequisites
Ensure you have the following installed on your machine:
* [Docker Desktop](https://www.docker.com/products/docker-desktop/) (includes Docker Compose)
* Git

### 2. Clone the Repository
```bash
git clone [https://github.com/akmalzaki/project-bouncer.git](https://github.com/akmalzaki/project-bouncer.git)
cd project-bouncer
```

### 3. Configure Environment Variables
In the `backend/` directory, create a `.env` file to store your secrets. You can copy the template below:

```bash
# backend/.env

# Cryptographic key for signing Entry Tickets and Fast Passes
SECRET_KEY=super_secret_jwt_key_bouncer_2026

# Secure Admin Dashboard Credentials
ADMIN_USERNAME=admin
ADMIN_PASSWORD=secure_admin_password_2026

# Internal Docker Redis URL
REDIS_URL=redis://redis:6379
```

### 4. Build and Run the Application
Start the entire stack using Docker Compose from the root directory:

```bash
docker-compose up --build
```
*The initial build may take a few minutes as it downloads the Python and Node.js base images and installs dependencies.*

Once the terminal logs confirm the servers are running, the application is accessible at:
* **Frontend UI:** http://localhost:5173
* **Backend API:** http://localhost:8000

---

## 🎮 How to Use & Test

The frontend is currently configured with a **Testing Mode** that allows you to easily simulate both authenticated and unauthenticated traffic flows.

### Standard Access (Auth Queue)
To simulate a normal, unauthenticated student arriving at the gateway:
1. Open your browser and navigate to `http://localhost:5173/`.
2. You will be placed in the **Auth Queue (Blue)**.
3. The system will hold your connection and automatically grant you an entry ticket when it is your turn.

### Priority Access (Fast Lane)
To simulate a student who already has a valid JWT session from a previous login:
1. Navigate to `http://localhost:5173/?fast=true`.
2. The testing bypass will generate a dummy JWT and place you in the **Fast Queue (Green)**.
3. Because the backend processes the Fast Lane at a significantly higher throughput rate (50 ops/sec vs 20 ops/sec), you will be cleared almost instantly.

### The Admin Dashboard
To monitor the system under load or manage the queues:
1. Navigate to `http://localhost:5173/admin`.
2. Log in using the credentials you defined in your `.env` file (`admin` / `secure_admin_password_2026`).
3. View real-time metrics for both queues and active WebSocket sessions.
4. Use the **Flush Queue** buttons (protected by a double-confirmation prompt) to safely clear testing data.

---

## ⚡ Load Testing

To verify the system's stability under pressure, Project Bouncer includes an asynchronous Python load-testing script. 

1. Open a new local terminal (outside of Docker).
2. Install the testing dependencies:
   ```bash
   pip install httpx websockets
   ```
3. Run the spike simulation (ensure your Docker containers and Admin Dashboard are running first):
   ```bash
   cd backend
   cd testing
   python load_test.py
   ```
Watch the Admin Dashboard as the script blasts the gateway with hundreds of concurrent simulated students, observing how the backend smoothly buffers and drains the queues without dropping a single connection.