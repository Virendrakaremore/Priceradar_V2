import { useEffect, useState } from "react";
import { api } from "../api";
import { ago } from "../format";

const DURATIONS = [
  { label: "15 min", mins: 15, desc: "Quick flash check" },
  { label: "30 min", mins: 30, desc: "Short monitor" },
  { label: "1 hr", mins: 60, desc: "Standard cycle" },
  { label: "2 hr", mins: 120, desc: "Extended watch" },
  { label: "1 day", mins: 1440, desc: "Full 24h cycle" },
  { label: "2 days", mins: 2880, desc: "48h weekend" },
  { label: "Continuous", mins: 0, desc: "No auto-stop" },
];

export function TrackingDurationCard({ productId, isPaused, trackUntil, onUpdated }) {
  const [selectedMins, setSelectedMins] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [timeLeft, setTimeLeft] = useState(null);

  // Calculate live countdown timer
  useEffect(() => {
    if (!trackUntil || isPaused) {
      setTimeLeft(null);
      return;
    }
    const updateCountdown = () => {
      const targetTime = new Date(trackUntil).getTime();
      const now = Date.now();
      const diffMs = targetTime - now;
      if (diffMs <= 0) {
        setTimeLeft("Expired (Tracking Auto-Stopped)");
      } else {
        const totalSecs = Math.floor(diffMs / 1000);
        const hours = Math.floor(totalSecs / 3600);
        const mins = Math.floor((totalSecs % 3600) / 60);
        const secs = totalSecs % 60;
        if (hours > 0) {
          setTimeLeft(`${hours}h ${mins}m ${secs}s remaining`);
        } else {
          setTimeLeft(`${mins}m ${secs}s remaining`);
        }
      }
    };
    updateCountdown();
    const interval = setInterval(updateCountdown, 1000);
    return () => clearInterval(interval);
  }, [trackUntil, isPaused]);

  const applyDuration = async (mins) => {
    setBusy(true);
    setMsg(null);
    try {
      await api(`/products/${productId}`, {
        method: "PATCH",
        body: { track_duration_minutes: mins },
      });
      setSelectedMins(mins);
      setMsg(mins === 0 ? "Continuous tracking enabled" : `Set to auto-stop in ${mins < 60 ? `${mins}m` : `${mins / 60}h`}`);
      if (onUpdated) onUpdated();
    } catch (err) {
      setMsg("Failed: " + err.message);
    } finally {
      setBusy(false);
    }
  };

  const stopTrackingNow = async () => {
    setBusy(true);
    try {
      await api(`/products/${productId}`, {
        method: "PATCH",
        body: { paused: true, track_duration_minutes: 0 },
      });
      if (onUpdated) onUpdated();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="duration-quiet-window" style={{ display: "flex", flexDirection: "column", height: "100%", justifyContent: "space-between" }}>
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <h2 style={{ fontSize: "1.05rem", margin: 0, display: "flex", alignItems: "center", gap: 6 }}>
            ⏱ Tracking Window
          </h2>
          <span
            className="chip"
            style={{
              fontSize: "0.72rem",
              margin: 0,
              background: !isPaused ? "rgba(16, 185, 129, 0.12)" : "rgba(196, 119, 0, 0.1)",
              color: !isPaused ? "var(--good)" : "var(--warn)",
              border: !isPaused ? "1px solid rgba(16, 185, 129, 0.3)" : "1px solid rgba(196, 119, 0, 0.25)",
            }}
          >
            {!isPaused ? "🟢 Active" : "⏸ Paused"}
          </span>
        </div>

        <p className="muted small" style={{ margin: "0 0 10px", lineHeight: 1.35 }}>
          Auto-stop after selected duration to prevent retailer rate limits & bot challenges.
        </p>

        {timeLeft && !isPaused && (
          <div
            style={{
              padding: "6px 10px",
              background: "rgba(79, 110, 247, 0.1)",
              border: "1px solid rgba(79, 110, 247, 0.25)",
              borderRadius: "var(--r-sm)",
              fontSize: "0.8rem",
              fontWeight: 700,
              color: "var(--accent)",
              display: "flex",
              alignItems: "center",
              gap: 6,
              marginBottom: 10,
            }}
          >
            <span className="neon-pulse-dot" style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--accent)" }} />
            {timeLeft}
          </div>
        )}

        <div className="duration-chip-group">
          {DURATIONS.map((d) => (
            <button
              key={d.label}
              type="button"
              disabled={busy}
              className={`duration-chip ${selectedMins === d.mins ? "selected" : ""}`}
              onClick={() => applyDuration(d.mins)}
              title={d.desc}
            >
              {d.label}
            </button>
          ))}
        </div>
      </div>

      <div style={{ marginTop: 12, paddingTop: 10, borderTop: "1px solid var(--line)" }}>
        {!isPaused ? (
          <button
            type="button"
            className="btn btn-danger"
            style={{ width: "100%", padding: "6px 12px", fontSize: "0.82rem" }}
            disabled={busy}
            onClick={stopTrackingNow}
          >
            ⏹ Stop Tracking Now
          </button>
        ) : (
          <button
            type="button"
            className="btn btn-primary"
            style={{ width: "100%", padding: "6px 12px", fontSize: "0.82rem", background: "#10b981", borderColor: "transparent", color: "#fff" }}
            disabled={busy}
            onClick={() => applyDuration(60)}
          >
            ▶ Start 1-Hour Track
          </button>
        )}
      </div>
    </div>
  );
}
