// Dashboard for team leads (GET /api/tickets/stats). Shows only what the backend returns: a missing figure
// says "not available", never a made-up number. The assisted MTTR is always an ESTIMATE with its assumptions
// (docs/DATA-CARD.md: never report it as observed). The data comes through the provisional adapter in lib/api.ts.
//
// Chart rules (dataviz method): meter lists in ONE hue (outcomes and categories are names, not a ranking of
// colour), the unfilled track a lighter step of the same blue, values printed beside every bar (no hover needed
// to read anything), no pie/donut, no dual axis; the two MTTR figures are separate tiles, never one chart.
import { useCallback, useEffect, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { BarChart3, Clock, DatabaseZap, RotateCw, ScrollText, ShieldCheck, ShieldX } from "lucide-react";
import { OutcomeBadge as AuditOutcomeBadge } from "../components/common/status";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorAlert, PageHeader, Skeleton } from "../components/common/ui";
import { ApiError } from "../lib/api";
import { formatHours, formatPercent, formatTimestamp } from "../lib/format";
import type { CategoryFeedback, DashboardStats, Outcome } from "../lib/types";
import { useAppData, useLoader } from "../state/AppData";

const OUTCOME_LABEL: Record<Outcome, string> = {
  RECOMMEND: "Recommended a fix",
  CLARIFY: "Asked for more information",
  ESCALATE: "Escalated to a team",
  BLOCKED: "Blocked by the safety screen",
};
const UNAVAILABLE = new Set(["DB_NOT_READY", "DATABASE_UNAVAILABLE"]);

function StatTile({ label, value, detail, badge }: { label: string; value: string; detail: ReactNode; badge?: ReactNode }) {
  return (
    <Card as="article" aria-label={label} className="p-5">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-muted">{label}</p>
        {badge}
      </div>
      <p className="mt-2 text-3xl font-semibold tracking-tight text-ink">{value}</p>
      <p className="mt-1 text-sm text-muted">{detail}</p>
    </Card>
  );
}

/** One row of a meter list: name, a single-hue bar (share 0..1) and the figure printed beside it. */
function MeterRow({ name, share, figure }: { name: string; share: number | null; figure: string }) {
  return (
    <li className="grid gap-x-4 gap-y-1.5 px-5 py-3 sm:grid-cols-[12rem_1fr_9rem] sm:items-center">
      <span className="text-sm font-medium text-ink">{name}</span>
      <span aria-hidden className="h-2.5 overflow-hidden rounded-full bg-blue-100" title={`${name}: ${figure}`}>
        {share !== null && share > 0 && (
          <span className="block h-full rounded-full bg-primary" style={{ width: `${Math.max(share * 100, 1.5)}%` }} />
        )}
      </span>
      <span className="text-sm text-slate-700 sm:text-right" style={{ fontVariantNumeric: "tabular-nums" }}>{figure}</span>
    </li>
  );
}

function OutcomeMix({ stats }: { stats: DashboardStats }) {
  return (
    <Card>
      <CardHeader title="Outcome mix" icon={<BarChart3 className="h-5 w-5" />}
                  description={`How the assistant answered ${stats.analyses.toLocaleString("en-GB")} saved analyses.`} />
      <ul aria-label="Outcome mix" className="divide-y divide-slate-100">
        {stats.outcomes.map(({ outcome, count }) => (
          <MeterRow key={outcome} name={OUTCOME_LABEL[outcome]} share={stats.analyses ? count / stats.analyses : null}
                    figure={`${count.toLocaleString("en-GB")} · ${stats.analyses ? formatPercent(count / stats.analyses) : "—"}`} />
        ))}
      </ul>
    </Card>
  );
}

function UsefulByCategory({ categories }: { categories: CategoryFeedback[] }) {
  // Largest first so the rows compare at a glance; categories nobody rated yet go last.
  const rows = [...categories].sort((a, b) => (b.usefulRate ?? -1) - (a.usefulRate ?? -1) || a.category.localeCompare(b.category));
  return (
    <Card>
      <CardHeader title="Rated useful, by category" icon={<BarChart3 className="h-5 w-5" />}
                  description="Share of analyst ratings that were Useful (the latest rating per analysis counts)." />
      {rows.length === 0 ? (
        <p className="p-5 text-sm text-muted">No analyst feedback has been given yet.</p>
      ) : (
        <ul aria-label="Rated useful by category" className="divide-y divide-slate-100">
          {rows.map((c) => (
            <MeterRow key={c.category} name={c.category} share={c.usefulRate}
                      figure={c.usefulRate === null ? "No feedback yet" : `${formatPercent(c.usefulRate)} · ${c.useful} of ${c.rated}`} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function ResolutionTime({ stats }: { stats: DashboardStats }) {
  const base = stats.mttrBaseline;
  const est = stats.mttrAssisted;
  return (
    <Card>
      <CardHeader title="Time to resolve" icon={<Clock className="h-5 w-5" />}
                  description="Median time from a ticket being raised to it being resolved." />
      <div className="grid gap-4 p-5 md:grid-cols-2">
        <section aria-label="Measured baseline" className="rounded-lg bg-page p-4">
          <p className="text-sm font-medium text-ink">Without the assistant <span className="font-normal text-muted">(measured)</span></p>
          {base ? (
            <>
              <p className="mt-1 text-2xl font-semibold text-ink">{formatHours(base.medianHours)}</p>
              <p className="text-sm text-muted">
                Median of {base.tickets.toLocaleString("en-GB")} resolved historical tickets
                {base.meanHours !== null && <> · mean {formatHours(base.meanHours)}</>}
              </p>
              {base.byPriority.length > 0 && (
                <table className="mt-3 w-full text-sm" style={{ fontVariantNumeric: "tabular-nums" }}>
                  <caption className="sr-only">Median time to resolve by priority</caption>
                  <thead className="text-left text-xs uppercase tracking-wide text-muted">
                    <tr><th scope="col" className="py-1 font-medium">Priority</th><th scope="col" className="py-1 text-right font-medium">Median</th><th scope="col" className="py-1 text-right font-medium">Tickets</th></tr>
                  </thead>
                  <tbody className="divide-y divide-slate-200 text-slate-700">
                    {base.byPriority.map((p) => (
                      <tr key={p.priority}>
                        <th scope="row" className="py-1 text-left font-medium text-ink">{p.priority}</th>
                        <td className="py-1 text-right">{formatHours(p.medianHours)}</td>
                        <td className="py-1 text-right">{p.tickets.toLocaleString("en-GB")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          ) : (
            <p className="mt-1 text-sm text-muted">Not available yet: the baseline calculation is not connected.</p>
          )}
        </section>
        <section aria-label="Estimated with the assistant" className="rounded-lg border border-dashed border-slate-300 p-4">
          <p className="flex items-center gap-2 text-sm font-medium text-ink">With the assistant <Badge tone="warning">Estimate</Badge></p>
          {est ? (
            <>
              <p className="mt-1 text-2xl font-semibold text-ink">{formatHours(est.medianHours)}</p>
              <p className="text-sm text-muted">Estimated, not observed. Based on these assumptions:</p>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-700">
                {est.assumptions.map((a) => <li key={a}>{a}</li>)}
              </ul>
            </>
          ) : (
            <p className="mt-1 text-sm text-muted">Not available yet: the estimate is not connected.</p>
          )}
        </section>
      </div>
      <p className="border-t border-slate-100 px-5 py-3 text-xs text-muted">
        The baseline comes from historical ticket timestamps (synthetic demo data, no business hours). The assisted
        figure is a projection from the stated assumptions and must not be reported as an observed result.
      </p>
    </Card>
  );
}

/** Audit trail: GET /api/audit/verify and /api/audit/results (both live today, analyst or higher). Loads on its
 *  own, so it works even while the stats are unavailable; a failure here only affects this card. */
function AuditTrail() {
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

function Loading() {
  return (
    <div role="status" aria-live="polite" className="space-y-6">
      <span className="sr-only">Loading the dashboard…</span>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => <Card key={i} as="div" className="space-y-3 p-5"><Skeleton className="h-4 w-24" /><Skeleton className="h-8 w-20" /></Card>)}
      </div>
      <Card as="div" className="space-y-3 p-5">{Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-3 w-full" />)}</Card>
    </div>
  );
}

export function DashboardPage() {
  const { api } = useAppData();
  const loader = useLoader(useCallback((s: AbortSignal) => api.stats(s), [api]));
  const { load } = loader;
  useEffect(() => { void load(); }, [load]); // once; never retried automatically
  const { data, error, loading } = loader.state;

  let body: ReactNode;
  if (loading && !data) {
    body = <Loading />;
  } else if (error instanceof ApiError && UNAVAILABLE.has(error.code)) {
    body = (
      <Card as="div">
        <EmptyState title="Dashboard unavailable" icon={<DatabaseZap className="h-5 w-5" />}
                    action={<Button onClick={() => void load()} loading={loading} icon={<RotateCw className="h-4 w-4" aria-hidden />}>Try again</Button>}>
          Dashboard data is temporarily unavailable. Please try again later.
        </EmptyState>
      </Card>
    );
  } else if (error !== null) {
    body = <ErrorAlert error={error} onRetry={() => void load()} retrying={loading} />;
  } else if (data && data.analyses === 0) {
    body = (
      <Card as="div">
        <EmptyState title="No analyses yet" icon={<BarChart3 className="h-5 w-5" />}
                    action={<Link to="/analyst/analyze" className="rounded-lg bg-primary px-3.5 py-2 text-sm font-medium text-white hover:bg-primary-hover">Analyze a ticket</Link>}>
          The dashboard fills in once tickets have been analysed and saved.
        </EmptyState>
      </Card>
    );
  } else if (data) {
    const rated = data.categories.reduce((n, c) => n + c.rated, 0);
    const useful = data.categories.reduce((n, c) => n + c.useful, 0);
    body = (
      <div className={`space-y-6 ${loading ? "opacity-60" : ""}`}>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatTile label="Analyses" value={data.analyses.toLocaleString("en-GB")} detail="Saved by the assistant" />
          <StatTile label="Rated useful" value={rated ? formatPercent(useful / rated) : "—"}
                    detail={rated ? `${useful.toLocaleString("en-GB")} of ${rated.toLocaleString("en-GB")} analyst ratings` : "No feedback yet"} />
          <StatTile label="Median time to resolve" value={data.mttrBaseline ? formatHours(data.mttrBaseline.medianHours) : "—"}
                    detail={data.mttrBaseline ? "Measured, without the assistant" : "Not available yet"} />
          <StatTile label="With the assistant" value={data.mttrAssisted ? formatHours(data.mttrAssisted.medianHours) : "—"}
                    detail={data.mttrAssisted ? "Estimated, not observed" : "Not available yet"}
                    badge={data.mttrAssisted ? <Badge tone="warning">Estimate</Badge> : undefined} />
        </div>
        <div className="grid gap-6 xl:grid-cols-2">
          <OutcomeMix stats={data} />
          <UsefulByCategory categories={data.categories} />
        </div>
        <ResolutionTime stats={data} />
      </div>
    );
  }

  return (
    <>
      <PageHeader title="Dashboard"
                  description="Assistant outcomes, analyst feedback and time to resolve across saved analyses. For team leads." />
      <div className="space-y-6">
        {body}
        <AuditTrail />
      </div>
    </>
  );
}
