import { useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "../hooks";
import { inr, when } from "../format";
import { Empty, ErrorBox, Spinner } from "./ui";

const RANGES = [["1d", "Day"], ["1w", "Week"], ["1m", "Month"], ["6m", "6 months"], ["1y", "Year"], ["all", "All time"]];
const SHADES = ["#4f6ef7", "#7c55f5", "#1a9e5c", "#c47700", "#d94040", "#3abfcf"];

function Tip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="tooltip">
      <div className="muted small">{when(new Date(label).toISOString())}</div>
      {payload.map((p) => (
        <div key={p.dataKey}><strong>{inr(p.value)}</strong> <span className="muted small">{p.dataKey === "price" ? "" : p.dataKey}</span></div>
      ))}
    </div>
  );
}

export default function PriceChart({ productId, target, refreshKey }) {
  const [range, setRange] = useState("all");
  const [byRetailer, setBy] = useState(false);
  const { data, error, loading, reload } = useApi(`/products/${productId}/history`, {
    params: { range, by_retailer: byRetailer ? 1 : undefined, _r: refreshKey }, pollMs: 30000,
  });

  const { rows, keys } = useMemo(() => {
    if (!data) return { rows: [], keys: [] };
    if (byRetailer && Object.keys(data.series).length) {
      const map = new Map();
      Object.entries(data.series).forEach(([name, pts]) => pts.forEach((p) => {
        const t = new Date(p.t).getTime(); map.set(t, { ...(map.get(t) || { t }), [name]: Number(p.price) });
      }));
      return { rows: [...map.values()].sort((a, b) => a.t - b.t), keys: Object.keys(data.series) };
    }
    return { rows: data.points.map((p) => ({ t: new Date(p.t).getTime(), price: Number(p.price) })), keys: ["price"] };
  }, [data, byRetailer]);

  const st = data?.stats;
  return (
    <div>
      <div className="toolbar">
        <div className="seg" role="tablist" aria-label="Date range">
          {RANGES.map(([k, l]) => <button key={k} role="tab" aria-selected={range === k} className={range === k ? "on" : ""} onClick={() => setRange(k)}>{l}</button>)}
        </div>
        <label className="check"><input type="checkbox" checked={byRetailer} onChange={(e) => setBy(e.target.checked)} /> Compare retailers</label>
      </div>
      {loading && !data && <Spinner label="Loading price history…" />}
      <ErrorBox error={error} onRetry={reload} />
      {data && (data.insufficient || rows.length < 2) ? (
        <Empty title="Not enough history yet">
          PriceRadar only charts prices it has actually recorded{rows.length === 1 ? ` (one so far: ${inr(rows[0].price)})` : ""}. The graph fills in as monitoring observes changes — nothing is back-filled or estimated.
        </Empty>
      ) : data && (
        <>
          <div className="chart" data-testid="price-chart">
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={rows} margin={{ top: 10, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="rgba(100,130,255,0.10)" vertical={false} />
                <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} stroke="#9298b8" fontSize={11} tickLine={false} axisLine={false}
                       tickFormatter={(t) => new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short" })} />
                <YAxis stroke="#9298b8" fontSize={11} width={70} domain={["auto", "auto"]} tickLine={false} axisLine={false} tickFormatter={(v) => inr(v)} />
                <Tooltip content={<Tip />} />
                {target && <ReferenceLine y={Number(target)} stroke="#1a9e5c" strokeDasharray="6 4" label={{ value: `Target ${inr(target)}`, fill: "#1a9e5c", fontSize: 11, position: "insideTopRight" }} />}
                {keys.map((k, i) => <Line key={k} type="stepAfter" dataKey={k} stroke={SHADES[i % SHADES.length]} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />)}
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="stats-row">
            {[["Current", st.current], ["Lowest", st.min], ["Highest", st.max], ["Average", st.average], ["Median", st.median]].map(([l, v]) =>
              <div key={l}><span className="label">{l}</span><strong>{inr(v)}</strong></div>)}
          </div>
          <p className="muted small">{st.count} recorded observations{data.bucket_seconds ? ` · aggregated into ${Math.round(data.bucket_seconds / 60)}-minute buckets (extremes preserved)` : ""}. Average and median are time-weighted. {data.provenance}</p>
        </>
      )}
    </div>
  );
}
