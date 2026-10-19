export const inr = (v, frac = 0) =>
  v === null || v === undefined || v === "" ? "—"
    : "₹" + Number(v).toLocaleString("en-IN", { minimumFractionDigits: Number(v) % 1 ? 2 : frac, maximumFractionDigits: 2 });

export const pct = (v) => (v === null || v === undefined ? "—" : `${Number(v).toFixed(1)}%`);

export const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "never");

export function ago(iso) {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export const VERDICT = {
  historically_low: "Historically low", below_average: "Below average", around_average: "Around average",
  above_average: "Above average", historically_high: "Historically high", unknown: "No data yet",
};

export const ALERT_KINDS = {
  target_reached: "Target price reached", any_decrease: "Any confirmed price drop", any_increase: "Any confirmed price increase",
  pct_drop: "Drop by a percentage", amount_drop: "Drop by a rupee amount", flash_drop: "Sudden (flash) drop",
  historical_low: "New historical low", back_in_stock: "Back in stock", lower_elsewhere: "Lower price at another retailer",
};
