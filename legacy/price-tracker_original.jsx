import { useState, useEffect, useRef, useCallback } from "react";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from "recharts";

const BACKEND = "http://localhost:5000";

const COLORS = {
  bg: "#0a0a0f", panel: "#111118", border: "#1e1e2e",
  accent: "#00e5ff", accentDim: "#00e5ff22",
  green: "#00ff88", red: "#ff4466", yellow: "#ffd700",
  text: "#e0e0ff", muted: "#5a5a7a",
};

function detectPlatform(url) {
  if (url.toLowerCase().includes("amazon")) return { name: "Amazon", color: "#ff9900", icon: "🛒" };
  if (url.toLowerCase().includes("flipkart")) return { name: "Flipkart", color: "#2874f0", icon: "🏪" };
  return { name: "Unknown", color: "#888", icon: "🔗" };
}

const CustomTooltip = ({ active, payload, label }) => {
  if (active && payload && payload.length) {
    return (
      <div style={{ background: "#0d0d1a", border: `1px solid #00e5ff44`, borderRadius: 8, padding: "10px 14px", fontSize: 12, fontFamily: "'Space Mono', monospace" }}>
        <div style={{ color: "#5a5a7a", marginBottom: 4 }}>{label}</div>
        <div style={{ color: "#00e5ff", fontSize: 16, fontWeight: "bold" }}>₹{payload[0].value.toLocaleString("en-IN")}</div>
      </div>
    );
  }
  return null;
};

export default function PriceTracker() {
  const [url, setUrl] = useState("");
  const [product, setProduct] = useState(null);
  const [priceData, setPriceData] = useState([]);
  const [isTracking, setIsTracking] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [currentPrice, setCurrentPrice] = useState(null);
  const [minPrice, setMinPrice] = useState(null);
  const [maxPrice, setMaxPrice] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [tick, setTick] = useState(0);
  const [backendStatus, setBackendStatus] = useState("checking");
  const [errorMsg, setErrorMsg] = useState("");
  const intervalRef = useRef(null);
  const alertIdRef = useRef(0);
  const prevPriceRef = useRef(null);

  useEffect(() => {
    fetch(`${BACKEND}/api/health`)
      .then(r => r.json())
      .then(() => setBackendStatus("ok"))
      .catch(() => setBackendStatus("error"));
  }, []);

  const addAlert = useCallback((msg, type = "info") => {
    const id = alertIdRef.current++;
    setAlerts(prev => [{ id, msg, type, time: new Date().toLocaleTimeString() }, ...prev.slice(0, 6)]);
  }, []);

  const startTracking = async () => {
    if (!url.trim()) return;
    setIsLoading(true); setErrorMsg(""); setAlerts([]); setPriceData([]);
    try {
      const res = await fetch(`${BACKEND}/api/product?url=${encodeURIComponent(url.trim())}`);
      const data = await res.json();
      if (!data.success || !data.price) {
        setErrorMsg(data.error || "Could not fetch price. May be a CAPTCHA or invalid URL.");
        setIsLoading(false); return;
      }
      const platform = detectPlatform(url);
      const p = {
        name: data.name, platform: { ...platform, name: data.platform || platform.name },
        basePrice: data.price, originalPrice: data.mrp || Math.round(data.price * 1.2),
        rating: data.rating || "N/A", reviews: data.reviews || 0,
      };
      setProduct(p); setCurrentPrice(data.price);
      prevPriceRef.current = data.price;
      setMinPrice(data.price); setMaxPrice(data.price);
      setPriceData([{ time: new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }), price: data.price }]);
      setIsTracking(true);
      addAlert(`✅ Tracking started on ${data.platform || platform.name}`, "success");
      addAlert(`📦 ${data.name?.slice(0, 55)}...`, "info");
    } catch (e) {
      setErrorMsg("Cannot connect to backend. Make sure: python app.py is running.");
    }
    setIsLoading(false);
  };

  const stopTracking = () => { setIsTracking(false); clearInterval(intervalRef.current); addAlert("⏹ Tracking paused", "info"); };

  useEffect(() => {
    if (!isTracking || !url) return;
    intervalRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${BACKEND}/api/price?url=${encodeURIComponent(url.trim())}`);
        const data = await res.json();
        if (!data.success || !data.price) return;
        const newPrice = data.price; const prev = prevPriceRef.current;
        if (prev && Math.abs(newPrice - prev) > 1) {
          if (newPrice < prev) addAlert(`📉 Dropped ₹${(prev - newPrice).toLocaleString("en-IN")} → ₹${newPrice.toLocaleString("en-IN")}`, "success");
          else addAlert(`📈 Rose ₹${(newPrice - prev).toLocaleString("en-IN")} → ₹${newPrice.toLocaleString("en-IN")}`, "warning");
        }
        prevPriceRef.current = newPrice;
        setCurrentPrice(newPrice);
        setMinPrice(p => Math.min(p ?? newPrice, newPrice));
        setMaxPrice(p => Math.max(p ?? newPrice, newPrice));
        setTick(t => t + 1);
        setPriceData(prev => [...prev.slice(-60), { time: new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }), price: newPrice }]);
      } catch (e) { addAlert("⚠️ Poll failed — retrying...", "warning"); }
    }, 15000);
    return () => clearInterval(intervalRef.current);
  }, [isTracking, url, addAlert]);

  const priceChange = currentPrice && product ? currentPrice - product.basePrice : 0;
  const pricePct = product ? Math.abs((priceChange / product.basePrice) * 100).toFixed(2) : 0;
  const trend = priceChange > 0 ? "up" : priceChange < 0 ? "down" : "flat";

  return (
    <div style={{ minHeight: "100vh", background: COLORS.bg, fontFamily: "'Space Mono', monospace", color: COLORS.text, backgroundImage: `radial-gradient(ellipse at 20% 20%, #00e5ff0a 0%, transparent 50%), radial-gradient(ellipse at 80% 80%, #2874f022 0%, transparent 50%)` }}>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Orbitron:wght@700;900&display=swap'); *{box-sizing:border-box;margin:0;padding:0} input:focus{outline:none} ::-webkit-scrollbar{width:4px} ::-webkit-scrollbar-thumb{background:#00e5ff44;border-radius:2px} @keyframes pulse{0%,100%{opacity:1}50%{opacity:0.3}} @keyframes slideIn{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:translateY(0)}} .btn{transition:all 0.2s;cursor:pointer;border:none} .btn:hover:not(:disabled){transform:translateY(-1px);filter:brightness(1.15)} .btn:disabled{opacity:0.5;cursor:not-allowed}`}</style>

      {/* Header */}
      <div style={{ padding: "20px 32px", borderBottom: `1px solid ${COLORS.border}`, display: "flex", alignItems: "center", justifyContent: "space-between", background: `${COLORS.panel}cc`, backdropFilter: "blur(12px)", position: "sticky", top: 0, zIndex: 100 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{ width: 36, height: 36, borderRadius: 8, background: `linear-gradient(135deg, ${COLORS.accent}, #0066ff)`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18 }}>📡</div>
          <div>
            <div style={{ fontFamily: "'Orbitron', monospace", fontSize: 18, color: COLORS.accent, letterSpacing: 2 }}>PRICERADAR</div>
            <div style={{ fontSize: 10, color: COLORS.muted, letterSpacing: 1 }}>LIVE PRICE INTELLIGENCE · REAL DATA</div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
            <div style={{ width: 7, height: 7, borderRadius: "50%", background: backendStatus === "ok" ? COLORS.green : backendStatus === "error" ? COLORS.red : COLORS.yellow, animation: backendStatus === "ok" ? "pulse 2s infinite" : "none" }} />
            <span style={{ color: backendStatus === "ok" ? COLORS.green : backendStatus === "error" ? COLORS.red : COLORS.yellow }}>
              {backendStatus === "ok" ? "BACKEND LIVE" : backendStatus === "error" ? "BACKEND OFFLINE" : "CONNECTING..."}
            </span>
          </div>
          {isTracking && <div style={{ fontSize: 11, color: COLORS.accent }}>📊 {tick} POLLS DONE</div>}
        </div>
      </div>

      <div style={{ padding: "24px 32px", maxWidth: 1100, margin: "0 auto" }}>

        {backendStatus === "error" && (
          <div style={{ background: "#ff446611", border: `1px solid ${COLORS.red}44`, borderRadius: 10, padding: "14px 20px", marginBottom: 16, fontSize: 12 }}>
            <span style={{ color: COLORS.red }}>⚠️ Backend offline — </span>
            <span style={{ color: COLORS.muted }}>Open a new terminal and run: </span>
            <code style={{ color: COLORS.yellow, background: "#ffffff0a", padding: "2px 8px", borderRadius: 4 }}>python app.py</code>
            <span style={{ color: COLORS.muted }}> then refresh this page.</span>
          </div>
        )}

        {/* URL Input */}
        <div style={{ background: COLORS.panel, border: `1px solid ${COLORS.border}`, borderRadius: 12, padding: "20px 24px", marginBottom: 24 }}>
          <div style={{ fontSize: 11, color: COLORS.muted, letterSpacing: 2, marginBottom: 12 }}>PASTE PRODUCT URL</div>
          <div style={{ display: "flex", gap: 12 }}>
            <input value={url} onChange={e => setUrl(e.target.value)}
              onKeyDown={e => e.key === "Enter" && !isTracking && !isLoading && startTracking()}
              placeholder="https://www.amazon.in/... or https://www.flipkart.com/..."
              style={{ flex: 1, background: "#0a0a0f", border: `1px solid ${url ? COLORS.accent + "66" : COLORS.border}`, borderRadius: 8, padding: "12px 16px", color: COLORS.text, fontFamily: "'Space Mono', monospace", fontSize: 13, transition: "border 0.2s" }}
            />
            {!isTracking
              ? <button className="btn" onClick={startTracking} disabled={!url.trim() || isLoading || backendStatus !== "ok"}
                  style={{ background: `linear-gradient(135deg, ${COLORS.accent}, #0099ff)`, color: "#000", borderRadius: 8, padding: "12px 28px", fontFamily: "'Space Mono', monospace", fontWeight: "bold", fontSize: 13, letterSpacing: 1, whiteSpace: "nowrap", minWidth: 160 }}>
                  {isLoading ? "⏳ FETCHING..." : "▶ START TRACK"}
                </button>
              : <button className="btn" onClick={stopTracking}
                  style={{ background: `linear-gradient(135deg, ${COLORS.red}, #cc0033)`, color: "#fff", borderRadius: 8, padding: "12px 28px", fontFamily: "'Space Mono', monospace", fontWeight: "bold", fontSize: 13, letterSpacing: 1, whiteSpace: "nowrap" }}>
                  ⏹ STOP
                </button>
            }
          </div>
          {errorMsg && <div style={{ marginTop: 10, fontSize: 12, color: COLORS.red, padding: "8px 12px", background: "#ff446611", borderRadius: 6 }}>❌ {errorMsg}</div>}
          <div style={{ fontSize: 10, color: COLORS.muted, marginTop: 8 }}>🔄 Polls real price every 15 seconds via Chromium browser scraping</div>
        </div>

        {isLoading && (
          <div style={{ textAlign: "center", padding: "60px", color: COLORS.muted }}>
            <div style={{ fontSize: 36, marginBottom: 16 }}>🔍</div>
            <div style={{ fontFamily: "'Orbitron', monospace", fontSize: 13, color: COLORS.accent, letterSpacing: 2 }}>OPENING PAGE WITH CHROMIUM...</div>
            <div style={{ fontSize: 11, marginTop: 8 }}>This takes 5–15 seconds — fetching real price data</div>
          </div>
        )}

        {product && !isLoading && (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 16, marginBottom: 16 }}>
              <div style={{ background: COLORS.panel, border: `1px solid ${COLORS.border}`, borderRadius: 12, padding: "20px 24px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
                  <span style={{ background: product.platform.color + "22", color: product.platform.color, border: `1px solid ${product.platform.color}44`, padding: "3px 10px", borderRadius: 20, fontSize: 11, fontWeight: "bold", letterSpacing: 1 }}>
                    {product.platform.icon} {product.platform.name.toUpperCase()}
                  </span>
                  {product.rating !== "N/A" && <span style={{ fontSize: 11, color: COLORS.muted }}>⭐ {product.rating} {product.reviews > 0 && `(${product.reviews.toLocaleString("en-IN")} reviews)`}</span>}
                </div>
                <div style={{ fontSize: 15, fontWeight: "bold", color: COLORS.text, lineHeight: 1.5 }}>{product.name}</div>
                <div style={{ fontSize: 11, color: COLORS.muted, marginTop: 6 }}>MRP: <span style={{ textDecoration: "line-through" }}>₹{product.originalPrice.toLocaleString("en-IN")}</span> · Start: ₹{product.basePrice.toLocaleString("en-IN")}</div>
              </div>
              <div style={{ background: COLORS.panel, border: `1px solid ${trend === "up" ? COLORS.red + "66" : trend === "down" ? COLORS.green + "66" : COLORS.border}`, borderRadius: 12, padding: "20px 28px", textAlign: "center", minWidth: 185, transition: "border 0.3s" }}>
                <div style={{ fontSize: 10, color: COLORS.muted, letterSpacing: 2, marginBottom: 8 }}>LIVE PRICE</div>
                <div style={{ fontFamily: "'Orbitron', monospace", fontSize: 26, fontWeight: "900", color: trend === "up" ? COLORS.red : trend === "down" ? COLORS.green : COLORS.accent, transition: "color 0.3s" }}>
                  ₹{currentPrice?.toLocaleString("en-IN")}
                </div>
                <div style={{ fontSize: 12, color: trend === "up" ? COLORS.red : trend === "down" ? COLORS.green : COLORS.muted, marginTop: 6 }}>
                  {trend === "up" ? "▲" : trend === "down" ? "▼" : "—"} {pricePct}%
                </div>
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginBottom: 16 }}>
              {[
                { label: "LOWEST TRACKED", value: `₹${minPrice?.toLocaleString("en-IN")}`, color: COLORS.green, icon: "▼" },
                { label: "HIGHEST TRACKED", value: `₹${maxPrice?.toLocaleString("en-IN")}`, color: COLORS.red, icon: "▲" },
                { label: "SAVINGS vs MRP", value: `₹${(product.originalPrice - (currentPrice ?? 0)).toLocaleString("en-IN")}`, color: COLORS.yellow, icon: "💰" },
              ].map(stat => (
                <div key={stat.label} style={{ background: COLORS.panel, border: `1px solid ${stat.color}22`, borderRadius: 10, padding: "14px 18px" }}>
                  <div style={{ fontSize: 10, color: COLORS.muted, letterSpacing: 1 }}>{stat.icon} {stat.label}</div>
                  <div style={{ fontFamily: "'Orbitron', monospace", fontSize: 20, color: stat.color, marginTop: 4 }}>{stat.value}</div>
                </div>
              ))}
            </div>

            <div style={{ background: COLORS.panel, border: `1px solid ${COLORS.border}`, borderRadius: 12, padding: "20px 24px", marginBottom: 16 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
                <div>
                  <div style={{ fontFamily: "'Orbitron', monospace", fontSize: 13, color: COLORS.accent, letterSpacing: 2 }}>LIVE PRICE CHART</div>
                  <div style={{ fontSize: 10, color: COLORS.muted, marginTop: 2 }}>Real {product.platform.name} prices · 15s poll interval</div>
                </div>
                {isTracking && <div style={{ fontSize: 11, color: COLORS.green, display: "flex", alignItems: "center", gap: 6 }}><div style={{ width: 6, height: 6, borderRadius: "50%", background: COLORS.green, animation: "pulse 1.5s infinite" }} />LIVE</div>}
              </div>
              {priceData.length < 2
                ? <div style={{ textAlign: "center", padding: "60px", color: COLORS.muted, fontSize: 12 }}>⏳ Waiting for 2nd poll (15s)... Graph builds automatically</div>
                : <ResponsiveContainer width="100%" height={260}>
                    <LineChart data={priceData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="2 6" stroke="#1e1e2e" vertical={false} />
                      <XAxis dataKey="time" tick={{ fill: COLORS.muted, fontSize: 10, fontFamily: "'Space Mono', monospace" }} tickLine={false} axisLine={{ stroke: COLORS.border }} interval={Math.max(0, Math.floor(priceData.length / 8) - 1)} />
                      <YAxis tick={{ fill: COLORS.muted, fontSize: 10, fontFamily: "'Space Mono', monospace" }} tickLine={false} axisLine={false} tickFormatter={v => `₹${(v / 1000).toFixed(1)}k`} domain={["auto", "auto"]} width={62} />
                      <Tooltip content={<CustomTooltip />} />
                      {minPrice && <ReferenceLine y={minPrice} stroke={COLORS.green} strokeDasharray="4 4" strokeWidth={1} label={{ value: "MIN", fill: COLORS.green, fontSize: 10, fontFamily: "'Space Mono', monospace" }} />}
                      {maxPrice && maxPrice !== minPrice && <ReferenceLine y={maxPrice} stroke={COLORS.red} strokeDasharray="4 4" strokeWidth={1} label={{ value: "MAX", fill: COLORS.red, fontSize: 10, fontFamily: "'Space Mono', monospace" }} />}
                      <Line type="monotoneX" dataKey="price" stroke={COLORS.accent} strokeWidth={2.5} dot={false} activeDot={{ r: 5, fill: COLORS.accent, stroke: "#0a0a0f", strokeWidth: 2 }} isAnimationActive={false} />
                    </LineChart>
                  </ResponsiveContainer>
              }
            </div>

            {alerts.length > 0 && (
              <div style={{ background: COLORS.panel, border: `1px solid ${COLORS.border}`, borderRadius: 12, padding: "16px 20px" }}>
                <div style={{ fontSize: 10, color: COLORS.muted, letterSpacing: 2, marginBottom: 12 }}>📋 PRICE ALERT FEED</div>
                {alerts.map(a => (
                  <div key={a.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 12px", marginBottom: 6, borderRadius: 6, background: a.type === "success" ? COLORS.green + "11" : a.type === "warning" ? COLORS.red + "11" : COLORS.accentDim, border: `1px solid ${a.type === "success" ? COLORS.green + "33" : a.type === "warning" ? COLORS.red + "33" : COLORS.accent + "22"}`, fontSize: 12, animation: "slideIn 0.3s ease" }}>
                    <span style={{ color: a.type === "success" ? COLORS.green : a.type === "warning" ? COLORS.red : COLORS.accent }}>{a.msg}</span>
                    <span style={{ color: COLORS.muted, fontSize: 10, whiteSpace: "nowrap", marginLeft: 16 }}>{a.time}</span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {!product && !isLoading && (
          <div style={{ textAlign: "center", padding: "80px 32px", color: COLORS.muted }}>
            <div style={{ fontSize: 48, marginBottom: 16, opacity: 0.3 }}>📡</div>
            <div style={{ fontFamily: "'Orbitron', monospace", fontSize: 14, letterSpacing: 3, marginBottom: 8 }}>AWAITING TARGET URL</div>
            <div style={{ fontSize: 12 }}>Paste an Amazon.in or Flipkart.com product link above to begin</div>
          </div>
        )}
      </div>
    </div>
  );
}
