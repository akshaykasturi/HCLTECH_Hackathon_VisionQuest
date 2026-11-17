// Incident Management: search and filter historical tickets (GET /api/tickets, FR-07).
// Search text and filters live in the URL, so the browser Back button and "Back to tickets" restore them.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { Loader2, Search, SearchX, X } from "lucide-react";
import { Button, Card, EmptyState, ErrorAlert, PageHeader, Skeleton } from "../components/common/ui";
import { PriorityBadge, StatusBadge } from "../components/tickets/TicketBadges";
import { ApiError } from "../lib/api";
import { formatDate } from "../lib/format";
import type { TicketQuery, TicketSummary } from "../lib/types";
import { useAppData, useLoader } from "../state/AppData";

const SEARCH_DEBOUNCE_MS = 350;
const LIST_LIMIT = 50; // backend LIST_LIMIT: newest 50 when there is no search text
const TICKET_ID = /^INC-\d{3,}$/i; // the data's ticket IDs: an exact ID opens that ticket directly

const FILTERS = [
  { key: "affected_service", label: "Service", taxonomy: "affected_service" },
  { key: "category", label: "Category", taxonomy: "category" },
  { key: "subcategory", label: "Subcategory", taxonomy: null },
  { key: "priority", label: "Priority", taxonomy: "priority" },
  { key: "status", label: "Status", taxonomy: "status" },
] as const;
type FilterKey = (typeof FILTERS)[number]["key"];

const select = "mt-1 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-ink shadow-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:bg-slate-50 disabled:text-muted";

// hiddenByFilters: an exact-ID lookup found the ticket, but the active filters exclude it.
type ListState = { items: TicketSummary[] | null; loading: boolean; error: unknown; idLookup: boolean; hiddenByFilters: boolean };

/** Same rule as the backend's filters (exact match, case-insensitive), so an exact-ID result obeys the chips too. */
function matchesFilters(ticket: TicketSummary, query: TicketQuery): boolean {
  return FILTERS.every(({ key }) => {
    const want = query[key];
    return !want || (ticket[key] ?? "").trim().toLowerCase() === want.trim().toLowerCase();
  });
}

function readQuery(params: URLSearchParams): TicketQuery {
  const query: TicketQuery = {};
  const q = params.get("q")?.trim();
  if (q) query.q = q;
  for (const { key } of FILTERS) {
    const v = params.get(key);
    if (v) query[key] = v;
  }
  return query;
}

function TableSkeleton() {
  return (
    <div role="status" aria-live="polite" className="divide-y divide-slate-100">
      <span className="sr-only">Loading tickets…</span>
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className="flex items-center gap-4 px-5 py-4">
          <Skeleton className="h-4 w-24" /><Skeleton className="h-4 flex-1" /><Skeleton className="hidden h-4 w-32 md:block" />
          <Skeleton className="h-5 w-20 rounded-full" />
        </div>
      ))}
    </div>
  );
}

export function TicketsPage() {
  const { api } = useAppData();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const query = useMemo(() => readQuery(params), [params]);
  const paramsKey = params.toString();

  // ---- search box (debounced into the URL)
  const [text, setText] = useState(query.q ?? "");
  const lastPushed = useRef(query.q ?? "");
  useEffect(() => {
    const urlQ = query.q ?? "";
    if (urlQ !== lastPushed.current) { lastPushed.current = urlQ; setText(urlQ); } // changed by Back / links
  }, [query.q]);
  useEffect(() => {
    const next = text.trim();
    if (next === (query.q ?? "")) return;
    const timer = window.setTimeout(() => {
      lastPushed.current = next;
      setParams((p) => { const n = new URLSearchParams(p); if (next) n.set("q", next); else n.delete("q"); return n; },
                { replace: true });
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [text, query.q, setParams]);

  const setFilter = (key: FilterKey, value: string) => {
    setParams((p) => {
      const n = new URLSearchParams(p);
      if (value) n.set(key, value); else n.delete(key);
      if (key === "category") n.delete("subcategory"); // subcategories belong to one category
      return n;
    });
  };
  const clearFilters = () => setParams((p) => {
    const n = new URLSearchParams();
    const q = p.get("q");
    if (q) n.set("q", q);
    return n;
  });
  const clearSearch = () => {
    setText("");
    lastPushed.current = "";
    setParams((p) => { const n = new URLSearchParams(p); n.delete("q"); return n; }, { replace: true });
  };
  // One URL update: React Router builds each update from the last render, so clearSearch() + clearFilters()
  // in a row would put the search text back.
  const clearAll = () => {
    setText("");
    lastPushed.current = "";
    setParams(new URLSearchParams());
  };

  // ---- filter values from the backend (the list still works if this fails)
  const taxonomy = useLoader(useCallback((s: AbortSignal) => api.taxonomy(s), [api]));
  useEffect(() => taxonomy.ensure(), [taxonomy]);
  const tax = taxonomy.state.data;
  const options = (key: FilterKey): string[] => {
    if (!tax) return [];
    if (key === "subcategory") return query.category ? tax.subcategory[query.category] ?? [] : [];
    return tax[key] ?? [];
  };

  // ---- results
  const [list, setList] = useState<ListState>({ items: null, loading: true, error: null, idLookup: false, hiddenByFilters: false });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const ctl = new AbortController();
    const idLookup = !!query.q && TICKET_ID.test(query.q);
    setList((s) => ({ ...s, loading: true, error: null, idLookup }));
    type Loaded = { items: TicketSummary[]; hiddenByFilters: boolean };
    const load: Promise<Loaded> = idLookup
      ? api.ticket(query.q!.toUpperCase(), ctl.signal)
          .then((t): Loaded => (matchesFilters(t, query) ? { items: [t], hiddenByFilters: false }
                                                          : { items: [], hiddenByFilters: true }))
          .catch((e: unknown): Loaded => {
            if (e instanceof ApiError && e.code === "TICKET_NOT_FOUND") return { items: [], hiddenByFilters: false };
            throw e;
          })
      : api.searchTickets(query, ctl.signal).then((items) => ({ items, hiddenByFilters: false }));
    load.then(({ items, hiddenByFilters }) => {
      if (!ctl.signal.aborted) setList({ items, loading: false, error: null, idLookup, hiddenByFilters });
    }).catch((error: unknown) => {
      if (!ctl.signal.aborted) setList({ items: null, loading: false, error, idLookup, hiddenByFilters: false });
    });
    return () => ctl.abort();
    // paramsKey captures every query value; attempt re-runs after Retry
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, paramsKey, attempt]);

  const active = FILTERS.filter(({ key }) => query[key]);
  const items = list.items ?? [];
  const searching = !!query.q;
  const open = (t: TicketSummary) => navigate(`/analyst/tickets/${encodeURIComponent(t.ticket_id)}`, { state: { from: location.search } });

  let summary = "";
  if (!list.loading && list.items) {
    if (list.idLookup) summary = items.length ? "Exact ticket ID match" : "";
    else if (searching) summary = `${items.length} best ${items.length === 1 ? "match" : "matches"} for “${query.q}”`;
    else if (items.length >= LIST_LIMIT) summary = `Showing the ${LIST_LIMIT} most recent tickets. Narrow the filters or search to find others.`;
    else summary = `${items.length} ${items.length === 1 ? "ticket" : "tickets"}, newest first`;
  }

  return (
    <>
      <PageHeader title="Incident Management" description="Search, review and manage historical IT support incidents." />

      <Card className="mb-6">
        <div className="space-y-4 p-5">
          <div className="relative">
            <label htmlFor="ticket-search" className="sr-only">Search tickets</label>
            <Search className="pointer-events-none absolute left-3.5 top-1/2 h-5 w-5 -translate-y-1/2 text-muted" aria-hidden />
            <input id="ticket-search" type="search" value={text} onChange={(e) => setText(e.target.value)} maxLength={300}
                   placeholder="Search by ticket ID, subject or description..."
                   className="block w-full rounded-xl border border-slate-300 bg-white py-3 pl-11 pr-20 text-base text-ink shadow-sm placeholder:text-slate-400 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 [&::-webkit-search-cancel-button]:hidden" />
            <div className="absolute right-2 top-1/2 flex -translate-y-1/2 items-center gap-1">
              {list.loading && <Loader2 className="h-4 w-4 animate-spin text-muted" aria-hidden />}
              {text && (
                <button type="button" onClick={clearSearch} aria-label="Clear search"
                        className="rounded-lg p-1.5 text-muted hover:bg-slate-100 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
                  <X className="h-4 w-4" aria-hidden />
                </button>
              )}
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {FILTERS.map(({ key, label }) => {
              const values = options(key);
              const disabled = !tax || (key === "subcategory" && !query.category);
              return (
                <div key={key}>
                  <label htmlFor={`filter-${key}`} className="text-xs font-medium uppercase tracking-wide text-muted">{label}</label>
                  <select id={`filter-${key}`} value={query[key] ?? ""} disabled={disabled}
                          onChange={(e) => setFilter(key, e.target.value)} className={select}>
                    <option value="">{key === "subcategory" && !query.category ? "Choose a category first" : `All ${label.toLowerCase()}`}</option>
                    {values.map((v) => <option key={v} value={v}>{v}</option>)}
                    {query[key] && !values.includes(query[key]!) && <option value={query[key]}>{query[key]}</option>}
                  </select>
                </div>
              );
            })}
          </div>
          {taxonomy.state.error !== null && (
            <p className="text-sm text-warning-text">Filter values could not be loaded. Search still works.</p>
          )}

          {active.length > 0 && (
            <div className="flex flex-wrap items-center gap-2" aria-label="Active filters">
              {active.map(({ key, label }) => (
                <span key={key} className="inline-flex items-center gap-1 rounded-full bg-primary-soft py-1 pl-3 pr-1 text-sm text-primary ring-1 ring-inset ring-primary/20">
                  {label}: <strong className="font-medium">{query[key]}</strong>
                  <button type="button" onClick={() => setFilter(key, "")} aria-label={`Remove ${label} filter`}
                          className="rounded-full p-0.5 hover:bg-primary/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
                    <X className="h-3.5 w-3.5" aria-hidden />
                  </button>
                </span>
              ))}
              <Button variant="ghost" onClick={clearFilters} className="px-2 py-1">Clear filters</Button>
            </div>
          )}
        </div>
      </Card>

      <Card as="div" aria-labelledby="results-heading">
        <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-slate-100 px-5 py-3">
          <h2 id="results-heading" className="text-base font-semibold text-ink">Tickets</h2>
          <p role="status" className="text-sm text-muted">{summary}</p>
        </div>

        {list.loading && !list.items ? (
          <TableSkeleton />
        ) : list.error !== null ? (
          <div className="p-5"><ErrorAlert error={list.error} onRetry={() => setAttempt((a) => a + 1)} /></div>
        ) : items.length === 0 ? (
          list.hiddenByFilters ? (
            <EmptyState title={`${query.q?.toUpperCase()} does not match the active filters`}
                        icon={<SearchX className="h-5 w-5" />}
                        action={<Button onClick={clearFilters}>Clear filters</Button>}>
              The ticket exists, but at least one filter excludes it. Clear the filters to see it.
            </EmptyState>
          ) : (
            <EmptyState title={searching ? "No tickets match your search" : "No tickets match these filters"}
                        icon={<SearchX className="h-5 w-5" />}
                        action={(searching || active.length > 0) && (
                          <Button onClick={clearAll}>Clear search and filters</Button>
                        )}>
              {list.idLookup ? `There is no historical ticket with the ID ${query.q?.toUpperCase()}.`
                             : "Try different words, or remove a filter."}
            </EmptyState>
          )
        ) : (
          <div className={list.loading ? "opacity-60 transition-opacity" : ""} aria-busy={list.loading || undefined}>
            <table className="hidden w-full text-left text-sm md:table">
              <thead className="bg-page text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th scope="col" className="px-5 py-3 font-medium">Ticket ID</th>
                  <th scope="col" className="px-3 py-3 font-medium">Subject</th>
                  <th scope="col" className="px-3 py-3 font-medium">Service</th>
                  <th scope="col" className="px-3 py-3 font-medium">Category</th>
                  <th scope="col" className="px-3 py-3 font-medium">Priority</th>
                  <th scope="col" className="px-3 py-3 font-medium">Status</th>
                  <th scope="col" className="px-5 py-3 font-medium">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {items.map((t) => (
                  <tr key={t.ticket_id} onClick={() => open(t)} className="cursor-pointer hover:bg-slate-50">
                    <td className="whitespace-nowrap px-5 py-3">
                      <Link to={`/analyst/tickets/${encodeURIComponent(t.ticket_id)}`} state={{ from: location.search }}
                            onClick={(e) => e.stopPropagation()}
                            className="font-mono text-[0.8125rem] font-medium text-primary hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
                        {t.ticket_id}
                      </Link>
                    </td>
                    <td className="max-w-xs px-3 py-3">
                      <p className="truncate font-medium text-ink" title={t.subject ?? undefined}>{t.subject ?? "—"}</p>
                      {t.match === "likely_duplicate" && <p className="text-xs text-success-text">Very close match</p>}
                    </td>
                    <td className="px-3 py-3 text-slate-700">{t.affected_service ?? "—"}</td>
                    <td className="px-3 py-3 text-slate-700">
                      {t.category ?? "—"}
                      {t.subcategory && <span className="block text-xs text-muted">{t.subcategory}</span>}
                    </td>
                    <td className="whitespace-nowrap px-3 py-3"><PriorityBadge priority={t.priority} /></td>
                    <td className="whitespace-nowrap px-3 py-3"><StatusBadge status={t.status} /></td>
                    <td className="whitespace-nowrap px-5 py-3 text-slate-700">{formatDate(t.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <ul className="divide-y divide-slate-100 md:hidden" aria-label="Tickets">
              {items.map((t) => (
                <li key={t.ticket_id}>
                  <Link to={`/analyst/tickets/${encodeURIComponent(t.ticket_id)}`} state={{ from: location.search }}
                        className="block px-5 py-4 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary">
                    <span className="flex items-center justify-between gap-2">
                      <span className="font-mono text-xs font-medium text-primary">{t.ticket_id}</span>
                      <span className="text-xs text-muted">{formatDate(t.created_at)}</span>
                    </span>
                    <span className="mt-1 block font-medium text-ink">{t.subject ?? "—"}</span>
                    <span className="mt-0.5 block text-sm text-muted">{[t.affected_service, t.category].filter(Boolean).join(" · ")}</span>
                    <span className="mt-2 flex flex-wrap gap-2"><PriorityBadge priority={t.priority} /><StatusBadge status={t.status} /></span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}
      </Card>
    </>
  );
}
