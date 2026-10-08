import { HashRouter, NavLink, Route, Routes } from "react-router-dom";
import { mockMode } from "./api";
import { Blocks } from "./pages/Blocks";
import { Calendar } from "./pages/Calendar";
import { Home } from "./pages/Home";
import { Plans } from "./pages/Plans";

export function App() {
  return (
    // Hash routing: the static build needs no server-side route fallback under /app/.
    <HashRouter>
      {mockMode && <div className="banner info">Development mock data. Open from Telegram for your real plans.</div>}
      <main>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/week" element={<Calendar />} />
          <Route path="/plans" element={<Plans />} />
          <Route path="/busy" element={<Blocks />} />
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
