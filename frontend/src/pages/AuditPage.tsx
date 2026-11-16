import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Fingerprint, ListOrdered, RefreshCw, Search, ShieldCheck, ShieldX } from "lucide-react";
import { ID_PATTERN } from "../lib/api";
import { formatClock, formatTimestamp, shortHash, valueToText } from "../lib/format";
import type { ChainStatus, FeedbackRecord, SavedResult } from "../lib/types";
import { useAppData, useLoader } from "../state/AppData";
import { OutcomeBadge } from "../components/common/status";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorAlert, LoadingBlock, Mono, PageHeader } from "../components/common/ui";

function ChainCard() {
  const { api } = useAppData();
  const [result, setResult] = useState<ChainStatus | null>(null);
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
      setResult(await api.verifyAudit());
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
                  description="Recalculates every SHA-256 hash in the append-only audit log (GET /api/audit/verify)."
                  actions={<Button variant="primary" onClick={() => void verify()} loading={loading} loadingText="Verifying…"
                                   icon={<ShieldCheck className="h-4 w-4" aria-hidden />}>Verify chain</Button>} />
      <div className="p-5" aria-live="polite">
        {error ? <ErrorAlert error={error} onRetry={() => void verify()} retrying={loading} /> : !result ? (
          <p className="text-sm text-muted">Not checked yet. Press Verify chain.</p>
        ) : result.valid ? (
          <div className="flex items-start gap-3 rounded-lg border border-success/40 bg-success-soft p-4">
            <ShieldCheck className="mt-0.5 h-5 w-5 text-success-text" aria-hidden />
            <div className="text-sm">
              <p className="font-semibold text-success-text">Chain verified</p>
              <p className="mt-0.5 text-slate-700">{result.events_checked} events checked, no gaps or edits found.</p>
              <p className="mt-1 text-xs text-muted">Head hash <Mono>{shortHash(result.head_hash, 16)}</Mono>{checkedAt && ` · checked at ${formatClock(checkedAt)}`}</p>
            </div>
          </div>
        ) : (
          <div role="alert" className="flex items-start gap-3 rounded-lg border border-danger/40 bg-danger-soft p-4">
            <ShieldX className="mt-0.5 h-5 w-5 text-danger-text" aria-hidden />
            <div className="text-sm">
              <p className="font-semibold text-danger-text">Chain check failed</p>
              <p className="mt-0.5 text-slate-700">
                {result.first_bad_seq !== null ? `At event #${result.first_bad_seq}: ` : ""}{result.reason ?? "unknown reason"}
              </p>
              <p className="mt-1 text-xs text-muted">{result.events_checked} events checked before the problem.</p>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

function LookupCard({ selected }: { selected: string | null }) {
  const { api } = useAppData();
  const [value, setValue] = useState("");
  const [inputError, setInputError] = useState<string | null>(null);
  const [found, setFound] = useState<{ result: SavedResult; feedback: FeedbackRecord[] } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  const lookup = useCallback(async (raw: string) => {
    const id = raw.trim();
    if (!ID_PATTERN.test(id)) {
      setInputError("Enter a result ID (letters, digits, dots, dashes or underscores).");
      return;
    }
    setInputError(null);
    setLoading(true);
    setError(null);
    setFound(null);
    try {
      const [result, feedback] = await Promise.all([api.result(id), api.feedback(id)]);
      setFound({ result, feedback });
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    if (selected) {
      setValue(selected);
      void lookup(selected);
    }
  }, [selected, lookup]);

  function submit(e: FormEvent) {
    e.preventDefault();
    void lookup(value);
  }

  return (
    <Card aria-labelledby="lookup-title">
      <CardHeader id="lookup-title" icon={<Search className="h-5 w-5" />} title="Look up a result"
                  description="Reads one saved result and its reviewer feedback from the audit log." />
      <div className="space-y-4 p-5">
        <form onSubmit={submit} noValidate className="flex flex-wrap items-start gap-2">
          <div className="min-w-[14rem] flex-1">
            <label htmlFor="lookup-id" className="sr-only">Result ID</label>
            <input id="lookup-id" value={value} onChange={(e) => setValue(e.target.value)} placeholder="Result ID"
                   aria-invalid={Boolean(inputError)} aria-describedby={inputError ? "lookup-error" : undefined}
                   className="w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/30" />
            {inputError && <p id="lookup-error" className="mt-1.5 text-sm text-danger-text">{inputError}</p>}
          </div>
          <Button type="submit" variant="primary" loading={loading} loadingText="Looking up…">Look up</Button>
        </form>
        {error ? <ErrorAlert error={error} compact /> : null}
        {found && (
          <div className="rounded-lg border border-slate-200 p-4 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono font-semibold">{found.result.case_id}</span>
              <OutcomeBadge outcome={typeof found.result.outcome === "string" ? found.result.outcome : null} />
            </div>
            <p className="mt-2 text-xs text-muted">{found.feedback.length} feedback {found.feedback.length === 1 ? "record" : "records"}</p>
            {found.feedback.length > 0 && (
              <ul aria-label="Feedback records" className="mt-2 space-y-1.5 border-t border-slate-100 pt-3">
                {found.feedback.map((f) => (
                  <li key={f.feedback_id} className="flex flex-wrap items-center gap-2 text-xs">
                    <Badge>{f.verdict}</Badge><span>{f.item_id ?? "Whole result"}</span>
                    {f.note && <span className="text-slate-700">{f.note}</span>}
                    <span className="text-muted">{formatTimestamp(f.created_at)}</span>
                  </li>
                ))}
              </ul>
            )}
            <details className="mt-3">
              <summary className="cursor-pointer text-primary">Saved data (masked)</summary>
              <pre className="mt-2 max-h-80 overflow-auto rounded bg-slate-50 p-3 font-mono text-xs">{valueToText(found.result)}</pre>
            </details>
          </div>
        )}
      </div>
    </Card>
  );
}

function ResultsCard({ onSelect }: { onSelect: (id: string) => void }) {
  const { api } = useAppData();
  const loader = useLoader(useCallback((signal: AbortSignal) => api.results(signal), [api]));
  const { ensure } = loader;
  useEffect(() => ensure(), [ensure]);
  const { data, error, loading } = loader.state;

  return (
    <Card className="lg:col-span-2" aria-labelledby="results-title">
      <CardHeader id="results-title" icon={<ListOrdered className="h-5 w-5" />} title="Saved results"
                  description="Newest first (GET /api/audit/results)."
                  actions={<Button onClick={() => void loader.load()} loading={loading} loadingText="Loading…"
                                   icon={<RefreshCw className="h-4 w-4" aria-hidden />}>Refresh</Button>} />
      {error ? <div className="p-5"><ErrorAlert error={error} onRetry={() => void loader.load()} /></div>
        : !data ? <LoadingBlock label="Loading saved results" />
        : data.length === 0 ? <EmptyState title="No results saved yet">Results appear here once the pipeline records them.</EmptyState>
        : (
          <ul className="divide-y divide-slate-100">
            {data.map((r) => (
              <li key={r.result_id} className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm">
                <button type="button" onClick={() => onSelect(r.result_id)}
                        className="rounded font-mono font-medium text-primary hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
                  {r.result_id}
                </button>
                <span className="font-mono text-muted">{r.case_id}</span>
                <OutcomeBadge outcome={r.outcome} />
                {r.summary && <span className="min-w-0 flex-1 truncate text-slate-700">{r.summary}</span>}
                <span className="text-xs text-muted">{formatTimestamp(r.recorded_at)} · {r.feedback_count} feedback</span>
              </li>
            ))}
          </ul>
        )}
    </Card>
  );
}

export function AuditPage() {
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <>
      <PageHeader title="Audit History" description="The backend keeps an append-only, hash-chained SQLite log of every saved result and every reviewer verdict." />
      <div className="grid gap-6 lg:grid-cols-2">
        <ChainCard />
        <LookupCard selected={selected} />
        <ResultsCard onSelect={setSelected} />
      </div>
    </>
  );
}
