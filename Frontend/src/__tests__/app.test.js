import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "../App";
import TrackPage from "../pages/TrackPage";
import ProductPage from "../pages/ProductPage";
import { Overview } from "../pages/Lists";

// Minimal fetch router: [method, path-regex] -> handler(body) => json
function mockApi(routes) {
  const calls = [];
  global.fetch = jest.fn(async (url, opts = {}) => {
    const path = url.replace(/^https?:\/\/[^/]+/, "").replace("/api/v1", "").split("?")[0];
    const method = opts.method || "GET";
    calls.push({ method, path, body: opts.body ? JSON.parse(opts.body) : undefined });
    for (const [m, re, h] of routes) {
      if (m === method && re.test(path)) {
        const out = await h(opts.body ? JSON.parse(opts.body) : undefined);
        const status = out?.__status || 200;
        return { ok: status < 400, status, json: async () => out };
      }
    }
    return { ok: false, status: 404, json: async () => ({ success: false, error: { code: "not_found", message: "no route" } }) };
  });
  return calls;
}

const offer = (o = {}) => ({
  id: 1, retailer: "Amazon India", retailer_key: "amazon", title: "Phone 256GB", image_url: null, url: "https://www.amazon.in/dp/B0CMDRCZ1Y",
  price: "74000.00", mrp: "89999.00", discount: { amount: "15999.00", pct: "17.8" }, delivery_charge: null, total_payable: null,
  availability: "in_stock", variant: "256gb", match_status: "source", match_reasons: [], verified: true, status: "ok", last_error: null,
  last_checked_at: new Date().toISOString(), last_success_at: new Date().toISOString(), retailer_live_verified: false,
  freshness: { fresh: true, age_seconds: 5, effective_interval_s: 60, requested_interval_s: 15, limited_by_retailer_floor: true }, fresh: true, is_best: true, ...o,
});
const productPayload = (offers = [offer()]) => ({
  success: true, product: { id: 7, title: "Phone 256GB", brand: "Samsung", image_url: null, paused: false, attributes: { storage: ["256gb"], colours: [] } },
  offers, best_offer_id: offers.find((o) => o.is_best)?.id ?? null, no_fresh_offer: !offers.some((o) => o.is_best), rejected_candidates: [],
  stats: { count: 1, current: "74000.00", change_7d: null, change_30d: null },
  verdict: { label: "around_average", preliminary: true, recommendation: "Preliminary: not enough history yet.", explanation: "Only one price.", disclaimer: "Not a prediction." },
  insights: [], monitoring: { target_interval_s: 15, note: "Effective intervals are limited per retailer." },
});
const baseRoutes = (extra = []) => [
  ["GET", /^\/health$/, () => ({ status: "ok" })],
  ["GET", /^\/notifications$/, () => ({ success: true, total: 0, items: [] })],
  ["GET", /^\/settings$/, () => ({ success: true, email: "me@example.com", smtp: { configured: true, host: "h", port: 587, tls: true }, monitoring: {}, default_cooldown_s: 3600 })],
  ...extra,
];

beforeEach(() => { window.location.hash = ""; sessionStorage.clear(); });

test("track form validates empty and non-URL input without calling the API", async () => {
  const calls = mockApi(baseRoutes([["GET", /^\/products$/, () => ({ items: [] })], ["GET", /^\/retailers$/, () => ({ retailers: [] })]]));
  render(<TrackPage />);
  await userEvent.click(screen.getByRole("button", { name: "Track" }));
  expect(await screen.findByText(/Paste a product link first/)).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("Product URL"), "iphone cheap");
  await userEvent.click(screen.getByRole("button", { name: "Track" }));
  expect(await screen.findByText(/doesn't look like a link/)).toBeInTheDocument();
  expect(calls.some((c) => c.method === "POST")).toBe(false);
});

test("track flow polls the job, shows stages, then navigates to the product", async () => {
  let polls = 0;
  mockApi(baseRoutes([
    ["GET", /^\/products$/, () => ({ items: [] })], ["GET", /^\/retailers$/, () => ({ retailers: [{ key: "amazon", name: "Amazon India", live_verified: false }] })],
    ["POST", /^\/products\/identify$/, () => ({ success: true, job: { id: 3, status: "running", stage: "resolving", warnings: [] } })],
    ["GET", /^\/jobs\/3$/, () => (++polls < 2 ? { success: true, job: { id: 3, status: "running", stage: "discovering", warnings: [] } } : { success: true, job: { id: 3, status: "done", stage: "done", product_id: 7, warnings: [] } })],
  ]));
  render(<TrackPage />);
  await userEvent.type(screen.getByLabelText("Product URL"), "Check https://amzn.in/d/abc");
  await userEvent.click(screen.getByRole("button", { name: "Track" }));
  expect(await screen.findByText(/Searching other retailers/)).toBeInTheDocument();
  await waitFor(() => expect(window.location.hash).toBe("#/products/7"), { timeout: 4000 });
});

test("track flow surfaces a backend rejection (unsupported retailer)", async () => {
  mockApi(baseRoutes([
    ["GET", /^\/products$/, () => ({ items: [] })], ["GET", /^\/retailers$/, () => ({ retailers: [] })],
    ["POST", /^\/products\/identify$/, () => ({ __status: 422, success: false, error: { code: "unsupported_retailer", message: "This retailer is not supported yet." } })],
  ]));
  render(<TrackPage />);
  await userEvent.type(screen.getByLabelText("Product URL"), "https://example.com/x");
  await userEvent.click(screen.getByRole("button", { name: "Track" }));
  expect(await screen.findByText(/not supported yet/)).toBeInTheDocument();
});

test("overview shows an honest empty state for a new user and no fabricated numbers", async () => {
  mockApi(baseRoutes([["GET", /^\/dashboard$/, () => ({ success: true, tracked_products: 0, active_alerts: 0, confirmed_price_drops_7d: 0, potential_savings_to_targets: "0", recent_products: [], price_drops: [], recent_offers: [], recent_notifications: [], smtp_configured: true })]]));
  render(<Overview />);
  expect(await screen.findByText(/Start by tracking a product/)).toBeInTheDocument();
  expect(screen.getAllByText("0").length).toBeGreaterThanOrEqual(3);
});

test("backend unavailable is reported clearly", async () => {
  global.fetch = jest.fn(() => Promise.reject(new TypeError("Failed to fetch")));
  render(<App />);
  expect((await screen.findAllByText(/Backend unavailable/i)).length).toBeGreaterThan(0);
});

test("navigation toggle works and routes render", async () => {
  mockApi(baseRoutes([["GET", /^\/dashboard$/, () => ({ success: true, tracked_products: 0, active_alerts: 0, confirmed_price_drops_7d: 0, potential_savings_to_targets: "0", recent_products: [], price_drops: [], recent_offers: [], recent_notifications: [], smtp_configured: false })]]));
  render(<App />);
  const btn = screen.getByLabelText("Toggle navigation");
  expect(btn).toHaveAttribute("aria-expanded", "false");
  await userEvent.click(btn);
  expect(btn).toHaveAttribute("aria-expanded", "true");
  expect(await screen.findByText(/Email delivery isn't configured/)).toBeInTheDocument();
});

test("product page: best offer, honest empty history, stale offers never shown as lowest", async () => {
  mockApi(baseRoutes([
    ["GET", /^\/products\/7$/, () => productPayload([offer(), offer({ id: 2, retailer: "Croma", retailer_key: "croma", price: "70000.00", is_best: false, fresh: false, status: "stale", freshness: { fresh: false, age_seconds: 99999, effective_interval_s: 60, limited_by_retailer_floor: false }, last_success_at: new Date(Date.now() - 2 * 864e5).toISOString() })])],
    ["GET", /^\/products\/7\/history$/, () => ({ success: true, points: [{ t: new Date().toISOString(), price: "74000.00" }], series: {}, stats: { count: 1 }, insufficient: true, provenance: "x" })],
    ["GET", /^\/products\/7\/target-suggestions$/, () => ({ success: true, available: false })],
    ["GET", /^\/alerts$/, () => ({ success: true, alerts: [] })],
  ]));
  render(<ProductPage id="7" />);
  expect(await screen.findByText("Lowest verified price")).toBeInTheDocument();
  expect(screen.getAllByText("₹74,000").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Lowest verified")).toHaveLength(1);       // only the fresh Amazon offer
  expect(screen.getByText(/Stale · last verified 2d ago/)).toBeInTheDocument();
  expect(await screen.findByText(/Not enough history yet/)).toBeInTheDocument();
  expect(screen.getAllByText(/Parser not yet live-verified/).length).toBeGreaterThan(0);
});

test("product page: target slider suggestions and alert creation posts the chosen target", async () => {
  const calls = mockApi(baseRoutes([
    ["GET", /^\/products\/7$/, () => productPayload()],
    ["GET", /^\/products\/7\/history$/, () => ({ success: true, points: [], series: {}, stats: { count: 0 }, insufficient: true, provenance: "x" })],
    ["GET", /^\/products\/7\/target-suggestions$/, () => ({ success: true, available: true, current: "10000.00", min: "8000.00", max: "12000.00", slider: { min: "6400.00", max: "12000.00", step: 1 }, disclaimer: "Historical minimums do not guarantee future prices.",
      suggestions: [{ key: "small", label: "Small drop", price: "9500.00", pct_drop_needed: "5.00", times_reached: 1 }, { key: "moderate", label: "Moderate drop", price: "9000.00", pct_drop_needed: "10.00", times_reached: 1 }, { key: "historical_low", label: "Historical low", price: "8000.00", pct_drop_needed: "20.00", times_reached: 0 }] }) ],
    ["GET", /^\/alerts$/, () => ({ success: true, alerts: [] })],
    ["POST", /^\/alerts$/, () => ({ success: true, alert: { id: 1 } })],
  ]));
  render(<ProductPage id="7" />);
  await userEvent.click(await screen.findByRole("button", { name: /Historical low/ }));
  expect(screen.getByLabelText("Custom target price")).toHaveValue(8000);
  expect(screen.getByText("20.0%")).toBeInTheDocument();                  // drop needed
  await userEvent.clear(screen.getByLabelText("Custom target price"));
  await userEvent.type(screen.getByLabelText("Custom target price"), "7500");   // below recorded low is allowed
  await userEvent.click(screen.getByRole("button", { name: "Create alert" }));
  await waitFor(() => expect(screen.getByText("Alert saved.")).toBeInTheDocument());
  const post = calls.find((c) => c.method === "POST" && c.path === "/alerts");
  expect(post.body).toMatchObject({ product_id: 7, kind: "target_reached", target_price: 7500, email: "me@example.com" });
});

test("alert creation shows the server's validation error", async () => {
  mockApi(baseRoutes([
    ["GET", /^\/products\/7$/, () => productPayload()],
    ["GET", /^\/products\/7\/history$/, () => ({ success: true, points: [], series: {}, stats: { count: 0 }, insufficient: true, provenance: "x" })],
    ["GET", /^\/products\/7\/target-suggestions$/, () => ({ success: true, available: false })],
    ["GET", /^\/alerts$/, () => ({ success: true, alerts: [] })],
    ["POST", /^\/alerts$/, () => ({ __status: 400, success: false, error: { code: "validation_error", message: "Enter a valid email address." } })],
  ]));
  render(<ProductPage id="7" />);
  await userEvent.selectOptions(await screen.findByLabelText("Notify me when"), "any_decrease");
  await userEvent.click(screen.getByRole("button", { name: "Create alert" }));
  expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument();
});
