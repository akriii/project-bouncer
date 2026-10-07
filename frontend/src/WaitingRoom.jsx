import { useState, useEffect, useRef } from "react";

export default function WaitingRoom() {
  const [status, setStatus] = useState("joining"); 
  const [position, setPosition] = useState(0);
  const [lane, setLane] = useState("");
  const [ticket, setTicket] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  
  const wsRef = useRef(null);
  const hasJoined = useRef(false); 

  useEffect(() => {
    if (hasJoined.current) return;
    hasJoined.current = true;

  const connectToBouncer = async () => {
      try {
        let headers = {};
        
        // 1. Read token and dynamic return_to URL from the browser link
        const urlParams = new URLSearchParams(window.location.search);
        const handoffToken = urlParams.get("token");
        const returnUrl = urlParams.get("return_to") || "http://localhost:8001/checkout";

        if (handoffToken) {
          headers["Authorization"] = `Bearer ${handoffToken}`;
        }

        const response = await fetch("http://localhost:8000/join", {
          method: "POST",
          headers: headers,
        });
        const data = await response.json();

        setLane(data.lane);
        setPosition(data.initial_position);
        setStatus("waiting"); 
        
        const ws = new WebSocket(`ws://localhost:8000/ws/waiting-room/${data.lane}/${data.session_id}`);
        wsRef.current = ws;

        ws.onmessage = (event) => {
          const wsData = JSON.parse(event.data);
          if (wsData.status === "waiting") {
            setPosition(wsData.position);
          } else if (wsData.status === "cleared") {
            setStatus("cleared");
            ws.close();
            
            // 2. Redirect back dynamically to the caller site with the ticket
            window.location.href = `${returnUrl}?bouncer_ticket=${wsData.entry_ticket}`;
            
          } else if (wsData.status === "error") {
            setStatus("error");
            setErrorMessage(wsData.message);
            ws.close();
          }
        };
      } catch (err) {
        setStatus("error");
        setErrorMessage("Failed to connect to Bouncer.");
      }
    };

    connectToBouncer();

    return () => {
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  const getEstimatedTime = () => {
    if (position === 0) return "Calculating...";
    
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
              {lane === "fast_lane" ? "🚀 Fast Queue" : "⏳ Auth Queue"}
            </h1>
            
            {/* DYNAMIC SUBTITLE COLOR */}
            <p className={`text-sm mb-8 font-semibold ${lane === 'fast_lane' ? 'text-emerald-400' : 'text-blue-400'}`}>
              {lane === "fast_lane" ? "Priority Access Validated" : "Standard Access"}
            </p>

            <div className="bg-gray-950 rounded-xl p-6 border border-gray-800 mb-6 shadow-inner">
              <div className="text-gray-500 text-xs uppercase tracking-widest font-semibold mb-2">
                People Ahead of You
              </div>
              <div className="text-6xl font-black text-white mb-6 tabular-nums tracking-tighter">
                {position.toLocaleString()}
              </div>

              <div className="text-gray-500 text-xs uppercase tracking-widest font-semibold mb-1">
                Estimated Wait Time
              </div>
              
              {/* DYNAMIC ETA COLOR */}
              <div className={`text-xl font-medium ${lane === 'fast_lane' ? 'text-emerald-400' : 'text-blue-400'}`}>
                {getEstimatedTime()}
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