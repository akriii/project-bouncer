import { useState, useEffect } from "react";

export default function AdminDashboard() {
  const [adminToken, setAdminToken] = useState(sessionStorage.getItem("adminToken"));
  
  // Login State
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState("");

  // Dashboard State
  const [metrics, setMetrics] = useState({ fast_lane_count: 0, auth_queue_count: 0, active_sessions: 0 });
  const [loading, setLoading] = useState(true);
  const [actionMessage, setActionMessage] = useState("");

  const handleLogin = async (e) => {
    e.preventDefault();
    setLoginError("");
    try {
      const res = await fetch("http://localhost:8000/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      
      if (res.ok) {
        const data = await res.json();
        sessionStorage.setItem("adminToken", data.access_token);
        setAdminToken(data.access_token);
      } else {
        setLoginError("Invalid credentials");
      }
    } catch (err) {
      setLoginError("Failed to connect to server");
    }
  };

  const logout = () => {
    sessionStorage.removeItem("adminToken");
    setAdminToken(null);
  };

  useEffect(() => {
    if (!adminToken) return;

    const fetchMetrics = async () => {
      try {
        const res = await fetch("http://localhost:8000/admin/metrics", {
          headers: { "Authorization": `Bearer ${adminToken}` },
        });
        if (res.ok) {
          const data = await res.json();
          setMetrics(data);
          setLoading(false);
        } else if (res.status === 401 || res.status === 403) {
          logout(); 
        }
      } catch (err) {
        console.error("Failed to fetch metrics");
      }
    };

    fetchMetrics();
    const interval = setInterval(fetchMetrics, 2000);
    return () => clearInterval(interval);
  }, [adminToken]);

  const flushQueue = async (lane) => {
    // 1. Double-confirm before doing anything
    const isConfirmed = window.confirm(
      `⚠️ WARNING: Are you sure you want to flush the ${lane}?\n\nThis will NOT admit users. It will instantly kick all currently waiting users out of line and force them to restart.`
    );
    
    if (!isConfirmed) return; // Exit if the admin clicks "Cancel"

    // 2. Proceed with the flush if they clicked "OK"
    try {
      const res = await fetch(`http://localhost:8000/admin/flush/${lane}`, {
        method: "POST",
        headers: { "Authorization": `Bearer ${adminToken}` },
      });
      if (res.ok) {
        const data = await res.json();
        setActionMessage(data.message);
        setTimeout(() => setActionMessage(""), 3000);
      }
    } catch (err) {
      setActionMessage(`Failed to clear ${lane}`);
    }
  };

  // --- LOGIN VIEW ---
  if (!adminToken) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center p-4">
        <form onSubmit={handleLogin} className="bg-gray-900 border border-gray-800 p-8 rounded-2xl shadow-xl w-full max-w-sm">
          <h2 className="text-2xl font-bold text-white mb-6 text-center">Admin Access</h2>
          {loginError && <div className="mb-4 text-red-400 text-sm bg-red-950/50 p-3 rounded-lg border border-red-900/50">{loginError}</div>}
          <div className="mb-4">
            <label className="block text-gray-400 text-xs font-semibold mb-2 uppercase tracking-wide">Username</label>
            <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} className="w-full bg-gray-950 border border-gray-800 text-white rounded-lg p-3 outline-none focus:border-emerald-500 transition" required />
          </div>
          <div className="mb-6">
            <label className="block text-gray-400 text-xs font-semibold mb-2 uppercase tracking-wide">Password</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} className="w-full bg-gray-950 border border-gray-800 text-white rounded-lg p-3 outline-none focus:border-emerald-500 transition" required />
          </div>
          <button type="submit" className="w-full bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-3 rounded-lg transition shadow-lg shadow-emerald-900/20">Secure Login</button>
        </form>
      </div>
    );
  }

  // --- DASHBOARD VIEW ---
  return (
    <div className="min-h-screen bg-gray-950 p-8 text-white font-sans">
      <div className="max-w-5xl mx-auto">
        <div className="flex justify-between items-center mb-8 border-b border-gray-800 pb-4">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Bouncer Admin</h1>
            <p className="text-gray-400 text-sm">Real-time throughput monitoring</p>
          </div>
          <div className="flex items-center space-x-4">
            <div className="flex items-center space-x-2">
              <span className="w-3 h-3 bg-emerald-500 rounded-full animate-pulse"></span>
              <span className="text-xs font-mono text-emerald-400 uppercase tracking-wider">Live</span>
            </div>
            <button onClick={logout} className="text-xs text-gray-400 hover:text-white border border-gray-700 hover:border-gray-500 px-3 py-1.5 rounded transition">Logout</button>
          </div>
        </div>

        {actionMessage && (
          <div className="mb-6 bg-emerald-950 border border-emerald-800 text-emerald-400 px-4 py-3 rounded-xl text-sm">
            {actionMessage}
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
          <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6 shadow-xl">
            <div className="text-gray-400 text-xs uppercase tracking-widest font-semibold mb-2">🚀 Fast Queue</div>
            <div className="text-5xl font-black text-emerald-400 mb-4 tabular-nums">{loading ? "..." : metrics.fast_lane_count}</div>
            <button onClick={() => flushQueue("fast_lane")} className="w-full bg-gray-800 hover:bg-gray-700 text-xs font-semibold py-2 px-3 rounded-lg transition text-gray-300">Flush Fast Queue</button>
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6 shadow-xl">
            <div className="text-gray-400 text-xs uppercase tracking-widest font-semibold mb-2">⏳ Auth Queue</div>
            <div className="text-5xl font-black text-blue-400 mb-4 tabular-nums">{loading ? "..." : metrics.auth_queue_count}</div>
            <button onClick={() => flushQueue("auth_queue")} className="w-full bg-gray-800 hover:bg-gray-700 text-xs font-semibold py-2 px-3 rounded-lg transition text-gray-300">Flush Auth Queue</button>
          </div>
          <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6 shadow-xl">
            <div className="text-gray-400 text-xs uppercase tracking-widest font-semibold mb-2">🔌 Active Sessions</div>
            <div className="text-5xl font-black text-white mb-4 tabular-nums">{loading ? "..." : metrics.active_sessions}</div>
            <div className="text-xs text-gray-500 py-2">Connected WebSockets</div>
          </div>
        </div>
      </div>
    </div>
  );
}