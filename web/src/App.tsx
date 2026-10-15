import { useEffect } from "react";
import { HashRouter, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { mockMode } from "./api";
import { Blocks } from "./pages/Blocks";
import { Calendar } from "./pages/Calendar";
import { Heatmap } from "./pages/Heatmap";
import { Home } from "./pages/Home";
import { Plans } from "./pages/Plans";
import { startParam } from "./telegram";

/** A link from a group poll card opens straight onto that poll's heatmap. */
function StartRoute() {
  const navigate = useNavigate();
  useEffect(() => {
    const match = /^poll_([0-9a-f-]{36})$/i.exec(startParam());
    if (match) navigate(`/poll/${match[1]}`, { replace: true });
  }, [navigate]);
  return null;
}

export function App() {
  return (
    // Hash routing: the static build needs no server-side route fallback under /app/.
    <HashRouter>
      {mockMode && <div className="banner info">Development mock data. Open from Telegram for your real plans.</div>}
      <StartRoute />
      <main>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/week" element={<Calendar />} />
          <Route path="/plans" element={<Plans />} />
          <Route path="/busy" element={<Blocks />} />
          <Route path="/poll/:id" element={<Heatmap />} />
        </Routes>
      </main>
      <nav>
        <NavLink to="/" end>Home</NavLink>
        <NavLink to="/week">Week</NavLink>
        <NavLink to="/plans">Plans</NavLink>
        <NavLink to="/busy">Busy</NavLink>
      </nav>
    </HashRouter>
  );
}
