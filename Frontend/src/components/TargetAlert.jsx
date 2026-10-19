import { useState } from "react";
import { api } from "../api";
import { ALERT_KINDS, inr, pct } from "../format";
import { useApi } from "../hooks";
import { ErrorBox, Empty } from "./ui";

export function TargetPicker({ productId, value, onChange }) {
  const { data, error } = useApi(`/products/${productId}/target-suggestions`);
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  if (!data.available) return <Empty title="No price recorded yet">Target suggestions appear once a verified price exists.</Empty>;
  const cur = Number(data.current), lo = Number(data.min), hi = Number(data.max);
  const sMin = Math.floor(Number(data.slider.min)), sMax = Math.ceil(Number(data.slider.max));
  const v = value ?? Math.round(Number(data.suggestions[1].price));
  const saving = cur - v, need = cur ? (saving / cur) * 100 : 0;
  const match = data.suggestions.find((s) => Math.round(Number(s.price)) === Math.round(v));
  return (
    <div className="target">
      <div className="range-labels"><span>Lowest recorded<strong>{inr(lo)}</strong></span><span>Current<strong>{inr(cur)}</strong></span><span>Highest recorded<strong>{inr(hi)}</strong></span></div>
      <input type="range" aria-label="Target price" min={sMin} max={sMax} step={data.slider.step} value={Math.min(Math.max(v, sMin), sMax)}
             onChange={(e) => onChange(Number(e.target.value))} />
      <div className="target-readout">
        <div><span className="label">Your target</span>
          <input className="input price-input" type="number" aria-label="Custom target price" min="1" value={v} onChange={(e) => onChange(Number(e.target.value))} /></div>
        <div><span className="label">You would save</span><strong>{saving > 0 ? inr(saving) : "—"}</strong></div>
        <div><span className="label">Drop needed</span><strong>{saving > 0 ? pct(need) : "Already at/below"}</strong></div>
        <div><span className="label">Reached before</span><strong>{match ? `${match.times_reached}×` : "n/a"}</strong></div>
      </div>
      <div className="suggest">
        {data.suggestions.map((s) => (
          <button key={s.key} className={`btn btn-ghost ${Math.round(Number(s.price)) === Math.round(v) ? "on" : ""}`} onClick={() => onChange(Math.round(Number(s.price)))}>
            {s.label}<small>{inr(s.price)} · {pct(s.pct_drop_needed)} lower</small>
          </button>
        ))}
        <button className="btn btn-ghost" onClick={() => onChange(Math.max(1, Math.round(lo * 0.95)))}>Custom (below low)<small>{inr(Math.round(lo * 0.95))}</small></button>
      </div>
      <p className="muted small">{data.disclaimer}</p>
    </div>
  );
}

export function AlertForm({ productId, target, defaultEmail, onCreated }) {
  const [kind, setKind] = useState("target_reached");
  const [email, setEmail] = useState(defaultEmail || "");
  const [threshold, setThreshold] = useState("");
  const [cool, setCool] = useState(3600);
  const [minChange, setMin] = useState("");
  const [state, setState] = useState({ busy: false, err: null, ok: null });
  const submit = async (e) => {
    e.preventDefault();
    setState({ busy: true, err: null, ok: null });
    const body = { product_id: productId, kind, email: email || undefined, cooldown_s: Number(cool) };
    if (kind === "target_reached") body.target_price = target;
    if (kind === "pct_drop" || kind === "flash_drop") body.pct_threshold = threshold || undefined;
    if (kind === "amount_drop") body.amount_threshold = threshold;
    if (minChange) body.min_change = minChange;
    try { await api("/alerts", { method: "POST", body }); setState({ busy: false, err: null, ok: "Alert saved." }); onCreated?.(); }
    catch (er) { setState({ busy: false, err: er, ok: null }); }
  };
  const needsThr = ["pct_drop", "flash_drop", "amount_drop"].includes(kind);
  return (
    <form onSubmit={submit} className="form-grid">
      <label>Notify me when<select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
        {Object.entries(ALERT_KINDS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
      {kind === "target_reached" && <div className="muted small field-note">Target: <strong>{target ? inr(target) : "choose a target above"}</strong></div>}
      {needsThr && <label>{kind === "amount_drop" ? "Drop of at least (₹)" : "Drop of at least (%)"}
        <input className="input" type="number" min="0.01" step="0.01" value={threshold} onChange={(e) => setThreshold(e.target.value)} required={kind !== "flash_drop"} placeholder={kind === "flash_drop" ? "default 10" : ""} /></label>}
      <label>Email<input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" required /></label>
      <label>Cooldown<select className="input" value={cool} onChange={(e) => setCool(e.target.value)}>
        <option value={0}>None — notify on every change</option><option value={900}>15 minutes</option><option value={3600}>1 hour</option><option value={21600}>6 hours</option><option value={86400}>24 hours</option></select></label>
      <label>Ignore changes smaller than (₹, optional)<input className="input" type="number" min="0" step="1" value={minChange} onChange={(e) => setMin(e.target.value)} /></label>
      <div><button className="btn btn-primary" disabled={state.busy || (kind === "target_reached" && !target)}>{state.busy ? "Saving…" : "Create alert"}</button></div>
      {state.ok && <div className="notice notice-good" role="status">{state.ok}</div>}
      <ErrorBox error={state.err} />
    </form>
  );
}
