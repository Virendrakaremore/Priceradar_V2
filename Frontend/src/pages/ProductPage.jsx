import { useState } from "react";
import { api } from "../api";
import { AlertForm, TargetPicker } from "../components/TargetAlert";
import PriceChart from "../components/PriceChart";
import { TrackingDurationCard } from "../components/TrackingDurationCard";
import { Chip, ErrorBox, Freshness, Glass, PageHead, Spinner, Verdict } from "../components/ui";
import { ALERT_KINDS, ago, inr, pct } from "../format";
import { go, useApi } from "../hooks";

/* ── Retailer logo / colour dot ─────────────────────────────────────── */
const RETAILER_COLORS = {
  "Amazon India":     "#f90",
  "Flipkart":         "#2874f0",
  "Croma":            "#1f5c2e",
  "Reliance Digital": "#e31837",
  "Vijay Sales":      "#c8202f",
  "Tata CLiQ":        "#7b2d8b",
  "JioMart":          "#0e66c8",
};

function RetailerDot({ name }) {
  const bg = RETAILER_COLORS[name] || "#6b7280";
  return (
    <span
      aria-hidden
      style={{
        display: "inline-block",
        width: 10, height: 10, borderRadius: "50%",
        background: bg, marginRight: 7, flexShrink: 0,
        boxShadow: `0 0 0 2px ${bg}33`,
        verticalAlign: "middle",
      }}
    />
  );
}

/* ── Offer comparison card ──────────────────────────────────────────── */
function OfferCard({ o }) {
  return (
    <div className={`offer ${o.is_best ? "best" : ""} ${!o.verified ? "alt" : ""}`}>
      <div className="offer-main">
        <div>
          <RetailerDot name={o.retailer} />
          <strong className="retailer">{o.retailer}</strong>
          {o.is_best && <Chip tone="good">🏆 Lowest verified</Chip>}
          {o.match_status === "source"   && <Chip>Your link</Chip>}
          {o.match_status === "verified" && <Chip tone="good">✓ Verified match</Chip>}
          {o.match_status === "possible" && <Chip tone="warn">⚠ Possible match — verify</Chip>}
        </div>
        {o.title && <div className="muted small" style={{ marginTop: 4 }}>{o.title}</div>}
        {o.variant && <div className="muted small">Variant: {o.variant}</div>}
        {o.match_reasons?.length > 0 && (
          <div className="muted small" style={{ marginTop: 4 }}>
            {o.match_reasons.map((r, i) => <span key={i}>{i > 0 && " · "}{r}</span>)}
          </div>
        )}
      </div>

      <div className="offer-price">
        <div className="price">{o.price ? inr(o.price) : <span className="muted">Unavailable</span>}</div>
        {o.mrp && (
          <div className="muted small">
            <s>{inr(o.mrp)}</s>
            {o.discount && <span className="down"> {o.discount.pct}% off</span>}
          </div>
        )}
        <div className="muted small">
          {o.total_payable ? `Total ${inr(o.total_payable)}` : "Delivery cost unknown"}
        </div>
        <div className="small">
          {o.availability === "in_stock"
            ? <span className="down">● In stock</span>
            : o.availability === "out_of_stock"
              ? <span style={{ color: "var(--bad)" }}>● Out of stock</span>
              : <span className="muted">● Stock unknown</span>}
        </div>
      </div>

      <div className="offer-meta">
        {o.verified
          ? <Freshness offer={o} />
          : <span className="muted small">
              Price captured at discovery {ago(o.last_success_at)} — monitored at reduced frequency
            </span>}
        {o.last_error && o.status !== "ok" && (
          <div className="small warn-text">{o.last_error}</div>
        )}
        <a className="btn btn-ghost" href={o.url} target="_blank" rel="noopener noreferrer">
          View on {o.retailer} ↗
        </a>
      </div>
    </div>
  );
}

/* ── Price comparison summary bar ───────────────────────────────────── */
function ComparisonSummary({ offers }) {
  const priced = offers.filter(o => o.price && o.match_status !== "possible");
  if (priced.length < 2) return null;
  const prices = priced.map(o => ({ retailer: o.retailer, price: parseFloat(o.price) }));
  const best = Math.min(...prices.map(p => p.price));
  const worst = Math.max(...prices.map(p => p.price));
  const saving = worst - best;
  if (saving < 1) return null;
  const bestRetailer = prices.find(p => p.price === best)?.retailer;
  const worstRetailer = prices.find(p => p.price === worst)?.retailer;
  return (
    <div className="notice notice-good" style={{ marginBottom: 16 }}>
      💰 <strong>You save {inr(saving)}</strong> by buying from {bestRetailer} instead of {worstRetailer}.
      Verified prices across {priced.length} stores.
    </div>
  );
}

/* ── Main page ───────────────────────────────────────────────────────── */
export default function ProductPage({ id }) {
  const { data, error, loading, reload } = useApi(`/products/${id}`, { pollMs: 15000 });
  const alerts  = useApi("/alerts", { params: { product_id: id } });
  const settings = useApi("/settings");
  const [target, setTarget]   = useState(null);
  const [addUrl, setAddUrl]   = useState("");
  const [addState, setAddState] = useState({ busy: false, err: null, ok: null });
  const [tick, setTick]       = useState(0);
  const [busy, setBusy]       = useState(false);
  const [rediscovering, setRediscovering] = useState(false);
  const [rediscoverMsg, setRediscoverMsg] = useState(null);

  if (loading && !data) return <Spinner label="Loading product…" />;
  if (error && !data) return <ErrorBox error={error} onRetry={reload} />;

  const p    = data.product;
  const best = data.offers.find(o => o.is_best);
  const verifiedOffers = data.offers.filter(o => o.match_status !== "possible");
  const possibleOffers = data.offers.filter(o => o.match_status === "possible");
  const onlyOneStore   = data.offers.length === 1;

  const act = async (fn) => {
    setBusy(true);
    try { await fn(); await reload(); setTick(t => t + 1); }
    finally { setBusy(false); }
  };

  const addOffer = async (e) => {
    e.preventDefault();
    setAddState({ busy: true, err: null, ok: null });
    try {
      await api(`/products/${id}/offers`, { method: "POST", body: { url: addUrl } });
      setAddState({ busy: false, err: null, ok: "Offer added and verified as the same product." });
      setAddUrl(""); reload();
    } catch (er) {
      setAddState({ busy: false, err: er, ok: null });
    }
  };

  const rediscover = async () => {
    setRediscovering(true);
    setRediscoverMsg(null);
    try {
      const r = await api(`/products/${id}/rediscover`, { method: "POST" });
      setRediscoverMsg(r.message || "Discovery started — refresh in ~30 seconds.");
    } catch (e) {
      setRediscoverMsg("Could not start discovery: " + e.message);
    } finally {
      setRediscovering(false);
    }
  };

  const activeTarget = (alerts.data?.alerts || []).find(
    a => a.kind === "target_reached" && a.enabled
  )?.target_price;

  return (
    <>
      <PageHead
        title={p.title}
        sub={[p.brand, ...(p.attributes?.storage || []), ...(p.attributes?.colours || [])]
          .filter(Boolean).join(" · ") || undefined}
        actions={<>
          <button className="btn btn-ghost" disabled={busy}
            onClick={() => act(() => api(`/products/${id}/refresh`, { method: "POST" }))}>
            ⚡ Check now
          </button>
          <button
            className={`btn ${p.paused ? "btn-primary" : "btn-danger"}`}
            style={p.paused ? { background: "linear-gradient(135deg, #10b981, #059669)", borderColor: "transparent", color: "#fff", fontWeight: 700 } : { fontWeight: 700 }}
            disabled={busy}
            onClick={() => act(() => api(`/products/${id}`, { method: "PATCH", body: { paused: !p.paused } }))}>
            {p.paused ? "▶ Start Tracking (15s)" : "⏹ Stop Tracking"}
          </button>
          <button className="btn btn-danger" disabled={busy}
            onClick={async () => {
              if (window.confirm("Stop tracking this product and delete its alerts?")) {
                await api(`/products/${id}`, { method: "DELETE" });
                go("/tracked");
              }
            }}>
            Remove
          </button>
        </>}
      />

      {p.paused ? (
        <div className="notice notice-warn" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span>⏸ <strong>Tracking is stopped</strong> — automatic 15-second price fetching is paused to protect against retailer IP blocks.</span>
          <button className="btn btn-primary" style={{ padding: "4px 12px", fontSize: "0.8rem", background: "#10b981", borderColor: "transparent", color: "#fff" }} onClick={() => act(() => api(`/products/${id}`, { method: "PATCH", body: { paused: false } }))}>
            Resume 15s Tracking
          </button>
        </div>
      ) : (
        <div className="notice notice-good" style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span className="neon-pulse-dot" style={{ display: "inline-block", width: 10, height: 10, borderRadius: "50%", background: "#00ff88", boxShadow: "0 0 10px #00ff88", flexShrink: 0 }} />
          <span>🟢 <strong>Active Live 15s Monitoring</strong> — Refreshing live price every 15 seconds. Click <strong>Stop Tracking</strong> anytime when done.</span>
        </div>
      )}

      {/* ── Hero ── */}
      <div className="hero-grid">
        <Glass className="hero">
          {p.image_url && (
            <img src={p.image_url} alt="" className="hero-img" referrerPolicy="no-referrer" />
          )}
          <div>
            {best ? (<>
              <span className="label">Lowest verified price</span>
              <div className="big-price">{inr(best.price)}</div>
              <div className="muted">
                at <RetailerDot name={best.retailer} />{best.retailer} · <Freshness offer={best} />
              </div>
            </>) : (<>
              <span className="label">No fresh verified price</span>
              <div className="big-price dim">—</div>
              <div className="muted small">
                Every offer is stale, out of stock or unavailable.
              </div>
            </>)}
            <div className="chips">
              <Verdict v={data.verdict.label} />
              {data.verdict.preliminary && <Chip tone="warn">Preliminary</Chip>}
            </div>
          </div>
        </Glass>
        <Glass>
          <h3>Should you buy now?</h3>
          <p>{data.verdict.recommendation || "Waiting for the first verified price."}</p>
          <p className="muted small">{data.verdict.explanation}</p>
          <p className="muted small">{data.verdict.disclaimer}</p>
        </Glass>
      </div>

      {/* ── Price comparison ── */}
      <Glass>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12, marginBottom: 8 }}>
          <div>
            <h2 style={{ margin: 0 }}>Price comparison across stores</h2>
            <p className="muted small" style={{ margin: "4px 0 0" }}>
              Comparing <strong>{data.offers.length}</strong> store{data.offers.length !== 1 ? "s" : ""} ·
              Only verified in-stock offers are ranked lowest.
            </p>
          </div>
          <button
            className="btn btn-primary"
            disabled={rediscovering}
            onClick={rediscover}
            title="Search all supported stores for this product"
          >
            {rediscovering ? "Searching…" : "🔍 Find on other stores"}
          </button>
        </div>

        {rediscoverMsg && (
          <div className={`notice ${rediscoverMsg.startsWith("Could") ? "notice-warn" : "notice-good"}`}>
            {rediscoverMsg}
          </div>
        )}

        <ComparisonSummary offers={data.offers} />

        {onlyOneStore && (
          <div className="notice notice-warn" style={{ marginBottom: 12 }}>
            ⚡ Only one store found so far. Click <strong>"Find on other stores"</strong> to
            automatically search Amazon, Flipkart, Croma, JioMart, Reliance Digital and more.
          </div>
        )}

        {/* Verified + source offers */}
        {verifiedOffers.length > 0 && verifiedOffers.map(o => <OfferCard key={o.id} o={o} />)}

        {/* Possible matches — shown separately with a disclaimer */}
        {possibleOffers.length > 0 && (
          <>
            <div className="muted small" style={{ margin: "16px 0 8px", fontWeight: 600, letterSpacing: ".04em", textTransform: "uppercase", fontSize: ".72rem" }}>
              Possible matches — verify before purchase
            </div>
            {possibleOffers.map(o => <OfferCard key={o.id} o={o} />)}
          </>
        )}

        {/* Manual add */}
        <div style={{ marginTop: 20, paddingTop: 16, borderTop: "1px solid var(--line)" }}>
          <p className="muted small" style={{ marginBottom: 10 }}>
            Know the exact listing on another store? Paste its URL below to verify and add it.
          </p>
          <form onSubmit={addOffer} className="inline-form">
            <input
              className="input"
              value={addUrl}
              onChange={e => setAddUrl(e.target.value)}
              placeholder="https://www.flipkart.com/product/… or Amazon, Croma, JioMart…"
              aria-label="Add offer URL"
              required
            />
            <button className="btn btn-primary" disabled={addState.busy}>
              {addState.busy ? "Verifying…" : "Add & verify"}
            </button>
          </form>
          {addState.ok && <div className="notice notice-good" style={{ marginTop: 10 }}>{addState.ok}</div>}
          <ErrorBox error={addState.err} />
        </div>

        {/* Rejected candidates */}
        {data.rejected_candidates.length > 0 && (
          <details style={{ marginTop: 16 }}>
            <summary>
              {data.rejected_candidates.length} listing(s) found but rejected as a different product
            </summary>
            <div style={{ marginTop: 10, display: "grid", gap: 6 }}>
              {data.rejected_candidates.map((c, i) => (
                <div key={i} className="small muted" style={{ padding: "8px 12px", background: "var(--glass-2)", borderRadius: "var(--r-xs)", border: "1px solid var(--line)" }}>
                  <RetailerDot name={c.retailer} /><strong>{c.retailer}</strong>: {c.title}
                  <div style={{ marginTop: 4, color: "var(--bad)", fontSize: ".78rem" }}>
                    {c.reasons.join(" · ")}
                  </div>
                  {c.url && <a href={c.url} target="_blank" rel="noopener noreferrer" className="muted small"> View listing ↗</a>}
                </div>
              ))}
            </div>
          </details>
        )}
      </Glass>

      {/* ── Price history chart ── */}
      <Glass>
        <h2>Price radar analytics & history</h2>
        <PriceChart productId={id} target={activeTarget || target} refreshKey={tick} paused={p.paused} />
      </Glass>

      {/* ── 3-Column Aligned Controls: Insights | Tracking Up to Time | Set Target Price ── */}
      <div className="three-col">
        {/* Card 1: Compact Insights */}
        <Glass style={{ padding: "18px 20px" }}>
          <h2 style={{ fontSize: "1.05rem", marginBottom: 10 }}>💡 Price Insights</h2>
          {data.insights.length === 0 ? (
            <p className="muted small">Insights appear once PriceRadar has recorded observations.</p>
          ) : (
            <ul className="insights" style={{ margin: "0 0 10px", gap: 6 }}>
              {data.insights.slice(0, 2).map((i, k) => (
                <li key={k} className={`tone-${i.tone}`} style={{ padding: "8px 12px", fontSize: "0.82rem" }}>
                  {i.text}
                </li>
              ))}
            </ul>
          )}
          <div className="stats-row small-stats" style={{ margin: "10px 0 0", gap: 8 }}>
            <div style={{ padding: "6px 8px" }}>
              <span className="label" style={{ fontSize: "0.62rem" }}>7d change</span>
              <strong style={{ fontSize: "0.95rem" }}>
                {data.stats.change_7d == null ? "—" : `${inr(data.stats.change_7d)} (${pct(data.stats.change_7d_pct)})`}
              </strong>
            </div>
            <div style={{ padding: "6px 8px" }}>
              <span className="label" style={{ fontSize: "0.62rem" }}>30d change</span>
              <strong style={{ fontSize: "0.95rem" }}>
                {data.stats.change_30d == null ? "—" : `${inr(data.stats.change_30d)} (${pct(data.stats.change_30d_pct)})`}
              </strong>
            </div>
          </div>
        </Glass>

        {/* Card 2: Tracking Window / Quiet Window (15m, 30m, 1h, 2h, 1d, 2d) */}
        <Glass style={{ padding: "18px 20px" }}>
          <TrackingDurationCard
            productId={id}
            isPaused={p.paused}
            trackUntil={p.track_until}
            onUpdated={() => reload()}
          />
        </Glass>

        {/* Card 3: Set Target Price */}
        <Glass style={{ padding: "18px 20px" }}>
          <h2 style={{ fontSize: "1.05rem", marginBottom: 10 }}>🎯 Target Price</h2>
          <TargetPicker productId={id} value={target} onChange={setTarget} />
        </Glass>
      </div>

      {/* ── Alerts ── */}
      <div className="two-col">
        <Glass>
          <h2>Create an alert</h2>
          {settings.data && !settings.data.smtp.configured && (
            <div className="notice notice-warn">
              Email isn't configured on the server yet — alerts will be recorded but can't be delivered. See Settings.
            </div>
          )}
          <AlertForm
            productId={Number(id)}
            target={target}
            defaultEmail={settings.data?.email}
            onCreated={() => alerts.reload()}
          />
        </Glass>
        <Glass>
          <h2>Alerts on this product</h2>
          {(alerts.data?.alerts || []).length === 0
            ? <p className="muted">No alerts yet.</p>
            : alerts.data.alerts.map(a => (
                <div key={a.id} className="row-line">
                  <div>
                    <strong>{ALERT_KINDS[a.kind]}</strong>
                    <div className="muted small">
                      {a.target_price ? `Target ${inr(a.target_price)}`
                        : a.pct_threshold ? `${a.pct_threshold}%`
                        : a.amount_threshold ? inr(a.amount_threshold) : ""}
                      {" "}· cooldown {Math.round(a.cooldown_s / 60)} min
                      {a.last_fired_at ? ` · last sent ${ago(a.last_fired_at)}` : ""}
                    </div>
                  </div>
                  <button
                    className="btn btn-ghost"
                    onClick={async () => { await api(`/alerts/${a.id}`, { method: "DELETE" }); alerts.reload(); }}
                  >
                    Delete
                  </button>
                </div>
              ))}
        </Glass>
      </div>

      <p className="muted small">
        Monitoring target: every {data.monitoring.target_interval_s}s. {data.monitoring.note}
      </p>
    </>
  );
}
