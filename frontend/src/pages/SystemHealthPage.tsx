import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Database, Gauge, RefreshCw, Server, Workflow } from "lucide-react";
import { ApiError } from "../lib/api";
import { formatClock } from "../lib/format";
import { deriveHealth, type HealthState } from "../lib/health";
import { useAppData, useHealth } from "../state/AppData";
import { HealthBadge } from "../components/common/status";
import { Button, Card, ErrorAlert, PageHeader, Skeleton } from "../components/common/ui";

function Tile({ title, icon, state, detail, loading }:
  { title: string; icon: ReactNode; state: HealthState; detail: ReactNode; loading: boolean }) {
  const headingId = `tile-${title.toLowerCase().replace(/\s+/g, "-")}`; // ids must not contain spaces
  return (
    <Card as="article" className="p-5" aria-labelledby={headingId}>
      <div className="flex items-center justify-between">
        <h2 id={headingId} className="flex items-center gap-2 text-sm font-semibold"><span className="text-primary" aria-hidden>{icon}</span>{title}</h2>
        {loading ? <Skeleton className="h-5 w-20" /> : <HealthBadge state={state} />}
      </div>
      <p className="mt-3 text-sm text-muted">{detail}</p>
    </Card>
  );
}

export function SystemHealthPage() {
  const { api } = useAppData();
  const health = useHealth();
  const [ready, setReady] = useState<{ state: HealthState; loading: boolean; error: unknown }>({ state: "unknown", loading: true, error: null });

  const checkReady = useCallback(async () => {
    setReady((r) => ({ ...r, loading: true, error: null }));
    try {
      await api.ready();
      setReady({ state: "healthy", loading: false, error: null });
    } catch (error) {
      const notReady = error instanceof ApiError && error.status === 503;
      setReady({ state: notReady ? "not_ready" : "unknown", loading: false, error: notReady ? null : error });
    }
  }, [api]);

  useEffect(() => { void checkReady(); }, [checkReady]);

  const status = deriveHealth(health.data, health.error);
  const loading = health.loading && !health.data && !health.error;
  const refresh = () => { void health.reload(); void checkReady(); };

  return (
    <>
      <PageHeader title="System Health"
                  description="Live status from GET /api/health and GET /api/health/ready. Checked when you open this page or press Refresh (no background polling)."
                  actions={<Button onClick={refresh} loading={health.loading || ready.loading} loadingText="Checking…"
                                   icon={<RefreshCw className="h-4 w-4" aria-hidden />}>Refresh</Button>} />
      {health.error ? <div className="mb-6"><ErrorAlert error={health.error} onRetry={refresh} retrying={health.loading} /></div> : null}
      <div className="grid gap-4 md:grid-cols-2">
        <Tile title="API" icon={<Server className="h-4 w-4" />} state={status.api} loading={loading}
              detail={status.api === "healthy" ? "The FastAPI server is answering." : status.api === "unavailable" ? "The FastAPI server cannot be reached." : "Not checked yet."} />
        <Tile title="Audit database" icon={<Database className="h-4 w-4" />} state={status.database} loading={loading}
              detail={status.database === "healthy" ? "SQLite audit log is available." : status.database === "unavailable" ? "The audit database is down: results cannot be saved." : "Unknown while the API is unreachable."} />
        <Tile title="Warm-up" icon={<Workflow className="h-4 w-4" />} state={status.warmup} loading={loading}
              detail={health.data?.warmup_detail ? <>Backend says: <span className="font-mono text-ink">{health.data.warmup_detail}</span></> : "Unknown while the API is unreachable."} />
        <Tile title="Readiness" icon={<Gauge className="h-4 w-4" />} state={ready.state} loading={ready.loading}
              detail={ready.state === "healthy" ? "Ready to serve requests (/api/health/ready)." : ready.state === "not_ready" ? "Not ready yet: still warming up, warm-up failed, or the database is down." : "Readiness could not be checked."} />
      </div>
      <p className="mt-4 text-sm text-muted" aria-live="polite">
        Last successful check: {health.loadedAt ? formatClock(health.loadedAt) : "never"}
      </p>
    </>
  );
}
