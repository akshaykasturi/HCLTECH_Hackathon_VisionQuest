// Audit History: what the permanent database holds (Neon Postgres in production).
//   list   GET /api/tickets/history (db/repo.history): every saved analysis, newest first, with its feedback rows
//          and resolution. Masked data only.
//   chain  GET /api/tickets/audit/verify (db/repo.verify_chain, role lead): recomputes every audit event's hash and
//          every stored row's fingerprint, so an edited result, feedback or resolution row is found.
// The older /api/audit/* routes are the legacy local audit store (docs/ARCHITECTURE.md) and are not used here.
import { useCallback, useEffect, useRef, useState } from "react";
import { Fingerprint, ListOrdered, RefreshCw, ShieldCheck, ShieldX } from "lucide-react";
import { formatClock, formatConfidence, formatTimestamp, valueToText } from "../lib/format";
import type { DbChainStatus, HistoryEntry } from "../lib/types";
import { useAppData, useLoader } from "../state/AppData";
import { OutcomeBadge } from "../components/common/status";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorAlert, LoadingBlock, Mono, PageHeader } from "../components/common/ui";

const PAGE_SIZE = 50;

function ChainCard() {
  const { api } = useAppData();
  const [result, setResult] = useState<DbChainStatus | null>(null);
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const busy = useRef(false);

  async function verify() {
    if (busy.current) return;
    busy.current = true;
    setLoading(true);
    setError(null);
    try {
      setResult(await api.verifyChain());
      setCheckedAt(new Date());
    } catch (e) {
      setError(e);
      setResult(null);
    } finally {
      busy.current = false;
      setLoading(false);
    }
  }

  return (
    <Card aria-labelledby="chain-title">
      <CardHeader id="chain-title" icon={<Fingerprint className="h-5 w-5" />} title="Audit chain verification"
                  description="Recalculates every SHA-256 hash in the database's audit chain and checks each saved row is unchanged (lead role)."
                  actions={<Button variant="primary" onClick={() => void verify()} loading={loading} loadingText="Verifying…"
                                   icon={<ShieldCheck className="h-4 w-4" aria-hidden />}>Verify chain</Button>} />
      <div className="p-5" aria-live="polite">
        {error ? <ErrorAlert error={error} onRetry={() => void verify()} retrying={loading} /> : !result ? (
          <p className="text-sm text-muted">Not checked yet. Press Verify chain.</p>
        ) : result.ok ? (
          <div className="flex items-start gap-3 rounded-lg border border-success/40 bg-success-soft p-4">
            <ShieldCheck className="mt-0.5 h-5 w-5 text-success-text" aria-hidden />
            <div className="text-sm">
              <p className="font-semibold text-success-text">Chain verified</p>
              <p className="mt-0.5 text-slate-700">{result.n_events} events checked, no gaps or edits found.</p>
              {checkedAt && <p className="mt-1 text-xs text-muted">Checked at {formatClock(checkedAt)}</p>}
            </div>
          </div>
        ) : (
          <div role="alert" className="flex items-start gap-3 rounded-lg border border-danger/40 bg-danger-soft p-4">
            <ShieldX className="mt-0.5 h-5 w-5 text-danger-text" aria-hidden />
            <div className="text-sm">
              <p className="font-semibold text-danger-text">Chain check failed</p>
              <p className="mt-0.5 text-slate-700">
                {result.first_broken_seq !== null
                  ? `Event #${result.first_broken_seq} or the row it recorded was changed after it was written.`
                  : "The chain could not be verified."}
              </p>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

function latestVerdict(entry: HistoryEntry): string | null {
  const own = entry.feedback.filter((f) => f.aboutTicketId === null);
  return own.length ? own[own.length - 1].verdict : null;
}

function ResultRow({ entry, open, onToggle }: { entry: HistoryEntry; open: boolean; onToggle: () => void }) {
  const verdict = latestVerdict(entry);
  return (
    <li className="px-5 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={onToggle} aria-expanded={open}
                className="rounded font-mono font-medium text-primary hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
          {entry.id}
        </button>
        <OutcomeBadge outcome={entry.outcome} />
        <span className="min-w-0 flex-1 truncate text-slate-700">{entry.ticket.subject}</span>
        {verdict && <Badge>{verdict}</Badge>}
        {entry.resolution && <Badge tone="success">resolved</Badge>}
        <span className="text-xs text-muted">
          {formatTimestamp(entry.analysedAt)} · {entry.feedback.length} feedback
        </span>
      </div>
      {open && (
        <div className="mt-3 rounded-lg border border-slate-200 p-4">
          <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
            <div><dt className="inline text-muted">Analyst: </dt><dd className="inline">{entry.analyst ?? "-"}</dd></div>
            <div><dt className="inline text-muted">Confidence: </dt><dd className="inline">{formatConfidence(entry.confidence)}</dd></div>
            <div><dt className="inline text-muted">Reasons: </dt><dd className="inline">{entry.reasons.join(", ") || "-"}</dd></div>
            <div><dt className="inline text-muted">Evidence: </dt><dd className="inline"><Mono>{entry.evidence.join(", ") || "-"}</Mono></dd></div>
            <div><dt className="inline text-muted">Resolved: </dt><dd className="inline">
              {entry.resolution ? `${formatTimestamp(entry.resolution.resolvedAt)} by ${entry.resolution.resolvedBy ?? "-"}` : "not yet"}
            </dd></div>
          </dl>
          {entry.feedback.length > 0 && (
            <ul aria-label="Feedback records" className="mt-3 space-y-1.5 border-t border-slate-100 pt-3">
              {entry.feedback.map((f, i) => (
                <li key={i} className="flex flex-wrap items-center gap-2 text-xs">
                  <Badge>{f.verdict}</Badge>
                  <span>{f.aboutTicketId ? `Match ${f.aboutTicketId}` : "Whole result"}</span>
                  {f.reviewer && <span className="text-muted">by {f.reviewer}</span>}
                  {f.note && <span className="text-slate-700">{f.note}</span>}
                  <span className="text-muted">{formatTimestamp(f.at)}</span>
                </li>
              ))}
            </ul>
          )}
          <details className="mt-3">
            <summary className="cursor-pointer text-primary">Saved data (masked)</summary>
            <pre className="mt-2 max-h-80 overflow-auto rounded bg-slate-50 p-3 font-mono text-xs">{valueToText(entry)}</pre>
          </details>
        </div>
      )}
    </li>
  );
}

function ResultsCard() {
  const { api } = useAppData();
  const loader = useLoader(useCallback((signal: AbortSignal) => api.history(PAGE_SIZE, 0, signal), [api]));
  const { ensure } = loader;
  useEffect(() => ensure(), [ensure]);
  const { data, error, loading } = loader.state;
  const [open, setOpen] = useState<string | null>(null);

  return (
    <Card className="lg:col-span-2" aria-labelledby="results-title">
      <CardHeader id="results-title" icon={<ListOrdered className="h-5 w-5" />} title="Saved results"
                  description={`Newest first, up to ${PAGE_SIZE} (GET /api/tickets/history). Click an ID for its feedback and saved data.`}
                  actions={<Button onClick={() => void loader.load()} loading={loading} loadingText="Loading…"
                                   icon={<RefreshCw className="h-4 w-4" aria-hidden />}>Refresh</Button>} />
      {error ? <div className="p-5"><ErrorAlert error={error} onRetry={() => void loader.load()} /></div>
        : !data ? <LoadingBlock label="Loading saved results" />
        : data.length === 0 ? <EmptyState title="No results saved yet">Results appear here after a ticket is analysed.</EmptyState>
        : (
          <ul className="divide-y divide-slate-100" aria-label="Saved results list">
            {data.map((entry) => (
              <ResultRow key={entry.id} entry={entry} open={open === entry.id}
                         onToggle={() => setOpen(open === entry.id ? null : entry.id)} />
            ))}
          </ul>
        )}
    </Card>
  );
}

export function AuditPage() {
  return (
    <>
      <PageHeader title="Audit History"
                  description="Every saved analysis and reviewer verdict in the permanent database, with a tamper-evident hash chain over all writes." />
      <div className="grid gap-6 lg:grid-cols-2">
        <ChainCard />
        <ResultsCard />
      </div>
    </>
  );
}
