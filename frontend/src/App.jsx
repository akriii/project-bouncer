import WaitingRoom from "./WaitingRoom";
import AdminDashboard from "./AdminDashboard";

function App() {
  // Simple router switch based on the URL pathname
  const path = window.location.pathname;

  if (path === "/admin") {
    return <AdminDashboard />;
  }

  return <WaitingRoom />;
}

export default App;