import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

export function useHashRoute() {
  const get = () => window.location.hash.replace(/^#/, "") || "/";
  const [route, setRoute] = useState(get);
  useEffect(() => {
    const on = () => setRoute(get());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}
export const go = (path) => { window.location.hash = path; };

/** Fetch + loading/error state; optional polling (done by the backend worker, not by scraping from the browser). */
export function useApi(path, { params, pollMs, enabled = true } = {}) {
  const [state, setState] = useState({ data: null, error: null, loading: enabled });
  const key = path + JSON.stringify(params || {});
  const alive = useRef(true);
  const load = useCallback(async (silent) => {
    if (!enabled) return;
    if (!silent) setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await api(path, { params });
      if (alive.current) setState({ data, error: null, loading: false });
    } catch (e) {
      if (alive.current) setState((s) => ({ data: silent ? s.data : null, error: e, loading: false }));
    }
  }, [key, enabled]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    alive.current = true; load(false);
    const t = pollMs ? setInterval(() => load(true), pollMs) : null;
    return () => { alive.current = false; if (t) clearInterval(t); };
  }, [load, pollMs]);
  return { ...state, reload: () => load(false) };
}
