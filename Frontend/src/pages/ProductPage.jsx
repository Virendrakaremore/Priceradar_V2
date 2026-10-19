import { useState } from "react";
import { api } from "../api";
import { AlertForm, TargetPicker } from "../components/TargetAlert";
import PriceChart from "../components/PriceChart";
import { Chip, ErrorBox, Freshness, Glass, PageHead, Spinner, Verdict } from "../components/ui";
import { ALERT_KINDS, ago, inr, pct } from "../format";
import { go, useApi } from "../hooks";

function OfferCard({ o }) {
  return (
    <div className={`offer ${o.is_best ? "best" : ""} ${!o.verified ? "alt" : ""}`}>
      <div className="offer-main">
        <div><strong className="retailer">{o.retailer}</strong>
          {o.is_best && <Chip tone="good">Lowest verified</Chip>}
          {o.match_status === "source" && <Chip>Your link</Chip>}
          {o.match_status === "verified" && <Chip>Verified match</Chip>}
          {o.match_status === "possible" && <Chip tone="warn">Possible alternative — not verified</Chip>}
          {!o.retailer_live_verified && <Chip tone="neutral">Parser not yet live-verified</Chip>}
        </div>
        <div className="muted small">{o.title}</div>
        {o.variant && <div className="muted small">Variant: {o.variant}</div>}
        {o.match_reasons?.length > 0 && <div className="muted small">{o.match_reasons.join(" · ")}</div>}
      </div>
      <div className="offer-price">
        <div className="price">{o.price ? inr(o.price) : "Unavailable"}</div>
        {o.mrp && <div className="muted small"><s>{inr(o.mrp)}</s>{o.discount && ` · ${o.discount.pct}% off`}</div>}
        <div className="muted small">{o.total_payable ? `Total ${inr(o.total_payable)}` : "Delivery cost unknown"}</div>
        <div className="small">{o.availability === "in_stock" ? "In stock" : o.availability === "out_of_stock" ? "Out of stock" : "Stock unknown"}</div>
      </div>
      <div className="offer-meta">
        {o.verified ? <Freshness offer={o} /> : <span className="muted small">Price seen at discovery {ago(o.last_success_at)} — not monitored</span>}
        {o.last_error && o.status !== "ok" && <div className="small warn-text">{o.last_error}</div>}
        <a className="btn btn-ghost" href={o.url} target="_blank" rel="noopener noreferrer">View on {o.retailer} ↗</a>
      </div>
    </div>
  );
}

export default function ProductPage({ id }) {
  const { data, error, loading, reload } = useApi(`/products/${id}`, { pollMs: 15000 });
  const alerts = useApi("/alerts", { params: { product_id: id } });
  const settings = useApi("/settings");
  const [target, setTarget] = useState(null);
  const [addUrl, setAddUrl] = useState("");
  const [addState, setAddState] = useState({ busy: false, err: null, ok: null });
  const [tick, setTick] = useState(0);
  const [busy, setBusy] = useState(false);

  if (loading && !data) return <Spinner label="Loading product…" />;
  if (error && !data) return <ErrorBox error={error} onRetry={reload} />;
  const p = data.product, best = data.offers.find((o) => o.is_best);

  const act = async (fn) => { setBusy(true); try { await fn(); await reload(); setTick((t) => t + 1); } finally { setBusy(false); } };
  const addOffer = async (e) => {
    e.preventDefault(); setAddState({ busy: true, err: null, ok: null });
    try { await api(`/products/${id}/offers`, { method: "POST", body: { url: addUrl } }); setAddState({ busy: false, err: null, ok: "Offer added and verified as the same product." }); setAddUrl(""); reload(); }
    catch (er) { setAddState({ busy: false, err: er, ok: null }); }
  };
  const activeTarget = (alerts.data?.alerts || []).find((a) => a.kind === "target_reached" && a.enabled)?.target_price;

  return (
    <>
      <PageHead title={p.title} sub={[p.brand, ...(p.attributes?.storage || []), ...(p.attributes?.colours || [])].filter(Boolean).join(" · ") || undefined}
        actions={<>
          <button className="btn btn-ghost" disabled={busy} onClick={() => act(() => api(`/products/${id}/refresh`, { method: "POST" }))}>Check now</button>
          <button className="btn btn-ghost" disabled={busy} onClick={() => act(() => api(`/products/${id}`, { method: "PATCH", body: { paused: !p.paused } }))}>{p.paused ? "Resume monitoring" : "Pause monitoring"}</button>
          <button className="btn btn-danger" disabled={busy} onClick={async () => { if (window.confirm("Stop tracking this product and delete its alerts?")) { await api(`/products/${id}`, { method: "DELETE" }); go("/tracked"); } }}>Remove</button>
        </>} />
      {p.paused && <div className="notice notice-warn">Monitoring is paused — prices are not being refreshed and no alerts will fire.</div>}
      <div className="hero-grid">
        <Glass className="hero">
          {p.image_url && <img src={p.image_url} alt="" className="hero-img" referrerPolicy="no-referrer" />}
          <div>
            {best ? (<>
              <span className="label">Lowest verified price</span>
              <div className="big-price">{inr(best.price)}</div>
              <div className="muted">at {best.retailer} · <Freshness offer={best} /></div>
            </>) : (<>
              <span className="label">No fresh verified price</span>
              <div className="big-price dim">—</div>
              <div className="muted small">Every offer is stale, out of stock or unavailable. Last known values are shown below, labelled as such.</div>
            </>)}
            <div className="chips"><Verdict v={data.verdict.label} />{data.verdict.preliminary && <Chip tone="warn">Preliminary</Chip>}</div>
          </div>
        </Glass>
        <Glass>
          <h3>Should you buy now?</h3>
          <p>{data.verdict.recommendation || "Waiting for the first verified price."}</p>
          <p className="muted small">{data.verdict.explanation}</p>
          <p className="muted small">{data.verdict.disclaimer}</p>
        </Glass>
      </div>

      <Glass>
        <h2>Price comparison</h2>
        <p className="muted small">Only verified, in-stock, recently-checked offers can be ranked lowest. Different variants and unverified matches are never mixed in.</p>
        {data.offers.map((o) => <OfferCard key={o.id} o={o} />)}
        {data.offers.length === 1 && <div className="notice notice-info">No other retailer has been matched yet. Automatic discovery depends on retailers allowing automated search; paste the same product's link from another store below.</div>}
        <form onSubmit={addOffer} className="inline-form">
          <input className="input" value={addUrl} onChange={(e) => setAddUrl(e.target.value)} placeholder="Paste the same product's link from another retailer" aria-label="Add offer URL" required />
          <button className="btn btn-primary" disabled={addState.busy}>{addState.busy ? "Verifying…" : "Add & verify"}</button>
        </form>
        {addState.ok && <div className="notice notice-good">{addState.ok}</div>}
        <ErrorBox error={addState.err} />
        {data.rejected_candidates.length > 0 && <details><summary>{data.rejected_candidates.length} listing(s) rejected as different products</summary>
          {data.rejected_candidates.map((c, i) => <div key={i} className="small muted">{c.retailer}: {c.title} — {c.reasons.join("; ")}</div>)}</details>}
      </Glass>

      <Glass>
        <h2>Price history</h2>
        <PriceChart productId={id} target={activeTarget || target} refreshKey={tick} />
      </Glass>

      <div className="two-col">
        <Glass>
          <h2>Insights</h2>
          {data.insights.length === 0 ? <p className="muted">Insights appear once PriceRadar has recorded more than one price.</p> :
            <ul className="insights">{data.insights.map((i, k) => <li key={k} className={`tone-${i.tone}`}>{i.text}</li>)}</ul>}
          <div className="stats-row small-stats">
            <div><span className="label">7-day change</span><strong>{data.stats.change_7d == null ? "—" : `${inr(data.stats.change_7d)} (${pct(data.stats.change_7d_pct)})`}</strong></div>
            <div><span className="label">30-day change</span><strong>{data.stats.change_30d == null ? "—" : `${inr(data.stats.change_30d)} (${pct(data.stats.change_30d_pct)})`}</strong></div>
          </div>
        </Glass>
        <Glass>
          <h2>Set a target price</h2>
          <TargetPicker productId={id} value={target} onChange={setTarget} />
        </Glass>
      </div>

      <div className="two-col">
        <Glass>
          <h2>Create an alert</h2>
          {settings.data && !settings.data.smtp.configured && <div className="notice notice-warn">Email isn't configured on the server yet, so alerts will be recorded but can't be delivered. See Settings.</div>}
          <AlertForm productId={Number(id)} target={target} defaultEmail={settings.data?.email} onCreated={() => { alerts.reload(); }} />
        </Glass>
        <Glass>
          <h2>Alerts on this product</h2>
          {(alerts.data?.alerts || []).length === 0 ? <p className="muted">No alerts yet.</p> :
            alerts.data.alerts.map((a) => (
              <div key={a.id} className="row-line"><div><strong>{ALERT_KINDS[a.kind]}</strong>
                <div className="muted small">{a.target_price ? `Target ${inr(a.target_price)}` : a.pct_threshold ? `${a.pct_threshold}%` : a.amount_threshold ? inr(a.amount_threshold) : ""} · cooldown {Math.round(a.cooldown_s / 60)} min{a.last_fired_at ? ` · last sent ${ago(a.last_fired_at)}` : ""}</div></div>
                <button className="btn btn-ghost" onClick={async () => { await api(`/alerts/${a.id}`, { method: "DELETE" }); alerts.reload(); }}>Delete</button></div>))}
        </Glass>
      </div>
      <p className="muted small">Monitoring target: every {data.monitoring.target_interval_s}s. {data.monitoring.note}</p>
    </>
  );
}
