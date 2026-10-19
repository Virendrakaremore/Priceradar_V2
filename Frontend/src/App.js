import { useState } from "react";
import ProductPage from "./pages/ProductPage";
import TrackPage from "./pages/TrackPage";
import { Alerts, Deals, Insights, Monitoring, Notifications, Overview, Settings, Tracked } from "./pages/Lists";
import { go, useHashRoute } from "./hooks";
import { useApi } from "./hooks";
import "./styles.css";

const NAV = [["/", "Overview"], ["/track", "Track a product"], ["/tracked", "My products"], ["/alerts", "Price alerts"],
  ["/deals", "Deals"], ["/insights", "Insights"], ["/notifications", "Notifications"], ["/settings", "Settings"], ["/monitoring", "Monitoring"]];

function Router({ route }) {
  const m = route.match(/^\/products\/(\d+)$/);
  if (m) return <ProductPage key={m[1]} id={m[1]} />;
  switch (route) {
    case "/": return <Overview />;
    case "/track": return <TrackPage />;
    case "/tracked": return <Tracked />;
    case "/alerts": return <Alerts />;
    case "/deals": return <Deals />;
    case "/insights": return <Insights />;
    case "/notifications": return <Notifications />;
    case "/settings": return <Settings />;
    case "/monitoring": return <Monitoring />;
    default: return <div className="glass"><h2>Page not found</h2><a href="#/">Back to overview</a></div>;
  }
}

export default function App() {
  const route = useHashRoute();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const health = useApi("/health", { pollMs: 30000 });
  const notes = useApi("/notifications", { params: { per_page: 1 }, pollMs: 60000 });
  const backendDown = health.error?.network;
  return (
    <div className="shell">
      <aside className={`sidebar ${open ? "open" : ""}`} aria-label="Primary">
        <a className="brand" href="#/" onClick={() => setOpen(false)}><span className="brand-mark">◉</span> PriceRadar</a>
        <nav>{NAV.map(([p, l]) => <a key={p} href={`#${p}`} className={route === p ? "active" : ""} onClick={() => setOpen(false)}>{l}</a>)}</nav>
      </aside>
      <div className="main">
        <div className="topbar">
          <button className="btn btn-ghost menu-btn" aria-label="Toggle navigation" aria-expanded={open} onClick={() => setOpen(!open)}>☰</button>
          <form className="search" onSubmit={(e) => { e.preventDefault(); if (q.trim()) { try { sessionStorage.setItem("pr_prefill", q.trim()); } catch {} setQ(""); go("/track"); } }} role="search">
            <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Paste a product link to track…" aria-label="Quick track" />
          </form>
          <a className="icon-link" href="#/notifications" aria-label="Notifications">🔔{notes.data?.total ? <span className="badge">{notes.data.total}</span> : null}</a>
          <a className="icon-link" href="#/settings" aria-label="Settings">⚙</a>
        </div>
        {backendDown && <div className="notice notice-bad" role="alert">Backend unavailable — showing no data until it comes back.</div>}
        <main id="content"><Router route={route} /></main>
      </div>
    </div>
  );
}
