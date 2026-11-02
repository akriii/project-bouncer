import { useState, useEffect, useRef, useCallback } from "react";

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";
const WS_BASE = API_BASE.replace(/^http/, "ws");

export default function WaitingRoom() {
  const [status, setStatus] = useState("joining"); 
  const [position, setPosition] = useState(0);
  const [lane, setLane] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  
  const wsRef = useRef(null);
  const hasJoined = useRef(false); 
  const validatedReturnUrlRef = useRef("http://localhost:8001/checkout");
  const sessionInfoRef = useRef({ lane: "", sessionId: "" });
  const reconnectAttemptsRef = useRef(0);
  const establishWebSocketRef = useRef(null);

  const establishWebSocket = useCallback((targetLane, sessionId) => {
    if (wsRef.current) {
      wsRef.current.close();
    }

    const wsUrl = `${WS_BASE}/ws/waiting-room/${targetLane}/${sessionId}`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      try {
        const wsData = JSON.parse(event.data);
        if (wsData.status === "waiting") {
          if (wsData.position !== undefined) {
            setPosition(wsData.position);
          } else if (wsData.progress) {
            setPosition((prev) => {
              if (typeof prev === "number") {
                return Math.max(1, prev - wsData.progress);
              }
              return prev;
            });
          }
        } else if (wsData.status === "cleared") {
          setStatus("cleared");
          ws.close();
          
          const targetUrl = validatedReturnUrlRef.current;
          try {
            const parsed = new URL(targetUrl);
            if (parsed.protocol === "http:" || parsed.protocol === "https:") {
              parsed.searchParams.set("bouncer_ticket", wsData.entry_ticket);
              window.location.href = parsed.toString();
            } else {
              setStatus("error");
              setErrorMessage("Security error: Untrusted redirect protocol.");
            }
          } catch {
            window.location.href = `${targetUrl}?bouncer_ticket=${wsData.entry_ticket}`;
          }
        } else if (wsData.status === "error") {
          setStatus("error");
          setErrorMessage(wsData.message || "An error occurred in waiting room.");
          ws.close();
        }
      } catch (e) {
        console.error("Failed to parse websocket message", e);
      }
    };

    ws.onclose = () => {
      // Reconnect if closed unexpectedly while still waiting
      if (status !== "cleared" && reconnectAttemptsRef.current < 5) {
        reconnectAttemptsRef.current += 1;
        const delay = Math.min(1000 * Math.pow(2, reconnectAttemptsRef.current), 8000);
        setTimeout(() => {
          if (sessionInfoRef.current.sessionId && establishWebSocketRef.current) {
            establishWebSocketRef.current(sessionInfoRef.current.lane, sessionInfoRef.current.sessionId);
          }
        }, delay);
      }
    };

    ws.onerror = () => {
      // On error, let onclose handle reconnection
    };
  }, [status]);

  useEffect(() => {
    establishWebSocketRef.current = establishWebSocket;
  }, [establishWebSocket]);

  useEffect(() => {
    if (hasJoined.current) return;
    hasJoined.current = true;

    const connectToBouncer = async () => {
      try {
        const headers = { "Content-Type": "application/json" };
        const urlParams = new URLSearchParams(window.location.search);
        const handoffToken = urlParams.get("token");
        const returnUrl = urlParams.get("return_to");
        const clientId = urlParams.get("client_id") || "default_client";

        if (handoffToken) {
          headers["Authorization"] = `Bearer ${handoffToken}`;
        }

        const response = await fetch(`${API_BASE}/join`, {
          method: "POST",
          headers: headers,
          body: JSON.stringify({
            client_id: clientId,
            return_to: returnUrl || undefined
          })
        });

        if (!response.ok) {
          const errData = await response.json().catch(() => ({}));
          throw new Error(errData.detail || `Server returned ${response.status}`);
        }

        const data = await response.json();

        if (data.validated_return_to) {
          validatedReturnUrlRef.current = data.validated_return_to;
        }

        setLane(data.lane);
        setPosition(data.initial_position);
        setStatus("waiting"); 
        
        sessionInfoRef.current = { lane: data.lane, sessionId: data.session_id };
        establishWebSocket(data.lane, data.session_id);
      } catch (err) {
        setStatus("error");
        setErrorMessage(err.message || "Failed to connect to Bouncer.");
      }
    };

    connectToBouncer();

    return () => {
      if (wsRef.current) wsRef.current.close();
    };
  }, [establishWebSocket]);

  const getEstimatedTime = () => {
    if (typeof position !== "number" || position === 0) return "Calculating...";
    
    const secondsPerUser = lane === "fast_lane" ? 0.02 : 0.05; 
    const totalSeconds = position * secondsPerUser;
    
    if (totalSeconds < 60) return "Less than a minute";
    return `~${Math.ceil(totalSeconds / 60)} minutes`;
  };

  return (
    <div className="min-h-screen bg-gray-950 flex flex-col items-center justify-center p-4 text-white font-sans">
      <div className="max-w-md w-full bg-gray-900 border border-gray-800 rounded-2xl shadow-2xl p-8 text-center">
        
        {/* AUTOMATIC LOADING STATE */}
        {status === "joining" && (
          <div className="py-12 animate-pulse">
            <div className="w-12 h-12 border-4 border-emerald-500 border-t-transparent rounded-full animate-spin mx-auto mb-4"></div>
            <p className="text-gray-300 font-medium">Authenticating & Assigning Lane...</p>
          </div>
        )}

        {/* THE ACTIVE WAITING ROOM */}
        {status === "waiting" && (
          <div className="py-4">
            
            {/* DYNAMIC TITLE & ICON */}
            <h1 className="text-3xl font-bold mb-1">
              {lane === "fast_lane" ? "🚀 Fast Queue" : lane === "holding_pool" ? "🎟️ Holding Pool" : "⏳ Auth Queue"}
            </h1>
            
            {/* DYNAMIC SUBTITLE COLOR */}
            <p className={`text-sm mb-8 font-semibold ${lane === "fast_lane" ? "text-emerald-400" : lane === "holding_pool" ? "text-amber-400" : "text-blue-400"}`}>
              {lane === "fast_lane" ? "Priority Access Validated" : lane === "holding_pool" ? "Grand Shuffle Pending" : "Standard Access"}
            </p>

            <div className="bg-gray-950 rounded-xl p-6 border border-gray-800 mb-6 shadow-inner">
              <div className="text-gray-500 text-xs uppercase tracking-widest font-semibold mb-2">
                People Ahead of You
              </div>
              <div className="text-6xl font-black text-white mb-6 tabular-nums tracking-tighter">
                {typeof position === "number" ? position.toLocaleString() : position}
              </div>

              <div className="text-gray-500 text-xs uppercase tracking-widest font-semibold mb-1">
                Estimated Wait Time
              </div>
              
              {/* DYNAMIC ETA COLOR */}
              <div className={`text-xl font-medium ${lane === "fast_lane" ? "text-emerald-400" : lane === "holding_pool" ? "text-amber-400" : "text-blue-400"}`}>
                {lane === "holding_pool" ? "Starting Soon" : getEstimatedTime()}
              </div>
            </div>

            <p className="text-xs text-gray-500 px-4">
              Do not refresh this page. You will be automatically redirected when it is your turn.
            </p>
          </div>
        )}

        {/* CLEARED STATE */}
        {status === "cleared" && (
          <div className="py-8">
            <div className="w-16 h-16 bg-emerald-500/20 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" d="M5 13l4 4L19 7"></path>
              </svg>
            </div>
            <h2 className="text-2xl font-bold text-emerald-400 mb-2">You are cleared!</h2>
            <p className="text-gray-400 text-sm mb-6">Secure access granted. Redirecting you to the portal...</p>
            
            {/* Simulated Redirect Spinner */}
            <div className="w-6 h-6 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin mx-auto mt-4"></div>
          </div>
        )}

        {/* ERROR STATE */}
        {status === "error" && (
          <div className="py-8">
            <div className="text-red-500 text-5xl mb-4">⚠️</div>
            <h2 className="text-xl font-bold text-red-400 mb-2">Connection Error</h2>
            <p className="text-gray-400 text-sm mb-6">{errorMessage}</p>
          </div>
        )}
      </div>
    </div>
  );
}
