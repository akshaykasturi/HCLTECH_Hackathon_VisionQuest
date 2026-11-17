// One historical ticket (GET /api/tickets/{ticket_id}). The backend masks personal data and secrets.
import { useCallback, useEffect, type ReactNode } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { ArrowLeft, Clock, FileText, Info, Link2, Wrench } from "lucide-react";
import { Card, CardHeader, EmptyState, ErrorAlert, LoadingBlock, Mono } from "../components/common/ui";
import { PriorityBadge, StatusBadge } from "../components/tickets/TicketBadges";
import { ApiError } from "../lib/api";
import { formatDuration, formatTimestamp, humanize } from "../lib/format";
import type { TicketDetail } from "../lib/types";
import { useAppData, useLoader } from "../state/AppData";

/** resolution_text is "<notes> Steps: a | b | c" (backend/app/tickets/similar.py). */
function splitResolution(text: string | null): { notes: string | null; steps: string[] } {
  if (!text) return { notes: null, steps: [] };
  const at = text.indexOf("Steps:");
  if (at < 0) return { notes: text.trim(), steps: [] };
  const notes = text.slice(0, at).trim() || null;
  const steps = text.slice(at + "Steps:".length).split("|").map((s) => s.trim()).filter(Boolean);
  return { notes, steps };
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="py-2.5 sm:grid sm:grid-cols-3 sm:gap-4">
      <dt className="text-sm text-muted">{label}</dt>
      <dd className="mt-0.5 text-sm text-ink sm:col-span-2 sm:mt-0">{children ?? "—"}</dd>
    </div>
  );
}

function Detail({ ticket }: { ticket: TicketDetail }) {
  const { notes, steps } = splitResolution(ticket.resolution_text);
  const timeToResolve = formatDuration(ticket.created_at, ticket.resolved_at);
  return (
    <>
      <header className="mb-6">
        <p className="font-mono text-sm font-medium text-primary">{ticket.ticket_id}</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-ink">{ticket.subject ?? "Untitled ticket"}</h1>
        <div className="mt-3 flex flex-wrap gap-2">
          <PriorityBadge priority={ticket.priority} />
          <StatusBadge status={ticket.status} />
        </div>
      </header>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card>
            <CardHeader title="Problem description" icon={<FileText className="h-5 w-5" />} />
            <p className="whitespace-pre-line p-5 text-sm leading-relaxed text-slate-700">
              {ticket.description || "No description was recorded."}
            </p>
          </Card>

          <Card>
            <CardHeader title="Resolution" icon={<Wrench className="h-5 w-5" />} />
            <div className="space-y-4 p-5 text-sm">
              {!ticket.root_cause && !notes && steps.length === 0 ? (
                <p className="text-muted">No resolution has been recorded yet{ticket.status ? ` (status: ${humanize(ticket.status).toLowerCase()})` : ""}.</p>
              ) : (
                <>
                  {ticket.root_cause && (
                    <div>
                      <h3 className="font-medium text-ink">Root cause</h3>
                      <p className="mt-1 text-slate-700">{ticket.root_cause}</p>
                    </div>
                  )}
                  {notes && (
                    <div>
                      <h3 className="font-medium text-ink">Resolution notes</h3>
                      <p className="mt-1 text-slate-700">{notes}</p>
                    </div>
                  )}
                  {steps.length > 0 && (
                    <div>
                      <h3 className="font-medium text-ink">Steps taken</h3>
                      <ol className="mt-2 list-decimal space-y-1 pl-5 text-slate-700">
                        {steps.map((s, i) => <li key={`${i}-${s}`}>{s}</li>)}
                      </ol>
                    </div>
                  )}
                </>
              )}
            </div>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader title="Details" icon={<Info className="h-5 w-5" />} />
            <dl className="divide-y divide-slate-100 px-5 py-1">
              <Field label="Service">{ticket.affected_service}</Field>
              <Field label="Category">{[ticket.category, ticket.subcategory].filter(Boolean).join(" › ") || null}</Field>
              <Field label="Environment">{ticket.environment ? humanize(ticket.environment) : null}</Field>
              <Field label="Resolver team">{ticket.assigned_team}</Field>
              <Field label="Channel">{ticket.channel ? humanize(ticket.channel) : null}</Field>
            </dl>
          </Card>

          <Card>
            <CardHeader title="Timeline" icon={<Clock className="h-5 w-5" />} />
            <dl className="divide-y divide-slate-100 px-5 py-1">
              <Field label="Created">{formatTimestamp(ticket.created_at)}</Field>
              <Field label="Resolved">{ticket.resolved_at ? formatTimestamp(ticket.resolved_at) : "Not resolved"}</Field>
              {timeToResolve && <Field label="Time to resolve">{timeToResolve}</Field>}
            </dl>
          </Card>

          {ticket.problem_id && (
            <Card>
              <CardHeader title="Related" icon={<Link2 className="h-5 w-5" />} />
              <dl className="px-5 py-1">
                <Field label="Problem record"><Mono>{ticket.problem_id}</Mono></Field>
              </dl>
              <p className="px-5 pb-4 text-xs text-muted">Tickets that share a problem record had the same underlying cause.</p>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}

export function TicketDetailPage() {
  const { ticketId = "" } = useParams();
  const { api } = useAppData();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "";
  const loader = useLoader(useCallback((s: AbortSignal) => api.ticket(ticketId, s), [api, ticketId]));
  const { load } = loader;
  useEffect(() => { void load(); }, [load]);
  const { data, error, loading } = loader.state;
  const notFound = error instanceof ApiError && error.code === "TICKET_NOT_FOUND";

  return (
    <>
      <Link to={`/analyst/tickets${from}`}
            className="mb-4 inline-flex items-center gap-1.5 rounded text-sm font-medium text-primary hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
        <ArrowLeft className="h-4 w-4" aria-hidden /> Back to tickets
      </Link>
      {loading && !data ? (
        <Card as="div"><LoadingBlock label="Loading ticket…" rows={6} /></Card>
      ) : notFound ? (
        <Card as="div">
          <EmptyState title="Ticket not found">There is no historical ticket with the ID {ticketId}.</EmptyState>
        </Card>
      ) : error !== null ? (
        <ErrorAlert error={error} onRetry={() => void load()} />
      ) : data ? (
        <Detail ticket={data} />
      ) : null}
    </>
  );
}
