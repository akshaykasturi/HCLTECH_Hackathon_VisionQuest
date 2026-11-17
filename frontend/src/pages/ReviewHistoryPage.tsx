// Review History: saved analyses with their feedback and resolution (GET /api/tickets/history).
// db/repo.history() supplies it. If the database is unavailable (DB_NOT_READY / DATABASE_UNAVAILABLE) the page says so.
// The page uses HistoryEntry only; the backend record and its adapter live in lib/api.ts.
// Nothing here is sample data.
import { useCallback, useEffect, useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  ArrowLeft, CheckCircle2, ChevronLeft, ChevronRight, Clock, DatabaseZap, FileText, History, Library, MessageSquare, RotateCw,
} from "lucide-react";
import { ClassificationCard, FeedbackCard, OutcomeBanner, StepsCard } from "../components/analyze/AnalysisView";
import { Button, Card, CardHeader, EmptyState, ErrorAlert, Mono, PageHeader, Skeleton } from "../components/common/ui";
import { OutcomeBadge, VerdictBadge } from "../components/history/HistoryBadges";
import { latestFeedback } from "../components/history/feedback";
import { ApiError } from "../lib/api";
import { formatDate, formatDuration, formatTimestamp, humanize } from "../lib/format";
import type { HistoryEntry } from "../lib/types";
import { useAppData, useLoader } from "../state/AppData";

const PAGE_SIZE = 20;
const TICKET_ID = /^INC-\d+$/i;
const UNAVAILABLE = new Set(["DB_NOT_READY", "DATABASE_UNAVAILABLE"]);

function isUnavailable(error: unknown) {
  return error instanceof ApiError && UNAVAILABLE.has(error.code);
}

function shortId(id: string) {
  return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}

function TableSkeleton() {
  return (
    <div role="status" aria-live="polite" className="divide-y divide-slate-100">
      <span className="sr-only">Loading review history…</span>
      {Array.from({ length: 5 }, (_, i) => (
        <div key={i} className="flex items-center gap-4 px-5 py-4">
          <Skeleton className="h-4 w-20" /><Skeleton className="h-4 flex-1" /><Skeleton className="h-5 w-20 rounded-full" />
        </div>
      ))}
    </div>
  );
}

function Unavailable({ onRetry, retrying }: { onRetry: () => void; retrying: boolean }) {
  return (
    <Card as="div">
      <EmptyState title="Review history unavailable" icon={<DatabaseZap className="h-5 w-5" />}
                  action={<Button onClick={onRetry} loading={retrying} icon={<RotateCw className="h-4 w-4" aria-hidden />}>Try again</Button>}>
        Your review history is temporarily unavailable. Please try again later.
      </EmptyState>
    </Card>
  );
}

// ---------- detail ----------

function EvidenceCard({ ids }: { ids: string[] }) {
  return (
    <Card>
      <CardHeader title="Evidence used" icon={<Library className="h-5 w-5" />}
                  description="The full text of similar tickets and knowledge-base articles is not stored with the result; these are the sources the analysis used." />
      {ids.length === 0 ? (
        <p className="p-5 text-sm text-muted">No evidence references were stored for this analysis.</p>
      ) : (
        <ul className="flex flex-wrap gap-2 p-5">
          {ids.map((id) => (
            <li key={id}>
              {TICKET_ID.test(id) ? (
                <Link to={`/analyst/tickets/${encodeURIComponent(id)}`}
                      className="inline-flex rounded-lg border border-slate-200 px-2.5 py-1 font-mono text-xs text-primary hover:bg-primary-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
                  {id}
                </Link>
              ) : (
                <span className="inline-flex rounded-lg border border-slate-200 px-2.5 py-1"><Mono className="text-xs">{id}</Mono></span>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function FeedbackHistoryCard({ item }: { item: HistoryEntry }) {
  const rows = [...item.feedback].sort((a, b) => (b.at ?? "").localeCompare(a.at ?? ""));
  return (
    <Card>
      <CardHeader title="Feedback history" icon={<MessageSquare className="h-5 w-5" />}
                  description="Feedback is never edited: a changed opinion is a new entry, and the newest one counts." />
      {rows.length === 0 ? (
        <p className="p-5 text-sm text-muted">No feedback has been given on this analysis yet.</p>
      ) : (
        <ol className="divide-y divide-slate-100" aria-label="Feedback entries">
          {rows.map((f, i) => (
            <li key={`${f.at ?? "none"}-${i}`} className="px-5 py-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <VerdictBadge verdict={f.verdict} />
                {i === 0 && rows.length > 1 && <span className="text-xs font-medium text-primary">Current</span>}
                <span className="text-muted">{f.at ? formatTimestamp(f.at) : "Time not recorded"}</span>
                <span className="text-muted">· {f.reviewer ?? "Reviewer not recorded"}</span>
              </div>
              {f.aboutTicketId && (
                <p className="mt-1 text-xs text-muted">About similar ticket <Mono className="text-xs">{f.aboutTicketId}</Mono></p>
              )}
              {f.note && <p className="mt-1 text-slate-700">{f.note}</p>}
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function ResolutionCard({ item }: { item: HistoryEntry }) {
  const r = item.resolution;
  const duration = r ? formatDuration(item.analysedAt, r.resolvedAt) : null;
  return (
    <Card>
      <CardHeader title="Resolution" icon={<CheckCircle2 className="h-5 w-5" />} />
      <div className="p-5 text-sm">
        {r ? (
          <dl className="grid gap-y-2 sm:grid-cols-3">
            <dt className="text-muted">Status</dt><dd className="sm:col-span-2"><span className="font-medium text-success-text">Resolved</span></dd>
            <dt className="text-muted">Resolved at</dt><dd className="sm:col-span-2">{formatTimestamp(r.resolvedAt)}</dd>
            <dt className="text-muted">Resolved by</dt><dd className="sm:col-span-2">{r.resolvedBy ?? "Not recorded"}</dd>
            {duration && <><dt className="text-muted">Time from analysis</dt><dd className="sm:col-span-2">{duration}</dd></>}
          </dl>
        ) : (
          <p className="text-muted">This analysis has not been marked as resolved.</p>
        )}
      </div>
    </Card>
  );
}

function HistoryDetail({ item, backTo, onFeedbackSaved }: { item: HistoryEntry; backTo: string; onFeedbackSaved: () => void }) {
  const t = item.ticket;
  return (
    <>
      <Link to={backTo} className="mb-4 inline-flex items-center gap-1.5 rounded text-sm font-medium text-primary hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
        <ArrowLeft className="h-4 w-4" aria-hidden /> Back to history
      </Link>
      <header className="mb-6">
        <p className="font-mono text-sm text-muted">Result {item.id}</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-ink">{t.subject || "Untitled ticket"}</h1>
        <p className="mt-1 flex flex-wrap items-center gap-x-3 text-sm text-muted">
          <span className="inline-flex items-center gap-1"><Clock className="h-4 w-4" aria-hidden />Analysed {formatTimestamp(item.analysedAt)}</span>
          {item.analyst && <span>by {item.analyst}</span>}
        </p>
      </header>
      <div className="grid gap-6 lg:grid-cols-12">
        <div className="space-y-5 lg:col-span-7">
          <OutcomeBanner result={item} />
          <Card>
            <CardHeader title="Original ticket" icon={<FileText className="h-5 w-5" />} description="Stored masked: personal data and secrets were removed before saving." />
            <div className="space-y-3 p-5 text-sm">
              <p className="whitespace-pre-line text-slate-700">{t.description || "No description was stored."}</p>
              <p className="text-muted">
                {[t.service, t.environment && humanize(t.environment), t.channel && humanize(t.channel)].filter(Boolean).join(" · ") || "No service details were stored."}
              </p>
            </div>
          </Card>
          <ClassificationCard classification={item.classification} emptyMessage="No classification was stored with this analysis." />
          {item.recommendation && <StepsCard recommendation={item.recommendation} findings={item.findings} />}
          <EvidenceCard ids={item.evidence} />
        </div>
        <div className="space-y-5 lg:col-span-5">
          <ResolutionCard item={item} />
          <FeedbackHistoryCard item={item} />
          <FeedbackCard key={item.id} resultId={item.id} onSaved={onFeedbackSaved} />
          {(item.generator || item.classifierVersion || item.promptVersion) && (
            <details className="rounded-xl border border-slate-200 bg-white px-5 py-3 text-sm shadow-card">
              <summary className="cursor-pointer font-medium text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">Technical details</summary>
              <dl className="mt-2 grid grid-cols-2 gap-y-1 text-slate-700">
                {item.generator && <><dt className="text-muted">Steps written by</dt><dd>{item.generator}</dd></>}
                {item.classifierVersion && <><dt className="text-muted">Classifier</dt><dd>{item.classifierVersion}</dd></>}
                {item.promptVersion && <><dt className="text-muted">Prompt version</dt><dd>{item.promptVersion}</dd></>}
                {item.finishedAt && <><dt className="text-muted">Finished</dt><dd>{formatTimestamp(item.finishedAt)}</dd></>}
              </dl>
            </details>
          )}
        </div>
      </div>
    </>
  );
}

// ---------- page ----------

export function ReviewHistoryPage() {
  const { api } = useAppData();
  const [params, setParams] = useSearchParams();
  const offset = Math.max(0, Number.parseInt(params.get("offset") ?? "0", 10) || 0);
  const selected = params.get("result");

  const loader = useLoader(useCallback((s: AbortSignal) => api.history(PAGE_SIZE, offset, s), [api, offset]));
  const { load } = loader;
  useEffect(() => { void load(); }, [load]); // once per page; never retried automatically
  const { data, error, loading } = loader.state;

  const items = useMemo(() => data ?? [], [data]);
  const item = selected ? items.find((x) => x.id === selected) ?? null : null;
  const listUrl = offset ? `?offset=${offset}` : "?";
  const goTo = (nextOffset: number) => setParams(nextOffset ? { offset: String(nextOffset) } : {});
  const open = (resultId: string) => setParams({ ...(offset ? { offset: String(offset) } : {}), result: resultId });

  if (selected && item) {
    return <HistoryDetail item={item} backTo={listUrl} onFeedbackSaved={() => void load()} />;
  }

  return (
    <>
      <PageHeader title="Review History" description="Review previous AI analyses, feedback and incident resolution activity." />

      {selected && !loading && data && (
        <div className="mb-4 rounded-lg bg-warning-soft px-4 py-3 text-sm text-warning-text" role="status">
          Result {selected} is not on this page of the history.
        </div>
      )}

      {loading && !data ? (
        <Card as="div"><TableSkeleton /></Card>
      ) : isUnavailable(error) ? (
        <Unavailable onRetry={() => void load()} retrying={loading} />
      ) : error !== null ? (
        <ErrorAlert error={error} onRetry={() => void load()} retrying={loading} />
      ) : items.length === 0 ? (
        <Card as="div">
          <EmptyState title={offset ? "No more analyses" : "No analyses yet"} icon={<History className="h-5 w-5" />}
                      action={offset ? <Button onClick={() => goTo(0)}>Back to the first page</Button>
                                     : <Link to="/analyst/analyze" className="rounded-lg bg-primary px-3.5 py-2 text-sm font-medium text-white hover:bg-primary-hover">Analyze a ticket</Link>}>
            {offset ? "There are no older analyses." : "Analyses appear here after a ticket has been analysed and saved."}
          </EmptyState>
        </Card>
      ) : (
        <Card as="div" aria-labelledby="history-heading">
          <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-slate-100 px-5 py-3">
            <h2 id="history-heading" className="text-base font-semibold text-ink">Analyses</h2>
            <p className="text-sm text-muted">Showing {offset + 1}–{offset + items.length}, newest first</p>
          </div>
          <div className={loading ? "opacity-60" : ""} aria-busy={loading || undefined}>
            <table className="hidden w-full text-left text-sm md:table">
              <thead className="bg-page text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th scope="col" className="px-5 py-3 font-medium">Result</th>
                  <th scope="col" className="px-3 py-3 font-medium">Ticket</th>
                  <th scope="col" className="px-3 py-3 font-medium">Category</th>
                  <th scope="col" className="px-3 py-3 font-medium">Priority</th>
                  <th scope="col" className="px-3 py-3 font-medium">Outcome</th>
                  <th scope="col" className="px-3 py-3 font-medium">Feedback</th>
                  <th scope="col" className="px-3 py-3 font-medium">Resolution</th>
                  <th scope="col" className="px-5 py-3 font-medium">Analysed</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {items.map((x) => {
                  const fb = latestFeedback(x.feedback);
                  return (
                    <tr key={x.id} onClick={() => open(x.id)} className="cursor-pointer hover:bg-slate-50">
                      <td className="whitespace-nowrap px-5 py-3">
                        <button type="button" onClick={(e) => { e.stopPropagation(); open(x.id); }}
                                aria-label={`Open result ${x.id}`} title={x.id}
                                className="font-mono text-[0.8125rem] font-medium text-primary hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
                          {shortId(x.id)}
                        </button>
                      </td>
                      <td className="max-w-xs px-3 py-3">
                        <p className="truncate font-medium text-ink" title={x.ticket.subject}>{x.ticket.subject || "—"}</p>
                        {x.ticket.service && <p className="text-xs text-muted">{x.ticket.service}</p>}
                      </td>
                      <td className="px-3 py-3 text-slate-700">{x.classification?.category.label ?? <span className="text-muted">Not classified</span>}</td>
                      <td className="px-3 py-3 text-slate-700">{x.classification?.priority.label ?? <span className="text-muted">—</span>}</td>
                      <td className="whitespace-nowrap px-3 py-3"><OutcomeBadge outcome={x.outcome} /></td>
                      <td className="whitespace-nowrap px-3 py-3">
                        {fb ? <span className="inline-flex items-center gap-1.5"><VerdictBadge verdict={fb.verdict} />
                                {x.feedback.length > 1 && <span className="text-xs text-muted">+{x.feedback.length - 1}</span>}</span>
                            : <span className="text-muted">None yet</span>}
                      </td>
                      <td className="whitespace-nowrap px-3 py-3">
                        {x.resolution ? <span className="text-success-text">Resolved</span> : <span className="text-muted">Open</span>}
                      </td>
                      <td className="whitespace-nowrap px-5 py-3 text-slate-700">{formatDate(x.analysedAt)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            <ul className="divide-y divide-slate-100 md:hidden" aria-label="Analyses">
              {items.map((x) => {
                const fb = latestFeedback(x.feedback);
                return (
                  <li key={x.id}>
                    <button type="button" onClick={() => open(x.id)}
                            className="block w-full px-5 py-4 text-left hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary">
                      <span className="flex items-center justify-between gap-2 text-xs text-muted">
                        <span className="font-mono">{shortId(x.id)}</span><span>{formatDate(x.analysedAt)}</span>
                      </span>
                      <span className="mt-1 block font-medium text-ink">{x.ticket.subject || "—"}</span>
                      <span className="mt-2 flex flex-wrap gap-2">
                        <OutcomeBadge outcome={x.outcome} />
                        {fb && <VerdictBadge verdict={fb.verdict} />}
                        {x.resolution && <span className="text-xs text-success-text">Resolved</span>}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
          <nav aria-label="History pages" className="flex items-center justify-between gap-2 border-t border-slate-100 px-5 py-3">
            <Button onClick={() => goTo(Math.max(0, offset - PAGE_SIZE))} disabled={offset === 0 || loading}
                    icon={<ChevronLeft className="h-4 w-4" aria-hidden />}>Newer</Button>
            <span className="text-sm text-muted">Page {Math.floor(offset / PAGE_SIZE) + 1}</span>
            {/* No total from the backend: a full page means there may be more. */}
            <Button onClick={() => goTo(offset + PAGE_SIZE)} disabled={items.length < PAGE_SIZE || loading}>
              Older <ChevronRight className="h-4 w-4" aria-hidden />
            </Button>
          </nav>
        </Card>
      )}
    </>
  );
}
