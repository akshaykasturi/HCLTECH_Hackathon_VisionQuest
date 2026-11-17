import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Sparkles } from "lucide-react";
import { AnalysisView } from "../components/analyze/AnalysisView";
import { Button, Card, CardHeader, EmptyState, ErrorAlert, LoadingBlock, PageHeader } from "../components/common/ui";
import type { AnalysisResult, Preset, TicketIn } from "../lib/types";
import { useAppData, useLoader } from "../state/AppData";

// Limits from TicketIn in backend/app/tickets/models.py.
const SUBJECT_MIN = 3;
const SUBJECT_MAX = 300;
const DESCRIPTION_MAX = 5000;
const ENVIRONMENTS = [["production", "Production"], ["staging", "Staging"], ["development", "Development"]] as const;
const CHANNELS = [["portal", "Portal"], ["email", "Email"], ["chat", "Chat"], ["phone", "Phone"], ["monitoring", "Monitoring alert"]] as const;

type Form = { subject: string; description: string; affected_service: string; environment: string; channel: string };
const EMPTY: Form = { subject: "", description: "", affected_service: "", environment: "", channel: "" };
type Errors = Partial<Record<"subject" | "description", string>>;

const control = "mt-1.5 block w-full rounded-lg border bg-white px-3 py-2 text-sm text-ink shadow-sm placeholder:text-slate-400 focus:outline-none focus:ring-2";
const ok = "border-slate-300 focus:border-primary focus:ring-primary/20";
const bad = "border-danger focus:border-danger focus:ring-danger/20";

function validate(form: Form): Errors {
  const errors: Errors = {};
  const subject = form.subject.trim();
  if (!subject) errors.subject = "Enter a short subject.";
  else if (subject.length < SUBJECT_MIN) errors.subject = `The subject needs at least ${SUBJECT_MIN} characters.`;
  else if (subject.length > SUBJECT_MAX) errors.subject = `Keep the subject under ${SUBJECT_MAX} characters.`;
  const description = form.description.trim();
  if (!description) errors.description = "Describe the problem.";
  else if (description.length > DESCRIPTION_MAX) errors.description = `Keep the description under ${DESCRIPTION_MAX} characters.`;
  return errors;
}

function toTicket(form: Form): TicketIn {
  const opt = (v: string) => (v.trim() ? v.trim() : null);
  return { subject: form.subject.trim(), description: form.description.trim(),
           affected_service: opt(form.affected_service), environment: opt(form.environment), channel: opt(form.channel) };
}

function fromPreset(p: Preset): Form {
  const t = p.ticket;
  return { subject: t.subject ?? "", description: t.description ?? "", affected_service: t.affected_service ?? "",
           environment: t.environment ?? "", channel: t.channel ?? "" };
}

export function AnalyzePage() {
  const { api } = useAppData();
  const [form, setForm] = useState<Form>(EMPTY);
  const [errors, setErrors] = useState<Errors>({});
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const resultRef = useRef<HTMLDivElement>(null);

  // Demo tickets and service names are helpers only: if they fail to load, the form still works.
  const presets = useLoader(useCallback((s: AbortSignal) => api.presets(s), [api]));
  const taxonomy = useLoader(useCallback((s: AbortSignal) => api.taxonomy(s), [api]));
  useEffect(() => { presets.ensure(); taxonomy.ensure(); }, [presets, taxonomy]);
  useEffect(() => () => controller.current?.abort(), []);

  const set = (field: keyof Form) => (value: string) => {
    setForm((f) => ({ ...f, [field]: value }));
    if (field in errors) setErrors((e) => ({ ...e, [field]: undefined }));
  };

  async function run(ticketForm: Form) {
    const found = validate(ticketForm);
    setErrors(found);
    if (Object.keys(found).length) {
      document.getElementById(found.subject ? "subject" : "description")?.focus();
      return;
    }
    controller.current?.abort();
    const ctl = new AbortController();
    controller.current = ctl;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const data = await api.analyze(toTicket(ticketForm), ctl.signal);
      if (ctl.signal.aborted) return;
      setResult(data);
      resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      if (!ctl.signal.aborted) setError(err);
    } finally {
      if (!ctl.signal.aborted) setBusy(false);
    }
  }

  const onSubmit = (e: FormEvent) => { e.preventDefault(); void run(form); };
  const subjectErr = errors.subject;
  const descErr = errors.description;

  return (
    <>
      <PageHeader title="Analyze a ticket"
                  description="Classify a ticket, find similar past tickets and knowledge-base articles, and get evidence-backed next steps." />
      <div className="grid gap-6 lg:grid-cols-12">
        <Card className="h-fit lg:col-span-5">
          <CardHeader title="Ticket" description="What the employee reported." />
          <form onSubmit={onSubmit} noValidate className="space-y-4 p-5" aria-label="Ticket to analyse">
            {presets.state.data && presets.state.data.length > 0 && (
              <div>
                <label htmlFor="preset" className="text-sm font-medium text-ink">Load a demo ticket</label>
                <select id="preset" defaultValue="" className={`${control} ${ok}`}
                        onChange={(e) => {
                          const p = presets.state.data?.find((x) => x.id === e.target.value);
                          if (p) { setForm(fromPreset(p)); setErrors({}); }
                        }}>
                  <option value="">Choose a demo ticket…</option>
                  {presets.state.data.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                </select>
              </div>
            )}
            <div>
              <label htmlFor="subject" className="text-sm font-medium text-ink">Subject <span className="text-danger-text" aria-hidden>*</span></label>
              <input id="subject" value={form.subject} onChange={(e) => set("subject")(e.target.value)} maxLength={SUBJECT_MAX}
                     placeholder="e.g. VPN disconnects every few minutes" aria-required="true"
                     aria-invalid={subjectErr ? true : undefined} aria-describedby={subjectErr ? "subject-error" : undefined}
                     className={`${control} ${subjectErr ? bad : ok}`} />
              {subjectErr && <p id="subject-error" className="mt-1 text-sm text-danger-text">{subjectErr}</p>}
            </div>
            <div>
              <label htmlFor="description" className="text-sm font-medium text-ink">Description <span className="text-danger-text" aria-hidden>*</span></label>
              <textarea id="description" rows={6} value={form.description} onChange={(e) => set("description")(e.target.value)}
                        placeholder="What happened, since when, error messages, who is affected."
                        aria-required="true" aria-invalid={descErr ? true : undefined}
                        aria-describedby={descErr ? "description-error" : "description-help"}
                        className={`${control} ${descErr ? bad : ok}`} />
              {descErr
                ? <p id="description-error" className="mt-1 text-sm text-danger-text">{descErr}</p>
                : <p id="description-help" className="mt-1 text-xs text-muted">Personal data and secrets are masked before anything is searched or stored.</p>}
            </div>
            <div>
              <label htmlFor="affected_service" className="text-sm font-medium text-ink">Affected service</label>
              <input id="affected_service" list="services" value={form.affected_service}
                     onChange={(e) => set("affected_service")(e.target.value)} placeholder="e.g. Corporate VPN"
                     className={`${control} ${ok}`} />
              <datalist id="services">
                {(taxonomy.state.data?.affected_service ?? []).map((s) => <option key={s} value={s} />)}
              </datalist>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label htmlFor="environment" className="text-sm font-medium text-ink">Environment</label>
                <select id="environment" value={form.environment} onChange={(e) => set("environment")(e.target.value)} className={`${control} ${ok}`}>
                  <option value="">Not specified</option>
                  {ENVIRONMENTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </div>
              <div>
                <label htmlFor="channel" className="text-sm font-medium text-ink">Channel</label>
                <select id="channel" value={form.channel} onChange={(e) => set("channel")(e.target.value)} className={`${control} ${ok}`}>
                  <option value="">Not specified</option>
                  {CHANNELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </div>
            </div>
            <div className="flex flex-wrap gap-2 pt-1">
              <Button type="submit" variant="primary" loading={busy} loadingText="Analysing…" icon={<Sparkles className="h-4 w-4" aria-hidden />}>
                Analyze ticket
              </Button>
              <Button variant="ghost" disabled={busy} onClick={() => { setForm(EMPTY); setErrors({}); setResult(null); setError(null); }}>
                Clear
              </Button>
            </div>
          </form>
        </Card>

        <div ref={resultRef} className="scroll-mt-20 lg:col-span-7" aria-live="polite">
          {busy ? (
            <Card as="div"><LoadingBlock label="Analysing the ticket…" rows={6} /></Card>
          ) : error !== null ? (
            <ErrorAlert error={error} onRetry={() => void run(form)} />
          ) : result ? (
            <AnalysisView result={result} />
          ) : (
            <Card as="div">
              <EmptyState title="No ticket analysed yet" icon={<Sparkles className="h-5 w-5" />}>
                Enter a ticket or load a demo ticket, then choose Analyze ticket. The result shows the outcome, the
                evidence it is based on and the suggested next steps.
              </EmptyState>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
