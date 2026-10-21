import { useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";
import { useApi } from "../hooks";
import { inr, when } from "../format";
import { Empty, ErrorBox, Spinner } from "./ui";

const RANGES = [["1d", "1 Day"], ["1w", "1 Week"], ["1m", "1 Month"], ["all", "All time"]];

/* Custom Pulsing Green Dot at the latest tip */
const NeonBlinkingDot = (props) => {
  const { cx, cy, index, dataLength } = props;
  if (index !== dataLength - 1) return null;
  return (
    <g>
      {/* Outer Glow Halo */}
      <circle cx={cx} cy={cy} r={16} fill="rgba(0, 255, 136, 0.15)">
        <animate attributeName="r" values="8;20;8" dur="1.8s" repeatCount="indefinite" />
        <animate attributeName="opacity" values="0.8;0;0.8" dur="1.8s" repeatCount="indefinite" />
      </circle>
      <circle cx={cx} cy={cy} r={9} fill="rgba(0, 255, 136, 0.4)">
        <animate attributeName="r" values="6;12;6" dur="1.8s" repeatCount="indefinite" />
      </circle>
      {/* Main Core Dot */}
      <circle cx={cx} cy={cy} r={5} fill="#00ff88" stroke="#ffffff" strokeWidth={2} filter="url(#neon-glow-filter)" />
    </g>
  );
};

function Tip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const timeStr = typeof label === "number" ? when(new Date(label).toISOString()) : label;
  return (
    <div className="tooltip neon-tooltip" style={{
      background: "rgba(10, 20, 15, 0.92)",
      border: "1px solid rgba(0, 255, 136, 0.4)",
      boxShadow: "0 8px 32px rgba(0, 255, 136, 0.25)",
      color: "#f0fff4"
    }}>
      <div className="small" style={{ color: "#a7f3d0", marginBottom: 4 }}>{timeStr}</div>
      {payload.map((p) => (
        <div key={p.dataKey || "price"} style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ color: "#00ff88", fontWeight: 700, fontSize: "1.05rem" }}>{inr(p.value)}</span>
          <span className="small" style={{ color: "#6ee7b7" }}>{p.dataKey === "price" ? "" : p.dataKey}</span>
        </div>
      ))}
    </div>
  );
}

export default function PriceChart({ productId, target, refreshKey, paused = false }) {
  const [range, setRange] = useState("all");
  const [chartMode, setChartMode] = useState("keepa"); // 'keepa' (full amazon lifetime) | 'radar' (recorded)

  const { data, error, loading, reload } = useApi(`/products/${productId}/history`, {
    params: { range, _r: refreshKey },
    pollMs: paused ? 60000 : 15000,
  });

  const allTimeMeta = data?.all_time_meta;

  // Local live tick counter for 15-second visual update
  const [liveTicks, setLiveTicks] = useState(0);
  useEffect(() => {
    if (paused) return;
    const interval = setInterval(() => {
      setLiveTicks((t) => t + 1);
    }, 15000);
    return () => clearInterval(interval);
  }, [paused]);

  // Transform data for Historical Chart (Left)
  const historyRows = useMemo(() => {
    if (!data || !data.points || data.points.length === 0) return [];
    return data.points.map((p) => ({
      t: p.t ? new Date(p.t).getTime() : Date.now(),
      price: Number(p.price),
      label: p.t ? new Date(p.t).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : ""
    }));
  }, [data]);

  // Transform data for Real-Time 15-Second Fetch Chart (Right)
  const liveRows = useMemo(() => {
    if (!data) return [];
    const raw = data.raw_samples && data.raw_samples.length > 0
      ? data.raw_samples
      : data.points || [];

    if (raw.length === 0) return [];

    // Map each sample to a 15-second tracking sequence
    return raw.map((s, idx) => ({
      t: s.t ? (typeof s.t === "number" ? s.t : new Date(s.t).getTime()) : Date.now() - (raw.length - idx) * 15000,
      price: Number(s.price),
      fetchSeq: `#${idx + 1} (${new Date(s.t || Date.now()).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })})`
    }));
  }, [data, liveTicks]);

  const st = data?.stats;
  const currentPrice = st?.current || (historyRows.length ? historyRows[historyRows.length - 1].price : null);

  return (
    <div className="neon-price-charts-container">
      {/* Glow SVG Defs */}
      <svg style={{ position: "absolute", width: 0, height: 0 }}>
        <defs>
          <filter id="neon-glow-filter" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="3.5" result="coloredBlur" />
            <feMerge>
              <feMergeNode in="coloredBlur" />
              <feMergeNode in="coloredBlur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          <linearGradient id="neonGreenGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#00ff88" stopOpacity={0.45} />
            <stop offset="60%" stopColor="#00ff88" stopOpacity={0.12} />
            <stop offset="100%" stopColor="#00ff88" stopOpacity={0.0} />
          </linearGradient>
        </defs>
      </svg>

      {/* Toolbar */}
      <div className="toolbar" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span className="label" style={{ margin: 0, letterSpacing: "0.08em", fontWeight: 700, color: "var(--accent)" }}>
            DUAL RADAR ANALYTICS
          </span>
          <div className="seg" role="tablist" aria-label="Date range">
            {RANGES.map(([k, l]) => (
              <button
                key={k}
                role="tab"
                aria-selected={range === k}
                className={range === k ? "on" : ""}
                onClick={() => setRange(k)}
              >
                {l}
              </button>
            ))}
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {!paused ? (
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.82rem", color: "#059669", background: "rgba(16, 185, 129, 0.12)", padding: "4px 12px", borderRadius: 999, border: "1px solid rgba(16, 185, 129, 0.3)" }}>
              <span className="neon-pulse-dot" style={{ width: 8, height: 8, borderRadius: "50%", background: "#00ff88", boxShadow: "0 0 8px #00ff88" }} />
              <strong>Live 15s Radar: ACTIVE</strong>
            </div>
          ) : (
            <div style={{ fontSize: "0.82rem", color: "var(--warn)", background: "rgba(196,119,0,0.1)", padding: "4px 12px", borderRadius: 999, border: "1px solid rgba(196,119,0,0.2)" }}>
              ⏸ 15s Radar: PAUSED
            </div>
          )}
        </div>
      </div>

      {loading && !data && <Spinner label="Synchronizing price radar…" />}
      <ErrorBox error={error} onRetry={reload} />

      {/* Dual Side-by-Side Neon Graphs */}
      <div className="dual-graph-grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 18, marginBottom: 20 }}>
        
        {/* GRAPH 1: All-Time Product History (Since Listed on Platform) */}
        <div className="neon-graph-panel" style={{
          background: "linear-gradient(180deg, rgba(8, 20, 16, 0.94) 0%, rgba(5, 14, 11, 0.96) 100%)",
          borderRadius: "var(--r)",
          border: "1px solid rgba(0, 255, 136, 0.25)",
          padding: "18px 18px 12px",
          boxShadow: "0 12px 36px rgba(0, 0, 0, 0.3), inset 0 0 20px rgba(0, 255, 136, 0.04)",
          position: "relative"
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <div>
              <div style={{ fontSize: "0.95rem", fontWeight: 700, color: "#f0fff4", display: "flex", alignItems: "center", gap: 8 }}>
                <span>🌐 All-Time Lifetime History</span>
                {allTimeMeta?.has_all_time && (
                  <span className="chip" style={{ background: "rgba(0, 255, 136, 0.15)", color: "#00ff88", border: "1px solid rgba(0, 255, 136, 0.3)", padding: "2px 8px", fontSize: "0.72rem" }}>
                    Amazon Lifetime
                  </span>
                )}
              </div>
              <div className="small" style={{ color: "#6ee7b7", opacity: 0.8 }}>
                Every historical price drop & hike since the product launched on store
              </div>
            </div>

            {/* Mode Toggle if All-Time Amazon chart is available */}
            {allTimeMeta?.has_all_time && (
              <div className="seg" style={{ margin: 0, scale: "0.9" }}>
                <button
                  type="button"
                  className={chartMode === "keepa" ? "on" : ""}
                  onClick={() => setChartMode("keepa")}
                  style={{ padding: "4px 10px", fontSize: "0.75rem" }}
                >
                  Store Lifetime
                </button>
                <button
                  type="button"
                  className={chartMode === "radar" ? "on" : ""}
                  onClick={() => setChartMode("radar")}
                  style={{ padding: "4px 10px", fontSize: "0.75rem" }}
                >
                  Radar Curve
                </button>
              </div>
            )}
          </div>

          <div style={{ height: 260, position: "relative" }}>
            {allTimeMeta?.has_all_time && chartMode === "keepa" ? (
              <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", position: "relative", borderRadius: "var(--r-sm)", overflow: "hidden", background: "#05110b" }}>
                <img
                  src={allTimeMeta.keepa_image_url}
                  alt="Official Amazon all-time product price history curve"
                  style={{ width: "100%", height: "100%", objectFit: "contain", filter: "contrast(1.1) brightness(1.05) drop-shadow(0 0 12px rgba(0,255,136,0.2))" }}
                  onError={(e) => {
                    // Fall back to radar curve if image load fails
                    setChartMode("radar");
                  }}
                />
                <div style={{ position: "absolute", bottom: 4, right: 8, fontSize: "0.68rem", color: "#6ee7b7", background: "rgba(0,0,0,0.65)", padding: "2px 6px", borderRadius: 4 }}>
                  Amazon ASIN: {allTimeMeta.amazon_asin} (All-Time Full History)
                </div>
              </div>
            ) : historyRows.length < 1 ? (
              <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", color: "#6ee7b7" }}>
                <span>Awaiting first observation...</span>
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={historyRows} margin={{ top: 12, right: 18, bottom: 0, left: -10 }}>
                  <CartesianGrid stroke="rgba(0, 255, 136, 0.08)" strokeDasharray="3 3" vertical={false} />
                  <XAxis
                    dataKey="t"
                    type="number"
                    scale="time"
                    domain={["dataMin", "dataMax"]}
                    stroke="#4ade80"
                    tick={{ fill: "#6ee7b7", fontSize: 10 }}
                    tickLine={false}
                    axisLine={{ stroke: "rgba(0, 255, 136, 0.2)" }}
                    tickFormatter={(t) => new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short" })}
                  />
                  <YAxis
                    stroke="#4ade80"
                    tick={{ fill: "#6ee7b7", fontSize: 10 }}
                    width={55}
                    domain={["auto", "auto"]}
                    tickLine={false}
                    axisLine={false}
                    tickFormatter={(v) => `₹${v >= 1000 ? `${(v / 1000).toFixed(0)}k` : v}`}
                  />
                  <Tooltip content={<Tip />} />
                  {target && (
                    <ReferenceLine
                      y={Number(target)}
                      stroke="#f59e0b"
                      strokeDasharray="4 4"
                      label={{ value: `Target ${inr(target)}`, fill: "#fbbf24", fontSize: 10, position: "insideTopLeft" }}
                    />
                  )}
                  <Area
                    type="monotone"
                    dataKey="price"
                    stroke="#00ff88"
                    strokeWidth={3}
                    fill="url(#neonGreenGradient)"
                    filter="url(#neon-glow-filter)"
                    dot={<NeonBlinkingDot dataLength={historyRows.length} />}
                    isAnimationActive={true}
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        {/* GRAPH 2: Live 15-Second Fetch Tracker */}
        <div className="neon-graph-panel" style={{
          background: "linear-gradient(180deg, rgba(8, 20, 16, 0.94) 0%, rgba(5, 14, 11, 0.96) 100%)",
          borderRadius: "var(--r)",
          border: "1px solid rgba(0, 255, 136, 0.35)",
          padding: "18px 18px 12px",
          boxShadow: "0 12px 36px rgba(0, 0, 0, 0.3), 0 0 24px rgba(0, 255, 136, 0.08)",
          position: "relative"
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <div>
              <div style={{ fontSize: "0.95rem", fontWeight: 700, color: "#f0fff4", display: "flex", alignItems: "center", gap: 8 }}>
                <span className="neon-pulse-dot" style={{ width: 8, height: 8, borderRadius: "50%", background: "#00ff88", boxShadow: "0 0 10px #00ff88" }} />
                ⏱ Live 15-Second Fetch Stream
              </div>
              <div className="small" style={{ color: "#6ee7b7", opacity: 0.8 }}>
                Every real-time check plotted tick-by-tick
              </div>
            </div>
            <span className="chip" style={{ background: "rgba(0, 255, 136, 0.15)", color: "#00ff88", border: "1px solid rgba(0, 255, 136, 0.3)", fontWeight: 700 }}>
              {liveRows.length} Fetches
            </span>
          </div>

          <div style={{ height: 260 }}>
            {liveRows.length < 1 ? (
              <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", color: "#6ee7b7" }}>
                <span>Starting first 15s live fetch...</span>
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={liveRows} margin={{ top: 12, right: 18, bottom: 0, left: -10 }}>
                  <CartesianGrid stroke="rgba(0, 255, 136, 0.08)" strokeDasharray="3 3" vertical={false} />
                  <XAxis
                    dataKey="t"
                    type="number"
                    scale="time"
                    domain={["dataMin", "dataMax"]}
                    stroke="#4ade80"
                    tick={{ fill: "#6ee7b7", fontSize: 10 }}
                    tickLine={false}
                    axisLine={{ stroke: "rgba(0, 255, 136, 0.2)" }}
                    tickFormatter={(t) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                  />
                  <YAxis
                    stroke="#4ade80"
                    tick={{ fill: "#6ee7b7", fontSize: 10 }}
                    width={55}
                    domain={["auto", "auto"]}
                    tickLine={false}
                    axisLine={false}
                    tickFormatter={(v) => `₹${v >= 1000 ? `${(v / 1000).toFixed(0)}k` : v}`}
                  />
                  <Tooltip content={<Tip />} />
                  <Line
                    type="stepAfter"
                    dataKey="price"
                    stroke="#00ff88"
                    strokeWidth={3}
                    filter="url(#neon-glow-filter)"
                    dot={<NeonBlinkingDot dataLength={liveRows.length} />}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

      </div>

      {/* Stats Summary Strip */}
      {st && (
        <div className="stats-row" style={{ marginTop: 0 }}>
          {[
            ["Current", st.current],
            ["Lowest Recorded", st.min],
            ["Highest Recorded", st.max],
            ["Time-Weighted Average", st.average],
            ["Sample Count", st.count]
          ].map(([l, v]) => (
            <div key={l} style={{ background: "var(--glass-2)", padding: "10px 14px", borderRadius: "var(--r-sm)", border: "1px solid var(--line-2)" }}>
              <span className="label">{l}</span>
              <strong style={{ color: l === "Lowest Recorded" ? "var(--good)" : "var(--text)" }}>
                {typeof v === "number" || (typeof v === "string" && !isNaN(v) && l !== "Sample Count") ? inr(v) : v}
              </strong>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
