// Audit trail on the Dashboard: reuses the existing audit API (hash-chain check + recent results), no new store.
import { useCallback, useEffect } from "react";
import { Link } from "react-router-dom";
import { ScrollText, ShieldCheck, ShieldX } from "lucide-react";
import { formatTimestamp } from "../../lib/format";
import { useAppData, useLoader } from "../../state/AppData";
import { OutcomeBadge as AuditOutcomeBadge } from "../common/status";
import { Card, CardHeader, ErrorAlert, Skeleton } from "../common/ui";

/** Audit trail: GET /api/audit/verify and /api/audit/results (both live today, analyst or higher). Loads on its
 *  own, so it works even while the stats are unavailable; a failure here only affects this card. */
export function AuditTrail() {
  const { api } = useAppData();
  const chain = useLoader(useCallback((s: AbortSignal) => api.verifyAudit(s), [api]));
  const recent = useLoader(useCallback((s: AbortSignal) => api.results(s), [api]));
  const loadChain = chain.load;
  const loadRecent = recent.load;
  useEffect(() => { void loadChain(); void loadRecent(); }, [loadChain, loadRecent]);
  const c = chain.state;
  const r = recent.state;

  return (
    <Card aria-labelledby="audit-trail-heading">
      <CardHeader id="audit-trail-heading" title="Audit trail" icon={<ScrollText className="h-5 w-5" />}
                  description="Tamper-evident log of saved results and reviewer feedback."
                  actions={<Link to="/analyst/audit" className="text-sm font-medium text-primary hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">Open Audit History</Link>} />
      <div className="grid gap-6 p-5 md:grid-cols-2">
        <section aria-label="Audit log integrity">
          <p className="text-sm font-medium text-ink">Integrity check</p>
          {c.loading && !c.data ? (
            <Skeleton className="mt-2 h-10 w-full" />
          ) : c.error !== null ? (
            <div className="mt-2"><ErrorAlert error={c.error} onRetry={() => void loadChain()} retrying={c.loading} compact /></div>
          ) : c.data ? (
            c.data.valid ? (
              <div className="mt-2 flex items-start gap-3">
                <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-success" aria-hidden />
                <div>
                  <p className="font-medium text-success-text">Chain intact</p>
                  <p className="text-sm text-muted">
                    {c.data.events_checked ? `${c.data.events_checked.toLocaleString("en-GB")} events checked` : "No events recorded yet"}
                  </p>
                </div>
              </div>
            ) : (
              <div className="mt-2 flex items-start gap-3">
                <ShieldX className="mt-0.5 h-5 w-5 shrink-0 text-danger" aria-hidden />
                <div>
                  <p className="font-medium text-danger-text">
                    Chain broken{c.data.first_bad_seq !== null ? ` at event #${c.data.first_bad_seq}` : ""}
                  </p>
                  {c.data.reason && <p className="text-sm text-slate-700">{c.data.reason}</p>}
                  <p className="text-sm text-muted">{c.data.events_checked.toLocaleString("en-GB")} events checked before the break</p>
                </div>
              </div>
            )
          ) : null}
        </section>
        <section aria-label="Recent audit records">
          <p className="text-sm font-medium text-ink">Recent records</p>
          {r.loading && !r.data ? (
            <div className="mt-2 space-y-2"><Skeleton className="h-4 w-full" /><Skeleton className="h-4 w-2/3" /></div>
          ) : r.error !== null ? (
            <div className="mt-2"><ErrorAlert error={r.error} onRetry={() => void loadRecent()} retrying={r.loading} compact /></div>
          ) : r.data && r.data.length > 0 ? (
            <ul className="mt-2 divide-y divide-slate-100" aria-label="Recent audit records list">
              {r.data.slice(0, 5).map((x) => (
                <li key={x.result_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-sm">
                  <AuditOutcomeBadge outcome={x.outcome} />
                  <span className="font-mono text-xs text-muted">{x.case_id}</span>
                  <span className="ml-auto text-xs text-muted">
                    {formatTimestamp(x.recorded_at)} · {x.feedback_count} feedback
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-muted">No audit records yet.</p>
          )}
        </section>
      </div>
    </Card>
  );
}
