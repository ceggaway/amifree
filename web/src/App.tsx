import { BackButton } from "./components/BackButton";
import { Icon } from "./components/Icon";
import { useEffect, useRef } from "react";
import { HashRouter, Link, Route, Routes, useNavigate } from "react-router-dom";
import { mockMode } from "./api";
import { Blocks } from "./pages/Blocks";
import { Calendar } from "./pages/Calendar";
import { FindTime } from "./pages/FindTime";
import { GroupEvent } from "./pages/GroupEvent";
import { GroupHome } from "./pages/GroupHome";
import { NewEvent } from "./pages/NewEvent";
import { GroupStats } from "./pages/GroupStats";
import { Poll } from "./pages/Poll";
import { MyTimes } from "./pages/MyTimes";
import { Home } from "./pages/Home";
import { Plans } from "./pages/Plans";
import { startParam } from "./telegram";

/** Where a link from a group opens: t.me/<bot>/<app>?startapp=<page>_<id>. */
export function startPath(param: string): string | null {
  const poll = /^(poll|mine)_([0-9a-f-]{36})$/i.exec(param);
  if (poll) return `/poll/${poll[2]}${poll[1] === "mine" ? "/mine" : ""}`;
  const event = /^ev_([0-9a-f-]{36})$/i.exec(param);
  if (event) return `/e/${event[1]}`;
  const group = /^(g|new|find)_([np]\d{1,20})$/.exec(param);
  if (group) return `/g/${group[2]}${group[1] === "g" ? "" : `/${group[1]}`}`;
  return null;
}

/** Opens the page a group link points to, once, when the app starts. React Router gives a
 * new `navigate` after every page change, so without the `done` guard this effect ran again
 * on each tap and sent people back to the start page. */
export function StartRoute() {
  const navigate = useNavigate();
  const done = useRef(false);
  useEffect(() => {
    if (done.current) return;
    done.current = true;
    const path = startPath(startParam());
    if (path) navigate(path, { replace: true });
  }, [navigate]);
  return null;
}

export function App() {
  return (
    // Hash routing: the static build needs no server-side route fallback under /app/.
    <HashRouter>
      {mockMode && <div className="banner info">Development mock data. Open from Telegram for your real plans.</div>}
      <StartRoute />
      <header className="app-header">
        <BackButton />
        <Link to="/" className="brand">
          <span className="brand-mark"><Icon name="calendar" size={19} /></span>
          AmIFree
          <span className="brand-caption">Make time together</span>
        </Link>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/week" element={<Calendar />} />
          <Route path="/plans" element={<Plans />} />
          <Route path="/busy" element={<Blocks />} />
          <Route path="/poll/:id" element={<Poll />} />
          <Route path="/poll/:id/mine" element={<MyTimes />} />
          <Route path="/g/:code" element={<GroupHome />} />
          <Route path="/g/:code/new" element={<NewEvent />} />
          <Route path="/g/:code/find" element={<FindTime />} />
          <Route path="/g/:code/stats" element={<GroupStats />} />
          <Route path="/e/:id" element={<GroupEvent />} />
        </Routes>
      </main>
    </HashRouter>
  );
}
