import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Chip, Empty, ErrorBox, Glass, PageHead } from "../components/ui";
import { inr } from "../format";
import { go, useApi } from "../hooks";

const STAGES = [
  { key: "resolving", label: "Resolving link", icon: "🔗", hint: "Unshortening & DNS check" },
  { key: "extracting", label: "Identifying product", icon: "📦", hint: "Extracting title & specs" },
  { key: "discovering", label: "Searching other retailers", icon: "🔍", hint: "Amazon, Flipkart, Croma..." },
  { key: "matching", label: "Verifying matches", icon: "⚡", hint: "Cross-store price comparison" }
];

export default function TrackPage() {
  const [text, setText] = useState(() => { try { const v = sessionStorage.getItem("pr_prefill") || ""; sessionStorage.removeItem("pr_prefill"); return v; } catch { return ""; } });
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const cancelled = useRef(false);
  const recent = useApi("/products", { params: { per_page: 5 } });
  const retailers = useApi("/retailers");

  useEffect(() => () => { cancelled.current = true; }, []);

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    if (!text.trim()) { setError({ message: "Paste a product link first." }); return; }
    if (!/https?:\/\//i.test(text)) { setError({ message: "That doesn't look like a link. Paste a product URL (a shared message containing one also works)." }); return; }
    setBusy(true); cancelled.current = false;
    try {
      let j = (await api("/products/identify", { method: "POST", body: { input: text } })).job;
      setJob(j);
      while (!cancelled.current && (j.status === "queued" || j.status === "running")) {
        await new Promise((r) => setTimeout(r, 1000));
        if (cancelled.current) break;
        j = (await api(`/jobs/${j.id}`)).job; setJob(j);
      }
      if (cancelled.current) return;
      if (j.status === "failed") { setError({ message: j.error || "Could not identify this product." }); setBusy(false); return; }
      go(`/products/${j.product_id}`);
    } catch (er) { setError(er); setBusy(false); }
  };
  const cancel = () => { cancelled.current = true; setBusy(false); setJob(null); };
  const paste = async () => { try { setText(await navigator.clipboard.readText()); } catch { setError({ message: "Clipboard access was blocked — paste with Ctrl/⌘+V instead." }); } };
  const idx = job ? STAGES.findIndex((s) => s.key === job.stage) : -1;
  const progressPercent = idx < 0 ? 10 : Math.min(100, Math.round(((idx + 0.6) / STAGES.length) * 100));

  return (
    <>
      <PageHead title="Track a product" sub="Paste a link from any supported store. PriceRadar identifies the product, looks for the same item elsewhere and starts recording its price." />
      <Glass className="track-box">
        <form onSubmit={submit}>
          <div className="inline-form big">
            <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="https://www.amazon.in/dp/…  or  a shared link / message" aria-label="Product URL" disabled={busy} />
            <button type="button" className="btn btn-ghost" onClick={paste} disabled={busy}>Paste</button>
            {busy ? <button type="button" className="btn btn-ghost" onClick={cancel}>Cancel</button> : <button className="btn btn-primary">Track</button>}
          </div>
        </form>
        {busy && (
          <div className="stage-tracker" aria-live="polite">
            <div className="stage-tracker-header">
              <div className="stage-tracker-title">
                <span className="stage-radar-dot" aria-hidden="true" />
                <span>
                  {idx >= 0 ? STAGES[idx].label : "Initiating tracking..."}
                </span>
              </div>
              <span className="stage-percent">{progressPercent}%</span>
            </div>

            <div className="stage-progress-bar-wrap" role="progressbar" aria-valuenow={progressPercent} aria-valuemin="0" aria-valuemax="100">
              <div className="stage-progress-bar-fill" style={{ width: `${progressPercent}%` }} />
            </div>

            <div className="stage-steps">
              {STAGES.map((s, i) => {
                const isDone = i < idx;
                const isActive = i === idx;
                return (
                  <div key={s.key} className={`stage-step-card ${isDone ? "done" : ""} ${isActive ? "active" : ""}`}>
                    <div className="stage-icon-bubble">
                      {isDone ? "✓" : isActive ? s.icon : (i + 1)}
                    </div>
                    <div className="stage-label">{s.label}</div>
                    <div className="stage-hint">{s.hint}</div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
        <ErrorBox error={error} />
        {job?.warnings?.length > 0 && !busy && <div className="notice notice-warn">{job.warnings.join(" ")}</div>}
        <p className="muted small">Cancelling stops waiting; a job already started on the server may still finish and appear in your tracked products.</p>
      </Glass>
      <div className="two-col">
        <Glass>
          <h2>Supported retailers</h2>
          <div className="chips">{(retailers.data?.retailers || []).map((r) => <Chip key={r.key} tone={r.live_verified ? "good" : "neutral"}>{r.name}</Chip>)}</div>
          <p className="muted small">Short links (amzn.in, dl.flipkart.com …) are expanded safely. Retailer parsers are tested against fixtures; none has yet been verified against live pages — treat live results accordingly.</p>
        </Glass>
        <Glass>
          <h2>Recently tracked</h2>
          {recent.data?.items?.length ? recent.data.items.map((p) => (
            <a key={p.id} className="row-line link" href={`#/products/${p.id}`}><span>{p.title}</span><strong>{inr(p.current_price)}</strong></a>
          )) : <Empty title="Nothing tracked yet">Your tracked products will appear here.</Empty>}
        </Glass>
      </div>
    </>
  );
}
