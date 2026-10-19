// API base URL comes from the build environment, never a hardcoded localhost in production builds.
const BASE = (process.env.REACT_APP_API_BASE_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:5000" : "")).replace(/\/$/, "");

export class ApiError extends Error {
  constructor(message, { code, status, network } = {}) {
    super(message);
    this.code = code; this.status = status; this.network = !!network;
  }
}

export async function api(path, { method = "GET", body, signal, params } = {}) {
  const qs = params ? "?" + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== "" && v !== null)) : "";
  let res;
  try {
    res = await fetch(`${BASE}/api/v1${path}${qs}`, {
      method, signal,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError("Can't reach the PriceRadar backend. Check that it is running and REACT_APP_API_BASE_URL is correct.", { network: true });
  }
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON error body */ }
  if (!res.ok || (data && data.success === false)) {
    const e = data?.error;
    throw new ApiError(e?.message || `Request failed (${res.status})`, { code: e?.code, status: res.status });
  }
  return data;
}
