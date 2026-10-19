import { ago, VERDICT } from "../format";

export const Glass = ({ children, className = "", ...p }) => <section className={`glass ${className}`} {...p}>{children}</section>;

export function PageHead({ title, sub, actions }) {
  return (
    <header className="page-head">
      <div><h1>{title}</h1>{sub && <p className="muted">{sub}</p>}</div>
      <div className="actions">{actions}</div>
    </header>
  );
}

export const Chip = ({ tone = "neutral", children }) => <span className={`chip chip-${tone}`}>{children}</span>;

export function Spinner({ label = "Loading…" }) {
  return <div className="spinner-row" role="status"><span className="spinner" aria-hidden />{label}</div>;
}

export function ErrorBox({ error, onRetry }) {
  if (!error) return null;
  return (
    <div className="notice notice-bad" role="alert">
      <strong>{error.network ? "Backend unavailable" : "Something went wrong"}</strong>
      <div>{error.message}</div>
      {onRetry && <button className="btn btn-ghost" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function Empty({ title, children, action }) {
  return <div className="empty"><div className="empty-mark" aria-hidden>◎</div><h3>{title}</h3><p className="muted">{children}</p>{action}</div>;
}

export function Verdict({ v }) {
  const tone = { historically_low: "good", below_average: "good", above_average: "warn", historically_high: "bad" }[v] || "neutral";
  return <Chip tone={tone}>{VERDICT[v] || v}</Chip>;
}

export function Freshness({ offer }) {
  const f = offer.freshness;
  if (offer.status === "blocked") return <Chip tone="bad">Blocked by retailer</Chip>;
  if (!f.fresh) return <Chip tone="warn">Stale · last verified {ago(offer.last_success_at)}</Chip>;
  return <span className="muted small">Verified {ago(offer.last_success_at)} · checked every ~{f.effective_interval_s}s{f.limited_by_retailer_floor ? " (retailer limit)" : ""}</span>;
}
