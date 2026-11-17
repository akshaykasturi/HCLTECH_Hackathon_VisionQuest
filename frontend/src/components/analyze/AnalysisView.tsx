// The analyst's view of one AnalysisResult: outcome, classification, cited steps, evidence, feedback.
// Shows only what the backend returned: no invented confidence, no placeholder results.
import { useState, type ReactNode } from "react";
import {
  AlertTriangle, ArrowUpRight, BookOpen, CheckCircle2, ChevronDown, FileText, HelpCircle, History, ListChecks,
  MessageSquare, ShieldAlert, Tags, ThumbsDown, ThumbsUp, Timer,
} from "lucide-react";
import { ApiError } from "../../lib/api";
import type {
  AnalysisResult, Classification, KBHit, MatchLabel, Outcome, Recommendation, SimilarTicket, TicketVerdict,
} from "../../lib/types";
import { useAppData } from "../../state/AppData";
import { useToast } from "../../state/Toasts";
import { Badge, Button, Card, CardHeader, ErrorAlert, Mono, type Tone } from "../common/ui";

const pct = (p: number) => `${Math.round(p * 100)}%`;

const OUTCOME: Record<Outcome, { title: string; text: string; icon: ReactNode; box: string; iconBox: string }> = {
  RECOMMEND: {
    title: "Recommended fix",
    text: "Similar resolved tickets and knowledge-base articles support these steps. Review them before applying.",
    icon: <CheckCircle2 className="h-6 w-6" aria-hidden />,
    box: "border-success/40 bg-success-soft", iconBox: "bg-success text-white",
  },
  CLARIFY: {
    title: "More information needed",
    text: "There is not enough detail to recommend a fix safely. Ask the requester the question below.",
    icon: <HelpCircle className="h-6 w-6" aria-hidden />,
    box: "border-warning/40 bg-warning-soft", iconBox: "bg-warning text-white",
  },
  ESCALATE: {
    title: "Escalate to a specialist team",
    text: "This ticket needs a human specialist. Do not apply a fix from this screen.",
    icon: <ArrowUpRight className="h-6 w-6" aria-hidden />,
    box: "border-danger/40 bg-danger-soft", iconBox: "bg-danger text-white",
  },
  BLOCKED: {
    title: "Request blocked",
    text: "The safety screen stopped this request. Nothing was searched and no AI text was generated.",
    icon: <ShieldAlert className="h-6 w-6" aria-hidden />,
    box: "border-slate-700 bg-slate-800 text-white", iconBox: "bg-white/15 text-white",
  },
};

/** What the outcome banner needs: a live AnalysisResult has all of it; a saved history result may lack some. */
export interface OutcomeInfo {
  outcome: Outcome;
  confidence?: number | null;     // missing = not shown (never displayed as 0%)
  reasons: string[];
  question?: string | null;
  escalation_group?: string | null;
  screen?: { codes: string[] } | null;
}

export function OutcomeBanner({ result }: { result: OutcomeInfo }) {
  const o = OUTCOME[result.outcome];
  const dark = result.outcome === "BLOCKED";
  return (
    <section aria-label={`Outcome: ${o.title}`} data-outcome={result.outcome} className={`rounded-xl border p-5 ${o.box}`}>
      <div className="flex items-start gap-4">
        <span className={`grid h-11 w-11 shrink-0 place-items-center rounded-xl ${o.iconBox}`}>{o.icon}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold">{o.title}</h2>
            <span className={`rounded-full px-2 py-0.5 font-mono text-xs ${dark ? "bg-white/15" : "bg-white/70 text-slate-700"}`}>
              {result.outcome}
            </span>
          </div>
          <p className={`mt-1 text-sm ${dark ? "text-slate-200" : "text-slate-700"}`}>{o.text}</p>
          {result.outcome === "CLARIFY" && result.question && (
            <p className="mt-3 rounded-lg bg-white px-3 py-2 text-sm font-medium text-ink ring-1 ring-warning/30">
              <span className="text-warning-text">Ask: </span>{result.question}
            </p>
          )}
          {result.outcome === "ESCALATE" && result.escalation_group && (
            <p className="mt-3 text-sm text-ink">Route to <strong>{result.escalation_group}</strong></p>
          )}
          {result.outcome === "BLOCKED" && result.screen && result.screen.codes.length > 0 && (
            <p className="mt-3 text-sm text-slate-200">Screen codes: {result.screen.codes.join(", ")}</p>
          )}
          {(typeof result.confidence === "number" || result.reasons.length > 0) && (
            <div className={`mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs ${dark ? "text-slate-300" : "text-slate-600"}`}>
              {typeof result.confidence === "number" && (
                <span>Decision confidence <strong className={dark ? "text-white" : "text-ink"}>{pct(result.confidence)}</strong></span>
              )}
              {result.reasons.length > 0 && <span>Reasons: <span className="font-mono">{result.reasons.join(", ")}</span></span>}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

const NO_CLASSIFICATION = "No classification was returned. The classifier is not connected yet, so category, "
  + "subcategory and priority are not shown.";

/** emptyMessage: what to say when there is no classification (the Analyze page's wording by default). */
export function ClassificationCard({ classification, emptyMessage = NO_CLASSIFICATION }:
  { classification: Classification | null; emptyMessage?: string }) {
  return (
    <Card>
      <CardHeader title="Classification" icon={<Tags className="h-5 w-5" />} />
      {classification ? (
        <div className="grid gap-3 p-5 sm:grid-cols-3">
          {([["Category", classification.category], ["Subcategory", classification.subcategory],
             ["Priority", classification.priority]] as const).map(([name, score]) => (
            <div key={name} className="rounded-lg bg-page p-3">
              <p className="text-xs font-medium uppercase tracking-wide text-muted">{name}</p>
              <p className="mt-1 font-semibold text-ink">{score.label}</p>
              <p className="text-xs text-muted">Confidence {pct(score.prob)}</p>
            </div>
          ))}
          {classification.priority_rule && (
            <p className="text-xs text-muted sm:col-span-3">Priority adjusted by rule <Mono>{classification.priority_rule}</Mono></p>
          )}
        </div>
      ) : (
        <p className="p-5 text-sm text-muted">{emptyMessage}</p>
      )}
    </Card>
  );
}

function sourceLabel(sourceId: string) {
  return sourceId.startsWith("doc:") ? "Knowledge base" : "Past ticket";
}

export function StepsCard({ recommendation, findings }: { recommendation: Recommendation; findings: AnalysisResult["findings"] }) {
  return (
    <Card>
      <CardHeader title="Recommended resolution steps" icon={<ListChecks className="h-5 w-5" />}
                  description={recommendation.summary}
                  actions={<Badge tone="neutral" title="Who wrote the wording">Written by {recommendation.generator}</Badge>} />
      {recommendation.steps.length === 0 ? (
        <p className="p-5 text-sm text-muted">No steps passed the checks.</p>
      ) : (
        <ol className="divide-y divide-slate-100">
          {recommendation.steps.map((step, i) => (
            <li key={`${step.source_id}-${i}`} className="flex gap-4 px-5 py-4">
              <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-primary-soft text-sm font-semibold text-primary">{i + 1}</span>
              <div className="min-w-0 flex-1">
                <p className="font-medium text-ink">{step.text}</p>
                <blockquote className="mt-2 border-l-2 border-slate-300 pl-3 text-sm italic text-slate-600">“{step.excerpt}”</blockquote>
                <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
                  <Badge tone="primary">{sourceLabel(step.source_id)}</Badge>
                  <Mono className="text-xs">{step.source_id}</Mono>
                  <span>· {step.why}</span>
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}
      {findings.length > 0 && (
        <div className="border-t border-slate-100 px-5 py-3">
          <p className="text-xs font-medium text-muted">Automatic checks</p>
          <ul className="mt-1 space-y-1">
            {findings.map((f, i) => (
              <li key={`${f.code}-${i}`} className="flex items-start gap-2 text-sm text-slate-700">
                <AlertTriangle className={`mt-0.5 h-4 w-4 shrink-0 ${f.severity === "info" ? "text-muted" : "text-warning"}`} aria-hidden />
                {f.message}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

const MATCH: Record<MatchLabel, { label: string; tone: Tone }> = {
  likely_duplicate: { label: "Likely duplicate", tone: "success" },
  related: { label: "Related", tone: "primary" },
  weak: { label: "Weak match", tone: "neutral" },
};

function SimilarCard({ similar }: { similar: SimilarTicket[] }) {
  return (
    <Card>
      <CardHeader title="Similar past tickets" icon={<History className="h-5 w-5" />}
                  description={similar.length ? `${similar.length} found, best match first` : undefined} />
      {similar.length === 0 ? (
        <p className="p-5 text-sm text-muted">No similar past tickets were found.</p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {similar.map((t) => (
            <li key={t.ticket_id} className="px-5 py-4">
              <div className="flex flex-wrap items-center gap-2">
                <Mono className="text-xs font-medium">{t.ticket_id}</Mono>
                <Badge tone={MATCH[t.match].tone}>{MATCH[t.match].label}</Badge>
                {t.lookalike && <Badge tone="warning" title="Shares keywords but is probably a different issue">Lookalike</Badge>}
                <span className="text-xs text-muted">{t.status}</span>
              </div>
              <p className="mt-1 font-medium text-ink">{t.subject}</p>
              <p className="text-sm text-muted">{[t.category, t.subcategory].filter(Boolean).join(" › ")}</p>
              {t.why.length > 0 && <p className="mt-1 text-xs text-slate-600">Why it matched: {t.why.join("; ")}</p>}
              {Object.keys(t.past_feedback).length > 0 && (
                <p className="mt-1 text-xs text-muted">
                  Past analyst feedback: {Object.entries(t.past_feedback).map(([k, v]) => `${v} ${k}`).join(", ")}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function KbCard({ kb }: { kb: KBHit[] }) {
  return (
    <Card>
      <CardHeader title="Knowledge-base evidence" icon={<BookOpen className="h-5 w-5" />} />
      {kb.length === 0 ? (
        <p className="p-5 text-sm text-muted">No knowledge-base articles were returned.</p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {kb.map((hit) => (
            <li key={hit.section_id} className="px-5 py-4">
              <details className="group">
                <summary className="flex cursor-pointer list-none items-start justify-between gap-3 rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
                  <span className="min-w-0">
                    <span className="flex items-center gap-2 font-medium text-ink"><FileText className="h-4 w-4 shrink-0 text-muted" aria-hidden />{hit.title}</span>
                    <Mono className="text-xs">{hit.section_id}</Mono>
                  </span>
                  <ChevronDown className="mt-1 h-4 w-4 shrink-0 text-muted transition-transform group-open:rotate-180" aria-hidden />
                </summary>
                <p className="mt-2 whitespace-pre-line text-sm text-slate-700">{hit.text}</p>
              </details>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

const VERDICTS: { value: TicketVerdict; label: string; icon: ReactNode }[] = [
  { value: "useful", label: "Useful", icon: <ThumbsUp className="h-4 w-4" aria-hidden /> },
  { value: "incorrect", label: "Incorrect", icon: <ThumbsDown className="h-4 w-4" aria-hidden /> },
  { value: "unresolved", label: "Unresolved", icon: <HelpCircle className="h-4 w-4" aria-hidden /> },
];

export function FeedbackCard({ resultId, onSaved }: { resultId: string; onSaved?: () => void }) {
  const { api } = useAppData();
  const { notify } = useToast();
  const [verdict, setVerdict] = useState<TicketVerdict | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const pending = error instanceof ApiError && error.code === "DB_NOT_READY";

  async function submit() {
    if (!verdict) return;
    setBusy(true);
    setError(null);
    try {
      await api.ticketFeedback(resultId, { verdict, ...(note.trim() ? { note: note.trim() } : {}) });
      setSaved(true);
      notify("Thanks, your feedback was saved.");
      onSaved?.();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader title="Was this analysis helpful?" icon={<MessageSquare className="h-5 w-5" />}
                  description="Your feedback is stored with the result and shown on similar tickets later." />
      <div className="space-y-3 p-5">
        {saved ? (
          <p role="status" className="flex items-center gap-2 text-sm text-success-text">
            <CheckCircle2 className="h-4 w-4" aria-hidden /> Feedback saved: {verdict}.
          </p>
        ) : (
          <>
            <div role="group" aria-label="Feedback" className="flex flex-wrap gap-2">
              {VERDICTS.map((v) => (
                <Button key={v.value} icon={v.icon} aria-pressed={verdict === v.value} onClick={() => setVerdict(v.value)}
                        className={verdict === v.value ? "border-primary bg-primary-soft text-primary" : ""}>
                  {v.label}
                </Button>
              ))}
            </div>
            <label htmlFor="feedback-note" className="block text-sm font-medium text-ink">Note (optional)</label>
            <textarea id="feedback-note" rows={2} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)}
                      placeholder="For example: step 2 fixed it, or the match was a different issue."
                      className="block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20" />
            {pending ? (
              <p role="status" className="rounded-lg bg-primary-soft px-3 py-2 text-sm text-slate-700">
                Feedback can't be saved yet: the results database is not connected. Your choice is not lost on this
                page; try again once it is connected.
              </p>
            ) : error !== null && <ErrorAlert error={error} compact />}
            <Button variant="primary" onClick={submit} disabled={!verdict} loading={busy} loadingText="Saving…">Send feedback</Button>
          </>
        )}
      </div>
    </Card>
  );
}

function TechnicalDetails({ result }: { result: AnalysisResult }) {
  return (
    <details className="rounded-xl border border-slate-200 bg-white px-5 py-3 text-sm shadow-card">
      <summary className="flex cursor-pointer items-center gap-2 font-medium text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
        <Timer className="h-4 w-4 text-muted" aria-hidden /> Technical details
      </summary>
      <dl className="mt-3 grid gap-x-6 gap-y-1 text-slate-700 sm:grid-cols-2">
        <dt className="text-muted">Result ID</dt><dd><Mono>{result.result_id}</Mono></dd>
        {Object.entries(result.timings_ms).map(([k, v]) => (
          <div key={k} className="contents"><dt className="text-muted">{k}</dt><dd>{Math.round(v)} ms</dd></div>
        ))}
      </dl>
      {result.trace.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs text-slate-600">
          {result.trace.map((t, i) => <li key={`${t.step}-${i}`}><Mono className="text-xs">{t.step}</Mono> {t.detail}</li>)}
        </ul>
      )}
    </details>
  );
}

export function AnalysisView({ result }: { result: AnalysisResult }) {
  const blocked = result.outcome === "BLOCKED";
  return (
    <div className="space-y-5">
      <OutcomeBanner result={result} />
      {!blocked && <ClassificationCard classification={result.classification} />}
      {!blocked && result.recommendation && <StepsCard recommendation={result.recommendation} findings={result.findings} />}
      {!blocked && <SimilarCard similar={result.similar} />}
      {!blocked && <KbCard kb={result.kb} />}
      <FeedbackCard key={result.result_id} resultId={result.result_id} />
      <TechnicalDetails result={result} />
    </div>
  );
}
