// Dashboard for team leads (GET /api/tickets/stats + the existing audit routes). Data comes through the provisional
// adapter in lib/api.ts (contract: docs/DASHBOARD-CONTRACT.md). Only what the backend returns is shown: a missing
// figure says "Not available yet", never a made-up number. Assisted MTTR, time saved and improvement are always
// ESTIMATES with their assumptions (docs/DATA-CARD.md: never report them as observed).
//
// Chart rules (dataviz method): meter lists in one hue with values printed beside each bar, a single-series line
// for the trend with a table view, no pie or donut, no dual axis, measured and estimated MTTR never in one chart.
import { useCallback, useEffect, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { BarChart3, Clock, DatabaseZap, Info, RotateCw, TrendingDown } from "lucide-react";
import { AuditTrail } from "../components/dashboard/AuditTrail";
import { MeterList, SectionHeading, StatTile, type MeterItem } from "../components/dashboard/Figures";
import { TrendChart } from "../components/dashboard/TrendChart";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorAlert, PageHeader, Skeleton } from "../components/common/ui";
import { ApiError } from "../lib/api";
import { formatHours, formatPercent } from "../lib/format";
import type { CountRow, DashboardStats, Outcome } from "../lib/types";
import { useAppData, useLoader } from "../state/AppData";

const OUTCOME_LABEL: Record<Outcome, string> = {
  RECOMMEND: "Recommended a fix",
  CLARIFY: "Asked for more information",
  ESCALATE: "Escalated to a team",
  BLOCKED: "Blocked by the safety screen",
};
const UNAVAILABLE = new Set(["DB_NOT_READY", "DATABASE_UNAVAILABLE"]);
const TOP_SERVICES = 8;
const n = (v: number) => v.toLocaleString("en-GB");
const estimate = <Badge tone="warning">Estimate</Badge>;

function NotAvailable({ children }: { children: ReactNode }) {
  return (
    <Card as="div" className="flex items-start gap-3 p-5 text-sm text-muted">
      <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <p>{children}</p>
    </Card>
  );
}

/** Counts as meter rows: share of the largest so bars compare; the figure shows count and share of the total. */
function countItems(rows: CountRow[], total: number, keepOrder = false): MeterItem[] {
  const sorted = keepOrder ? rows : [...rows].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
  const max = Math.max(1, ...sorted.map((r) => r.count));
  return sorted.map((r) => ({ name: r.label, share: r.count / max,
                              figure: `${n(r.count)}${total ? ` · ${formatPercent(r.count / total)}` : ""}` }));
}

/** Top services by count; the rest folded into "Other services" (never more bars than can be read). */
function topServices(rows: CountRow[]): CountRow[] {
  const sorted = [...rows].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
  if (sorted.length <= TOP_SERVICES) return sorted;
  const rest = sorted.slice(TOP_SERVICES - 1).reduce((s, r) => s + r.count, 0);
  return [...sorted.slice(0, TOP_SERVICES - 1), { label: `Other services (${sorted.length - TOP_SERVICES + 1})`, count: rest }];
}

// ---------- sections ----------

function IncidentOverview({ stats }: { stats: DashboardStats }) {
  const i = stats.incidents;
  return (
    <section aria-labelledby="overview-heading">
      <SectionHeading id="overview-heading" title="Incident overview">Historical incidents, each counted once.</SectionHeading>
      {i ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <StatTile label="Total incidents" value={n(i.total)} detail="In the ticket history" />
          <StatTile label="Resolved" value={n(i.resolved)} detail="Resolved or closed" />
          <StatTile label="Pending" value={n(i.pending)} detail="Open or in progress" />
          <StatTile label="Critical" value={n(i.critical)} detail="Priority P1" />
          <StatTile label="Resolution rate" value={i.total ? formatPercent(i.resolved / i.total) : "—"}
                    detail={i.total ? `${n(i.resolved)} of ${n(i.total)} resolved` : "No incidents"} />
        </div>
      ) : (
        <NotAvailable>Incident figures are not available yet: the statistics service does not return them.</NotAvailable>
      )}
    </section>
  );
}

function Mttr({ stats }: { stats: DashboardStats }) {
  const base = stats.mttrBaseline;
  const est = stats.mttrAssisted;
  const saved = stats.timeSaved;
  return (
    <section aria-labelledby="mttr-heading">
      <SectionHeading id="mttr-heading" title="Time to resolve (MTTR)">
        Median time from a ticket being raised to it being resolved. The baseline is measured; anything with the
        assistant is an estimate.
      </SectionHeading>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile label="Median time to resolve" value={base ? formatHours(base.medianHours) : "—"}
                  detail={base ? `Measured, without the assistant · ${n(base.tickets)} tickets` : "Not available yet"} />
        <StatTile label="With the assistant" value={est ? formatHours(est.medianHours) : "—"}
                  detail={est ? "Estimated, not observed" : "Not available yet"} badge={est ? estimate : undefined} />
        <StatTile label="Time saved" value={saved ? formatHours(Math.abs(saved.hours)) : "—"}
                  detail={saved ? (saved.hours >= 0 ? "Estimated saving per ticket" : "Estimated extra time per ticket") : "Not available yet"}
                  badge={saved ? estimate : undefined} />
        <StatTile label="Improvement" value={saved && saved.percent !== null ? formatPercent(saved.percent) : "—"}
                  detail={saved && saved.percent !== null ? "Estimated, against the measured baseline" : "Not available yet"}
                  badge={saved && saved.percent !== null ? estimate : undefined} />
      </div>

      {base && (
        <div className="mt-4 grid gap-6 xl:grid-cols-2">
          <Card>
            <CardHeader title="By priority" icon={<Clock className="h-5 w-5" />} description="Measured median per priority." />
            {base.byPriority.length ? (
              <MeterList label="Median time to resolve by priority" items={(() => {
                const max = Math.max(...base.byPriority.map((p) => p.medianHours), 0.0001);
                return base.byPriority.map((p) => ({ name: p.priority, share: p.medianHours / max,
                                                    figure: `${formatHours(p.medianHours)} · ${n(p.tickets)} tickets` }));
              })()} />
            ) : <p className="p-5 text-sm text-muted">Not available yet.</p>}
          </Card>
          <Card>
            <CardHeader title="By service" icon={<Clock className="h-5 w-5" />} description="Measured median per service, slowest first." />
            {base.byService.length ? (
              <MeterList label="Median time to resolve by service" items={(() => {
                const rows = [...base.byService].sort((a, b) => b.medianHours - a.medianHours).slice(0, TOP_SERVICES);
                const max = Math.max(...rows.map((s) => s.medianHours), 0.0001);
                return rows.map((s) => ({ name: s.service, share: s.medianHours / max,
                                          figure: `${formatHours(s.medianHours)} · ${n(s.tickets)} tickets` }));
              })()} />
            ) : <p className="p-5 text-sm text-muted">Not available yet.</p>}
          </Card>
          <Card className="xl:col-span-2">
            <CardHeader title="Resolution trend" icon={<TrendingDown className="h-5 w-5" />}
                        description="Measured median time to resolve, by the month tickets were resolved." />
            {base.trend.length ? <TrendChart points={base.trend} /> : <p className="p-5 text-sm text-muted">Not available yet.</p>}
          </Card>
        </div>
      )}

      <Card className="mt-4">
        <div className="grid gap-4 p-5 md:grid-cols-2">
          <div>
            <p className="text-sm font-medium text-ink">How the baseline is measured</p>
            <p className="mt-1 text-sm text-muted">
              Resolved time minus created time for each historical ticket, counted once. Tickets with a missing,
              unreadable or negative duration are left out. The ticket data is synthetic (no business hours).
            </p>
          </div>
          <div>
            <p className="flex items-center gap-2 text-sm font-medium text-ink">Assumptions behind the estimate {est && estimate}</p>
            {est ? (
              <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-slate-700">{est.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
            ) : (
              <p className="mt-1 text-sm text-muted">Not available yet: the estimate is not connected.</p>
            )}
          </div>
        </div>
        <p className="border-t border-slate-100 px-5 py-3 text-xs text-muted">
          Estimated figures are projections from the stated assumptions and must not be reported as observed results.
        </p>
      </Card>
    </section>
  );
}

function Distribution({ stats }: { stats: DashboardStats }) {
  const total = stats.incidents?.total ?? 0;
  const priority = ["P1", "P2", "P3", "P4"]
    .map((p) => stats.byPriority.find((r) => r.label === p))
    .filter((r): r is CountRow => !!r)
    .concat(stats.byPriority.filter((r) => !["P1", "P2", "P3", "P4"].includes(r.label)));
  const cards: [string, string, ReactNode][] = [
    ["By priority", "Incidents per priority", priority.length
      ? <MeterList label="Incidents by priority" items={countItems(priority, total, true)} /> : null],
    ["By service", `Incidents per affected service${stats.byService.length > TOP_SERVICES ? `, top ${TOP_SERVICES - 1}` : ""}`,
      stats.byService.length ? <MeterList label="Incidents by service" items={countItems(topServices(stats.byService), total, true)} /> : null],
    ["By category", "Incidents per category", stats.byCategory.length
      ? <MeterList label="Incidents by category" items={countItems(stats.byCategory, total)} /> : null],
  ];
  return (
    <section aria-labelledby="distribution-heading">
      <SectionHeading id="distribution-heading" title="Incident distribution" />
      <div className="grid gap-6 xl:grid-cols-3">
        {cards.map(([title, description, body]) => (
          <Card key={title}>
            <CardHeader title={title} icon={<BarChart3 className="h-5 w-5" />} description={description} />
            {body ?? <p className="p-5 text-sm text-muted">Not available yet.</p>}
          </Card>
        ))}
      </div>
    </section>
  );
}

function AssistantActivity({ stats }: { stats: DashboardStats }) {
  const rated = stats.categories.reduce((s, c) => s + c.rated, 0);
  const useful = stats.categories.reduce((s, c) => s + c.useful, 0);
  const categories = [...stats.categories].sort((a, b) => (b.usefulRate ?? -1) - (a.usefulRate ?? -1) || a.category.localeCompare(b.category));
  return (
    <section aria-labelledby="assistant-heading">
      <SectionHeading id="assistant-heading" title="Assistant activity">Saved AI analyses and how analysts rated them.</SectionHeading>
      <div className="grid gap-4 sm:grid-cols-2">
        <StatTile label="Analyses" value={n(stats.analyses)} detail="Saved by the assistant" />
        <StatTile label="Rated useful" value={rated ? formatPercent(useful / rated) : "—"}
                  detail={rated ? `${n(useful)} of ${n(rated)} analyst ratings` : "No feedback yet"} />
      </div>
      <div className="mt-4 grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader title="AI outcome mix" icon={<BarChart3 className="h-5 w-5" />}
                      description={`How the assistant answered ${n(stats.analyses)} saved analyses.`} />
          <MeterList label="Outcome mix" items={stats.outcomes.map(({ outcome, count }) => ({
            name: OUTCOME_LABEL[outcome], share: stats.analyses ? count / stats.analyses : null,
            figure: `${n(count)} · ${stats.analyses ? formatPercent(count / stats.analyses) : "—"}`,
          }))} />
        </Card>
        <Card>
          <CardHeader title="Rated useful, by category" icon={<BarChart3 className="h-5 w-5" />}
                      description="Share of analyst ratings that were Useful (the latest rating per analysis counts)." />
          {categories.length ? (
            <MeterList label="Rated useful by category" items={categories.map((c) => ({
              name: c.category, share: c.usefulRate,
              figure: c.usefulRate === null ? "No feedback yet" : `${formatPercent(c.usefulRate)} · ${c.useful} of ${c.rated}`,
            }))} />
          ) : <p className="p-5 text-sm text-muted">No analyst feedback has been given yet.</p>}
        </Card>
      </div>
    </section>
  );
}

function Loading() {
  return (
    <div role="status" aria-live="polite" className="space-y-6">
      <span className="sr-only">Loading the dashboard…</span>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        {Array.from({ length: 5 }, (_, i) => <Card key={i} as="div" className="space-y-3 p-5"><Skeleton className="h-4 w-24" /><Skeleton className="h-8 w-20" /></Card>)}
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
  } else if (data && data.analyses === 0 && !data.incidents && !data.mttrBaseline) {
    body = (
      <Card as="div">
        <EmptyState title="No analyses yet" icon={<BarChart3 className="h-5 w-5" />}
                    action={<Link to="/analyst/analyze" className="rounded-lg bg-primary px-3.5 py-2 text-sm font-medium text-white hover:bg-primary-hover">Analyze a ticket</Link>}>
          The dashboard fills in once tickets have been analysed and saved.
        </EmptyState>
      </Card>
    );
  } else if (data) {
    body = (
      <div className={`space-y-8 ${loading ? "opacity-60" : ""}`}>
        <IncidentOverview stats={data} />
        <Mttr stats={data} />
        <Distribution stats={data} />
        <AssistantActivity stats={data} />
      </div>
    );
  }

  return (
    <>
      <PageHeader title="Dashboard"
                  description="Incidents, time to resolve and assistant activity across the whole ticket history. For team leads." />
      <p className="-mt-3 mb-6 flex items-center gap-2 text-xs text-muted">
        <Info className="h-3.5 w-3.5" aria-hidden />
        Covers all data. Date, service and priority filters will appear once the statistics service supports them.
      </p>
      <div className="space-y-8">
        {body}
        <AuditTrail />
      </div>
    </>
  );
}
