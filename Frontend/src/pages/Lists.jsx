import { useState } from "react";
import { api } from "../api";
import { Chip, Empty, ErrorBox, Glass, PageHead, Spinner, Verdict } from "../components/ui";
import { ALERT_KINDS, ago, inr, pct, when } from "../format";
import { go, useApi } from "../hooks";

export function Overview() {
  const { data, error, loading, reload } = useApi("/dashboard", { pollMs: 15000 });
  if (loading && !data) return <Spinner />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const d = data;

  return (
    <div className="advanced-overview-dashboard">
      <PageHead
        title="Price Radar Command Center"
        sub="Comprehensive multi-platform intelligence, real-time drop alerts, and dynamic price trajectories."
        actions={
          <div style={{ display: "flex", gap: 10 }}>
            <button className="btn btn-ghost" onClick={() => reload()}>
              ↻ Sync Radar
            </button>
            <button className="btn btn-primary" onClick={() => go("/track")}>
              + Track New Product
            </button>
          </div>
        }
      />

      {!d.smtp_configured && (
        <div className="notice notice-warn" style={{ marginBottom: 18 }}>
          Email delivery isn't configured on the server — alerts will trigger internally but cannot send emails. Visit Settings to set SMTP.
        </div>
      )}

      {/* Hero Projection Card */}
      <div className="dashboard-hero-banner">
        <div className="dashboard-hero-content">
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
            <span className="neon-pulse-dot" style={{ width: 9, height: 9, borderRadius: "50%", background: "#00ff88", boxShadow: "0 0 10px #00ff88" }} />
            <span style={{ fontSize: "0.75rem", fontWeight: 700, letterSpacing: "0.08em", color: "#10b981", textTransform: "uppercase" }}>
              Active Radar Stream
            </span>
          </div>
          <h2>Live Store Surveillance & Price Matrix</h2>
          <p className="muted" style={{ margin: 0, maxWidth: "62ch", fontSize: "0.88rem" }}>
            Continuously scanning Amazon, Flipkart, Croma, JioMart, and Reliance Digital. Historical baselines and 15s instant checks protect against dynamic pricing.
          </p>
        </div>

        <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
          <div style={{ background: "var(--glass-2)", padding: "12px 18px", borderRadius: "var(--r-sm)", border: "1px solid var(--line-2)", textAlign: "center" }}>
            <span className="label" style={{ fontSize: "0.65rem", margin: 0 }}>Active Radar</span>
            <strong style={{ fontSize: "1.45rem", color: "#10b981" }}>{d.active_monitors ?? d.tracked_products}</strong>
          </div>
          <div style={{ background: "var(--glass-2)", padding: "12px 18px", borderRadius: "var(--r-sm)", border: "1px solid var(--line-2)", textAlign: "center" }}>
            <span className="label" style={{ fontSize: "0.65rem", margin: 0 }}>Paused Radar</span>
            <strong style={{ fontSize: "1.45rem", color: "var(--warn)" }}>{d.paused_monitors ?? 0}</strong>
          </div>
        </div>
      </div>

      {/* KPI Cards Strip */}
      <div className="dashboard-kpi-grid">
        {/* Card 1 */}
        <div className="dashboard-kpi-card">
          <div className="dashboard-kpi-top">
            <span className="label" style={{ margin: 0 }}>Tracked Items</span>
            <div className="dashboard-kpi-icon" style={{ color: "var(--accent)" }}>📦</div>
          </div>
          <div className="dashboard-kpi-val">{d.tracked_products}</div>
          <div className="dashboard-kpi-sub">
            <span>Across 7 Indian retailers</span>
          </div>
        </div>

        {/* Card 2 */}
        <div className="dashboard-kpi-card">
          <div className="dashboard-kpi-top">
            <span className="label" style={{ margin: 0 }}>Potential Savings</span>
            <div className="dashboard-kpi-icon" style={{ color: "#10b981" }}>💰</div>
          </div>
          <div className="dashboard-kpi-val" style={{ color: "#10b981" }}>
            {inr(d.potential_savings_to_targets)}
          </div>
          <div className="dashboard-kpi-sub">
            <span>To your configured target prices</span>
          </div>
        </div>

        {/* Card 3 */}
        <div className="dashboard-kpi-card">
          <div className="dashboard-kpi-top">
            <span className="label" style={{ margin: 0 }}>Confirmed Drops (7d)</span>
            <div className="dashboard-kpi-icon" style={{ color: "#38bdf8" }}>📉</div>
          </div>
          <div className="dashboard-kpi-val">{d.confirmed_price_drops_7d}</div>
          <div className="dashboard-kpi-sub">
            <span>{d.price_drops?.length > 0 ? `Max saving: ${d.max_savings_pct || 0}%` : "No drops this week"}</span>
          </div>
        </div>

        {/* Card 4 */}
        <div className="dashboard-kpi-card">
          <div className="dashboard-kpi-top">
            <span className="label" style={{ margin: 0 }}>Active Price Alerts</span>
            <div className="dashboard-kpi-icon" style={{ color: "#f59e0b" }}>🔔</div>
          </div>
          <div className="dashboard-kpi-val">{d.active_alerts}</div>
          <div className="dashboard-kpi-sub">
            <span>Instant drop triggers armed</span>
          </div>
        </div>
      </div>

      {/* Empty State */}
      {d.tracked_products === 0 ? (
        <Glass>
          <Empty
            title="Start your Price Radar"
            action={
              <button className="btn btn-primary" onClick={() => go("/track")}>
                Paste an Amazon or Flipkart link
              </button>
            }
          >
            Track prices from Amazon, Flipkart, Croma, JioMart and more. Real-time monitoring and historical graphs activate immediately.
          </Empty>
        </Glass>
      ) : (
        <>
          {/* Main 2-Column Dashboard Projection */}
          <div className="dashboard-advanced-grid">
            {/* Left Big Card: Monitored Products Matrix */}
            <Glass style={{ margin: 0 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
                <h2 style={{ fontSize: "1.1rem", margin: 0 }}>📦 Tracked Products Portfolio</h2>
                <a href="#/tracked" className="title-link small" style={{ color: "var(--accent)", fontWeight: 600 }}>
                  View All ({d.tracked_products}) →
                </a>
              </div>
              <div style={{ display: "grid", gap: 10 }}>
                {d.recent_products.map((p) => (
                  <a
                    key={p.id}
                    className="row-line link"
                    href={`#/products/${p.id}`}
                    style={{ padding: "10px 14px", borderRadius: "var(--r-sm)", border: "1px solid var(--line-2)", background: "var(--glass-2)" }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                      {p.image_url ? (
                        <img src={p.image_url} alt="" style={{ width: 42, height: 42, objectFit: "contain", borderRadius: 6, background: "#fff", padding: 3, border: "1px solid var(--line)" }} />
                      ) : (
                        <div style={{ width: 42, height: 42, borderRadius: 6, background: "var(--glass-3)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "1.2rem" }}>
                          🏷️
                        </div>
                      )}
                      <div>
                        <div style={{ fontWeight: 600, color: "var(--text)", fontSize: "0.92rem", maxWidth: "340px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {p.title}
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 3 }}>
                          <Verdict v={p.verdict} />
                          {p.paused ? (
                            <span className="chip" style={{ fontSize: "0.68rem", background: "rgba(196,119,0,0.1)", color: "var(--warn)" }}>Paused</span>
                          ) : (
                            <span className="chip" style={{ fontSize: "0.68rem", background: "rgba(16,185,129,0.1)", color: "var(--good)" }}>15s Active</span>
                          )}
                        </div>
                      </div>
                    </div>
                    <div style={{ textAlign: "right" }}>
                      <strong style={{ fontSize: "1.1rem", color: "var(--text)" }}>{inr(p.current_price)}</strong>
                      {p.best_offer && <div className="muted small">{p.best_offer.retailer}</div>}
                    </div>
                  </a>
                ))}
              </div>
            </Glass>

            {/* Right Card: Verified Price Drops & Movements */}
            <Glass style={{ margin: 0 }}>
              <h2 style={{ fontSize: "1.1rem", marginBottom: 14 }}>📉 Recent Price Drops (7 Days)</h2>
              {d.price_drops.length === 0 ? (
                <div style={{ padding: "28px 16px", textAlign: "center", color: "var(--muted)" }}>
                  <div style={{ fontSize: "2rem", marginBottom: 6 }}>📊</div>
                  <p className="small" style={{ margin: 0 }}>No price reductions recorded in the last 7 days. Once stores drop prices, they appear here.</p>
                </div>
              ) : (
                <div style={{ display: "grid", gap: 10 }}>
                  {d.price_drops.map((x) => (
                    <a
                      key={x.product_id}
                      className="row-line link"
                      href={`#/products/${x.product_id}`}
                      style={{ padding: "10px 14px", borderRadius: "var(--r-sm)", border: "1px solid var(--line-2)", background: "var(--glass-2)" }}
                    >
                      <div style={{ maxWidth: "230px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        <span style={{ fontWeight: 600, color: "var(--text)", fontSize: "0.88rem" }}>{x.title}</span>
                      </div>
                      <div style={{ textAlign: "right" }}>
                        <strong className="down" style={{ display: "block" }}>{inr(x.change)}</strong>
                        <span className="small down" style={{ fontWeight: 600 }}>({pct(x.pct)})</span>
                      </div>
                    </a>
                  ))}
                </div>
              )}
            </Glass>
          </div>

          {/* Bottom Secondary Intelligence Row */}
          <div className="two-col" style={{ marginTop: 18 }}>
            <Glass style={{ margin: 0 }}>
              <h2 style={{ fontSize: "1.05rem", marginBottom: 12 }}>⚡ Recently Observed Store Offers</h2>
              {d.recent_offers.length === 0 ? (
                <p className="muted small">No verified multi-store offers recorded yet.</p>
              ) : (
                d.recent_offers.map((o) => (
                  <a key={o.id} className="row-line link" href={`#/products/${o.product_id}`} style={{ padding: "8px 0" }}>
                    <span>
                      <strong style={{ color: "var(--accent)" }}>{o.retailer}</strong> · {inr(o.price)}
                    </span>
                    <span className="muted small">{ago(o.last_success_at)}</span>
                  </a>
                ))
              )}
            </Glass>

            <Glass style={{ margin: 0 }}>
              <h2 style={{ fontSize: "1.05rem", marginBottom: 12 }}>🔔 Real-Time Alert Activity</h2>
              {d.recent_notifications.length === 0 ? (
                <p className="muted small">No notifications triggered yet. When targets are hit, attempts are logged here.</p>
              ) : (
                d.recent_notifications.map((n) => (
                  <div key={n.id} className="row-line" style={{ padding: "8px 0" }}>
                    <span style={{ maxWidth: "240px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: "0.88rem" }}>
                      {n.product_name}
                    </span>
                    <Chip tone={n.status === "sent" ? "good" : n.status === "dead" ? "bad" : "warn"}>
                      {n.status}
                    </Chip>
                  </div>
                ))
              )}
            </Glass>
          </div>
        </>
      )}
    </div>
  );
}

export function Tracked() {
  const [f, setF] = useState({ sort: "added", order: "desc", state: "", retailer: "", min_price: "", max_price: "" });
  const { data, error, loading, reload } = useApi("/products", { params: f, pollMs: 20000 });
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <>
      <PageHead title="My tracked products" actions={<button className="btn btn-primary" onClick={() => go("/track")}>Track a product</button>} />
      <Glass className="filters">
        <label>Sort<select className="input" value={f.sort} onChange={set("sort")}><option value="added">Date added</option><option value="price">Price</option><option value="recent">Recent movement</option><option value="alerts">Alert count</option></select></label>
        <label>Order<select className="input" value={f.order} onChange={set("order")}><option value="desc">High → low</option><option value="asc">Low → high</option></select></label>
        <label>Monitoring<select className="input" value={f.state} onChange={set("state")}><option value="">All</option><option value="active">Active</option><option value="paused">Paused</option></select></label>
        <label>Retailer<select className="input" value={f.retailer} onChange={set("retailer")}><option value="">All</option>{["Amazon India", "Flipkart", "Croma", "Reliance Digital", "Vijay Sales", "Tata CLiQ", "JioMart"].map((r) => <option key={r}>{r}</option>)}</select></label>
        <label>Min ₹<input className="input" type="number" value={f.min_price} onChange={set("min_price")} /></label>
        <label>Max ₹<input className="input" type="number" value={f.max_price} onChange={set("max_price")} /></label>
      </Glass>
      <ErrorBox error={error} onRetry={reload} />
      {loading && !data && <Spinner />}
      {data && data.items.length === 0 && <Glass><Empty title="No products match">Track a product or loosen the filters.</Empty></Glass>}
      {data?.items.map((p) => (
        <Glass key={p.id} className="tracked">
          {p.image_url && <img src={p.image_url} alt="" referrerPolicy="no-referrer" />}
          <div className="grow"><a href={`#/products/${p.id}`} className="title-link">{p.title}</a>
            <div className="chips"><Verdict v={p.verdict} />{p.paused && <Chip tone="warn">Paused</Chip>}<Chip>{p.alert_count} alert{p.alert_count === 1 ? "" : "s"}</Chip>
              {p.retailers.map((r) => <Chip key={r}>{r}</Chip>)}</div>
            <div className="muted small">7-day change: {p.stats.change_7d == null ? "not enough history" : `${inr(p.stats.change_7d)} (${pct(p.stats.change_7d_pct)})`}</div></div>
          <div className="right"><div className="price">{inr(p.current_price)}</div>{p.best_offer && <div className="muted small">{p.best_offer.retailer}</div>}
            <button className="btn btn-ghost" onClick={async () => { await api(`/products/${p.id}`, { method: "PATCH", body: { paused: !p.paused } }); reload(); }}>{p.paused ? "Resume" : "Pause"}</button>
            <button className="btn btn-danger" onClick={async () => { if (window.confirm("Remove this product and its alerts?")) { await api(`/products/${p.id}`, { method: "DELETE" }); reload(); } }}>Remove</button></div>
        </Glass>))}
    </>
  );
}

export function Alerts() {
  const { data, error, loading, reload } = useApi("/alerts");
  const [edit, setEdit] = useState({});
  const [err, setErr] = useState(null);
  const patch = async (id, body) => { setErr(null); try { await api(`/alerts/${id}`, { method: "PATCH", body }); reload(); } catch (e) { setErr(e); } };
  return (
    <>
      <PageHead title="Price alerts" sub="Alerts are evaluated by the backend monitor — no browser tab needs to stay open." />
      <ErrorBox error={error} onRetry={reload} /><ErrorBox error={err} />
      {loading && !data && <Spinner />}
      {data && data.alerts.length === 0 && <Glass><Empty title="No alerts yet">Open a tracked product to set a target price or fluctuation alert.</Empty></Glass>}
      {data?.alerts.map((a) => (
        <Glass key={a.id} className="row-line">
          <div className="grow"><a className="title-link" href={`#/products/${a.product_id}`}>{a.product_title}</a>
            <div className="muted small">{ALERT_KINDS[a.kind]} · cooldown {Math.round(a.cooldown_s / 60)} min · {a.last_fired_at ? `last sent ${ago(a.last_fired_at)}` : "never sent"}</div></div>
          {a.kind === "target_reached" && (
            <span className="inline-form"><input className="input price-input" type="number" aria-label="Target price" value={edit[a.id] ?? Math.round(a.target_price)} onChange={(e) => setEdit({ ...edit, [a.id]: e.target.value })} />
              <button className="btn btn-ghost" onClick={() => patch(a.id, { target_price: edit[a.id] })} disabled={edit[a.id] === undefined}>Save</button></span>)}
          {a.kind === "pct_drop" && <Chip>{a.pct_threshold}%</Chip>}
          <button className="btn btn-ghost" onClick={() => patch(a.id, { enabled: !a.enabled })}>{a.enabled ? "Pause" : "Resume"}</button>
          <button className="btn btn-danger" onClick={async () => { await api(`/alerts/${a.id}`, { method: "DELETE" }); reload(); }}>Delete</button>
        </Glass>))}
    </>
  );
}

export function Deals() {
  const { data, error, loading, reload } = useApi("/deals", { pollMs: 30000 });
  return (
    <>
      <PageHead title="Price drops & deals" sub={data?.scope || "Products you track that are currently below their recorded average."} />
      <ErrorBox error={error} onRetry={reload} />{loading && !data && <Spinner />}
      {data && data.deals.length === 0 && <Glass><Empty title="No deals right now">A product appears here when its price sits at least 3% below its recorded average. This needs a few days of history.</Empty></Glass>}
      {data?.deals.map((d) => (
        <a key={d.product_id} href={`#/products/${d.product_id}`} className="glass tracked link">
          {d.image_url && <img src={d.image_url} alt="" referrerPolicy="no-referrer" />}
          <div className="grow"><div className="title-link">{d.title}</div><div className="muted small">{d.retailer} · average {inr(d.average)}</div></div>
          <div className="right"><div className="price">{inr(d.current)}</div><strong className="down">{pct(d.pct_below_average)} below average</strong></div></a>))}
    </>
  );
}

export function Insights() {
  const { data, error, loading, reload } = useApi("/products");
  return (
    <>
      <PageHead title="Price insights" sub="Transparent classifications from recorded history. Not price predictions." />
      <ErrorBox error={error} onRetry={reload} />{loading && !data && <Spinner />}
      {data && data.items.length === 0 && <Glass><Empty title="Nothing to analyse yet">Insights need at least one tracked product.</Empty></Glass>}
      {data?.items.map((p) => <ProductInsight key={p.id} p={p} />)}
    </>
  );
}

function ProductInsight({ p }) {
  const { data } = useApi(`/products/${p.id}/insights`);
  return (
    <Glass>
      <div className="row-line"><a className="title-link" href={`#/products/${p.id}`}>{p.title}</a><div className="chips"><Verdict v={p.verdict} />{data?.verdict.preliminary && <Chip tone="warn">Preliminary</Chip>}</div></div>
      {data && <><p>{data.verdict.recommendation}</p><p className="muted small">{data.verdict.explanation}</p>
        <ul className="insights">{data.insights.map((i, k) => <li key={k} className={`tone-${i.tone}`}>{i.text}</li>)}</ul></>}
    </Glass>
  );
}

export function Notifications() {
  const [page, setPage] = useState(1);
  const { data, error, loading, reload } = useApi("/notifications", { params: { page } });
  return (
    <>
      <PageHead title="Notification history" sub="Every delivery attempt is recorded." />
      <ErrorBox error={error} onRetry={reload} />{loading && !data && <Spinner />}
      {data && data.items.length === 0 && <Glass><Empty title="No notifications yet">They appear here when an alert fires.</Empty></Glass>}
      {data?.items.map((n) => (
        <Glass key={n.id} className="row-line">
          <div className="grow"><strong>{n.product_name}</strong><div className="muted small">{n.reason}</div>
            <div className="muted small">{n.retailer} · {inr(n.previous_price)} → {inr(n.current_price)} · {when(n.created_at)}</div>
            {n.error && <div className="small warn-text">{n.error}</div>}</div>
          <Chip tone={n.status === "sent" ? "good" : n.status === "dead" ? "bad" : "warn"}>{n.status}{n.attempts > 1 ? ` · ${n.attempts} attempts` : ""}</Chip>
        </Glass>))}
      {data && data.total > 25 && <div className="actions"><button className="btn btn-ghost" disabled={page === 1} onClick={() => setPage(page - 1)}>Previous</button><button className="btn btn-ghost" disabled={page * 25 >= data.total} onClick={() => setPage(page + 1)}>Next</button></div>}
    </>
  );
}

export function Settings() {
  const { data, error, reload } = useApi("/settings");
  const [email, setEmail] = useState(null);
  const [cool, setCool] = useState(null);
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);
  const save = async () => {
    setErr(null); setMsg(null);
    try { await api("/settings", { method: "PUT", body: { email: email ?? data.email, default_cooldown_s: Number(cool ?? data.default_cooldown_s) } }); setMsg("Saved."); reload(); } catch (e) { setErr(e); }
  };
  const test = async () => {
    setErr(null); setMsg(null);
    try { setMsg((await api("/settings/test-email", { method: "POST", body: { email: email ?? data.email } })).message); } catch (e) { setErr(e); }
  };
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data) return <Spinner />;
  return (
    <>
      <PageHead title="Settings" />
      <div className="two-col">
        <Glass><h2>Notifications</h2>
          <div className="form-grid">
            <label>Email address<input className="input" type="email" value={email ?? data.email} onChange={(e) => setEmail(e.target.value)} /></label>
            <label>Default cooldown (seconds)<input className="input" type="number" min="0" value={cool ?? data.default_cooldown_s} onChange={(e) => setCool(e.target.value)} /></label>
            <div className="inline-form"><button className="btn btn-primary" onClick={save}>Save</button><button className="btn btn-ghost" onClick={test}>Send test email</button></div>
          </div>
          {msg && <div className="notice notice-good" role="status">{msg}</div>}<ErrorBox error={err} />
        </Glass>
        <Glass><h2>Email server</h2>
          <p>{data.smtp.configured ? <Chip tone="good">Configured</Chip> : <Chip tone="bad">Not configured</Chip>}</p>
          <p className="muted small">{data.smtp.host}:{data.smtp.port} · {data.smtp.ssl ? "SSL" : data.smtp.tls ? "STARTTLS" : "no encryption"}</p>
          {!data.smtp.configured && <p className="muted small">Set SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD and EMAIL_FROM in the backend environment, then restart. Credentials are never shown or stored in the browser.</p>}
          <h3>Monitoring</h3><p className="muted small">Target interval {data.monitoring.target_interval_s}s; per-retailer floor {data.monitoring.retailer_floor_s}s. These are server settings.</p>
        </Glass>
      </div>
    </>
  );
}

export function Monitoring() {
  const { data, error, loading, reload } = useApi("/monitoring/status", { pollMs: 5000 });
  if (loading && !data) return <Spinner />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <>
      <PageHead title="Monitoring health" sub={data.config.note} />
      {!data.worker_running && <div className="notice notice-bad">No monitoring worker heartbeat. Prices are NOT being refreshed. Start it with <code>python -m priceradar.worker</code>.</div>}
      <div className="metrics">
        <Glass className="metric"><span className="label">Worker</span><strong>{data.worker_running ? "Running" : "Down"}</strong></Glass>
        <Glass className="metric"><span className="label">Target interval</span><strong>{data.config.target_interval_s}s</strong></Glass>
        <Glass className="metric"><span className="label">Retailer floor</span><strong>{data.config.retailer_floor_s}s</strong></Glass>
        <Glass className="metric"><span className="label">Pending emails</span><strong>{data.pending_notifications}</strong></Glass>
      </div>
      <Glass><h2>Monitored offers</h2>
        {data.offers.length === 0 ? <p className="muted">No offers yet.</p> : <div className="table-wrap"><table><thead><tr><th>Retailer</th><th>Product</th><th>Status</th><th>Last success</th><th>Interval</th><th>Failures</th></tr></thead>
          <tbody>{data.offers.map((o) => <tr key={o.offer_id}><td>{o.retailer}</td><td>{o.title}</td>
            <td><Chip tone={o.status === "ok" && o.fresh ? "good" : o.status === "blocked" ? "bad" : "warn"}>{o.circuit_open ? "circuit open" : !o.monitoring_enabled ? "paused" : o.fresh ? "fresh" : o.status}</Chip></td>
            <td>{ago(o.last_success_at)}</td><td>{o.effective_interval_s}s</td><td>{o.consecutive_failures}{o.last_error ? ` · ${o.last_error}` : ""}</td></tr>)}</tbody></table></div>}
      </Glass>
      <Glass><h2>Retailer errors (24h)</h2>{data.retailer_errors_24h.length === 0 ? <p className="muted">None.</p> :
        data.retailer_errors_24h.map((e, i) => <div key={i} className="row-line"><span>{e.retailer} · {e.category}</span><strong>{e.count}</strong></div>)}</Glass>
    </>
  );
}
