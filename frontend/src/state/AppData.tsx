import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createApiClient, makeEndpoints, type Endpoints } from "../lib/api";
import type { HealthData } from "../lib/types";

export interface Resource<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  loadedAt: Date | null;
}

const EMPTY: Resource<never> = { data: null, error: null, loading: false, loadedAt: null };

interface AppData {
  api: Endpoints;
  health: Resource<HealthData>;
  loadHealth: () => Promise<void>;
  ensureHealth: () => void;
}

const AppDataContext = createContext<AppData | null>(null);

/** Load a resource; a newer call cancels the older one so results never arrive out of order. */
export function useLoader<T>(fetcher: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<Resource<T>>(EMPTY);
  const controller = useRef<AbortController | null>(null);
  const requested = useRef(false);

  const load = useCallback(async () => {
    requested.current = true;
    controller.current?.abort();
    const ctl = new AbortController();
    controller.current = ctl;
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await fetcher(ctl.signal);
      if (ctl.signal.aborted) return;
      setState({ data, error: null, loading: false, loadedAt: new Date() });
    } catch (error) {
      if (ctl.signal.aborted) return;
      setState((s) => ({ ...s, loading: false, error }));
    }
  }, [fetcher]);

  const ensure = useCallback(() => {
    if (!requested.current) void load();
  }, [load]);

  return { state, load, ensure };
}

export function AppDataProvider({ children, endpoints }: { children: ReactNode; endpoints?: Endpoints }) {
  const api = useMemo(() => endpoints ?? makeEndpoints(createApiClient()), [endpoints]);
  const healthLoader = useLoader(useCallback((signal: AbortSignal) => api.health(signal), [api]));

  const value = useMemo<AppData>(() => ({
    api,
    health: healthLoader.state,
    loadHealth: healthLoader.load,
    ensureHealth: healthLoader.ensure,
  }), [api, healthLoader.state, healthLoader.load, healthLoader.ensure]);

  return <AppDataContext.Provider value={value}>{children}</AppDataContext.Provider>;
}

export function useAppData(): AppData {
  const ctx = useContext(AppDataContext);
  if (!ctx) throw new Error("useAppData must be used inside AppDataProvider");
  return ctx;
}

export function useHealth() {
  const { health, loadHealth, ensureHealth } = useAppData();
  useEffect(() => ensureHealth(), [ensureHealth]);
  return { ...health, reload: loadHealth };
}
