// One HTTP client for the FastAPI backend. Every response is the {ok, data, error} envelope.
// Errors are normalised into ApiError so screens can show one consistent message and retry.
import type {
  AnalysisResult, ChainStatus, Classification, FeedbackEntry, Finding, HistoryEntry, Recommendation, Envelope, FeedbackRecord, FeedbackRequest, HealthData, LoginData, Preset,
  ResultSummary, SavedResult, Taxonomy, TicketDetail, TicketFeedbackIn, TicketIn, TicketQuery, TicketSummary, User,
} from "./types";
import { OUTCOMES, ROLES } from "./types";
import { readSession, UNAUTHORIZED_EVENT } from "./session";

export type ApiErrorKind = "http" | "network" | "timeout" | "aborted" | "invalid_response";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly code: string;
  readonly requestId: string | null;

  constructor(kind: ApiErrorKind, code: string, message: string, status: number | null = null,
              requestId: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.code = code;
    this.status = status;
    this.requestId = requestId;
  }
}

export const DEFAULT_TIMEOUT_MS = 15_000;

export interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  signal?: AbortSignal;
  timeoutMs?: number;
}

export interface ApiClientConfig {
  baseUrl?: string;
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
  /** The login token to send as Authorization: Bearer (default: the stored session's token). */
  getToken?: () => string | null;
  /** Called when a request that carried a token gets 401 (default: fire UNAUTHORIZED_EVENT). */
  onUnauthorized?: () => void;
}

function defaultUnauthorized() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
}

/** Base URL from VITE_API_BASE_URL; empty means same origin (the Vite dev proxy in development). */
export function configuredBaseUrl(): string {
  const raw = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";
  return raw.trim().replace(/\/+$/, "");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isEnvelope(value: unknown): value is Envelope<unknown> {
  return isRecord(value) && typeof value.ok === "boolean" && "data" in value && "error" in value;
}

export function createApiClient(config: ApiClientConfig = {}) {
  const baseUrl = (config.baseUrl ?? configuredBaseUrl()).replace(/\/+$/, "");
  const defaultTimeout = config.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const doFetch = (...args: Parameters<typeof fetch>) => (config.fetchImpl ?? globalThis.fetch)(...args);
  const getToken = config.getToken ?? (() => readSession()?.token ?? null);
  const onUnauthorized = config.onUnauthorized ?? defaultUnauthorized;

  async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, options.timeoutMs ?? defaultTimeout);
    const onOuterAbort = () => controller.abort();
    if (options.signal) {
      if (options.signal.aborted) controller.abort();
      else options.signal.addEventListener("abort", onOuterAbort, { once: true });
    }

    const token = getToken();
    const headers: Record<string, string> = { Accept: "application/json" };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    if (token) headers.Authorization = `Bearer ${token}`;

    let response: Response;
    try {
      response = await doFetch(`${baseUrl}${path}`, {
        method: options.method ?? "GET",
        headers,
        body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
        signal: controller.signal,
      });
    } catch {
      if (timedOut) throw new ApiError("timeout", "TIMEOUT", "The request timed out.");
      if (controller.signal.aborted) throw new ApiError("aborted", "ABORTED", "The request was cancelled.");
      throw new ApiError("network", "NETWORK_ERROR", "Cannot reach the backend.");
    } finally {
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", onOuterAbort);
    }

    const requestId = response.headers.get("X-Request-ID");
    if (response.status === 401 && token) onUnauthorized();
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      if (!response.ok) {
        throw new ApiError("http", `HTTP_${response.status}`, `The server answered HTTP ${response.status}.`,
                           response.status, requestId);
      }
      throw new ApiError("invalid_response", "INVALID_JSON", "The server sent a response that is not valid JSON.",
                         response.status, requestId);
    }

    if (!isEnvelope(body)) {
      if (!response.ok) {
        throw new ApiError("http", `HTTP_${response.status}`, `The server answered HTTP ${response.status}.`,
                           response.status, requestId);
      }
      throw new ApiError("invalid_response", "INVALID_ENVELOPE", "The server response has an unexpected shape.",
                         response.status, requestId);
    }
    if (!body.ok || !response.ok) {
      const code = body.error?.code ?? `HTTP_${response.status}`;
      const message = body.error?.message ?? `The server answered HTTP ${response.status}.`;
      throw new ApiError("http", code, message, response.status, requestId);
    }
    return body.data as T;
  }

  return { request, baseUrl };
}

export type ApiClient = ReturnType<typeof createApiClient>;

// ---------- response checks (light, so a changed backend fails loudly instead of rendering garbage) ----------

function invalid(what: string): never {
  throw new ApiError("invalid_response", "INVALID_RESPONSE", `The server sent an unexpected ${what}.`);
}

export function assertHealth(data: unknown): HealthData {
  if (!isRecord(data) || typeof data.api !== "string" || typeof data.database !== "string"
      || typeof data.warmup !== "string") invalid("health status");
  return data as unknown as HealthData;
}

export function assertResults(data: unknown): ResultSummary[] {
  if (!Array.isArray(data) || !data.every((r) => isRecord(r) && typeof r.result_id === "string")) {
    invalid("result list");
  }
  return data as ResultSummary[];
}

export function assertResult(data: unknown): SavedResult {
  if (!isRecord(data) || typeof data.result_id !== "string" || typeof data.case_id !== "string") invalid("result");
  return data as SavedResult;
}

export function assertFeedbackList(data: unknown): FeedbackRecord[] {
  if (!Array.isArray(data)) invalid("feedback list");
  return data as FeedbackRecord[];
}

export function assertChain(data: unknown): ChainStatus {
  if (!isRecord(data) || typeof data.valid !== "boolean") invalid("audit chain status");
  return data as unknown as ChainStatus;
}

export function assertLogin(data: unknown): LoginData {
  if (!isRecord(data) || typeof data.token !== "string" || !data.token || typeof data.expires_at !== "string"
      || !isRecord(data.user) || typeof data.user.username !== "string"
      || !ROLES.includes(data.user.role as never)) invalid("login response");
  return data as unknown as LoginData;
}

export function assertAnalysis(data: unknown): AnalysisResult {
  if (!isRecord(data) || typeof data.result_id !== "string" || !OUTCOMES.includes(data.outcome as never)
      || !Array.isArray(data.similar) || !Array.isArray(data.kb) || !Array.isArray(data.reasons)
      || typeof data.confidence !== "number") invalid("analysis result");
  return data as unknown as AnalysisResult;
}

export function assertPresets(data: unknown): Preset[] {
  if (!Array.isArray(data) || !data.every((p) => isRecord(p) && typeof p.id === "string" && isRecord(p.ticket))) {
    invalid("preset list");
  }
  return data as Preset[];
}

export function assertTaxonomy(data: unknown): Taxonomy {
  if (!isRecord(data) || !Array.isArray(data.affected_service) || !Array.isArray(data.category)) invalid("taxonomy");
  return data as unknown as Taxonomy;
}

export function assertTicketList(data: unknown): TicketSummary[] {
  if (!Array.isArray(data) || !data.every((t) => isRecord(t) && typeof t.ticket_id === "string")) invalid("ticket list");
  return data as TicketSummary[];
}

export function assertTicket(data: unknown): TicketDetail {
  if (!isRecord(data) || typeof data.ticket_id !== "string") invalid("ticket");
  return data as unknown as TicketDetail;
}

// ---------- Review history adapter: GET /api/tickets/history ----------
// PROVISIONAL CONTRACT. db/repo.history() (Raswanth) is not merged; until it is, the route answers 503
// DB_NOT_READY. HistoryRecordApi is our proposal, built only from the documented table columns in
// docs/ARCHITECTURE.md ("Database": analysis_results, feedback, resolutions) with their own names.
// Backend field names stay in this block: when the real response is known, change HistoryRecordApi and
// toHistoryEntry() only. Pages use HistoryEntry (lib/types.ts).

/** One feedback row as the backend is expected to send it (table `feedback`). */
export interface HistoryFeedbackApi {
  verdict: string;
  note_masked?: string | null;
  reviewer_id?: string | null;
  target?: "result" | "match" | null;
  target_id?: string | null;
  created_at?: string | null;
}

/** One saved analysis as the backend is expected to send it (tables analysis_results + feedback + resolutions). */
export interface HistoryRecordApi {
  result_id: string;
  created_at: string;
  recommended_at?: string | null;
  user_id?: string | null;
  ticket_masked: { subject: string; description?: string | null; affected_service?: string | null;
                   environment?: string | null; channel?: string | null };
  classification?: Classification | null;
  evidence_ids?: string[] | null;
  outcome: string;
  reasons?: string[] | null;
  confidence?: number | null;
  recommendation?: Recommendation | null;
  findings?: Finding[] | null;
  generator?: string | null;
  model_version?: string | null;
  prompt_version?: string | null;
  feedback?: HistoryFeedbackApi[] | null;
  resolution?: { resolved_by?: string | null; resolved_at: string } | null;
}

const VERDICTS = ["useful", "incorrect", "unresolved"];

/** null/undefined -> null; a string stays; anything else is malformed. */
function optText(value: unknown, what: string): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") invalid(what);
  return value;
}

/** null/undefined -> []; an array whose items pass `ok` stays; anything else is malformed. */
function optList<T>(value: unknown, ok: (item: unknown) => boolean, what: string): T[] {
  if (value === null || value === undefined) return [];
  if (!Array.isArray(value) || !value.every(ok)) invalid(what);
  return value as T[];
}

function toFeedbackEntry(row: unknown): FeedbackEntry {
  if (!isRecord(row) || !VERDICTS.includes(row.verdict as string)) invalid("feedback entry");
  const target = row.target ?? "result";
  if (target !== "result" && target !== "match") invalid("feedback entry");
  return {
    verdict: row.verdict as FeedbackEntry["verdict"],
    note: optText(row.note_masked, "feedback note"),
    reviewer: optText(row.reviewer_id, "feedback reviewer"),
    at: optText(row.created_at, "feedback time"),
    aboutTicketId: target === "match" ? optText(row.target_id, "feedback target") : null,
  };
}

/** The adapter: one backend history record -> HistoryEntry. Throws INVALID_RESPONSE for malformed data. */
export function toHistoryEntry(raw: unknown): HistoryEntry {
  if (!isRecord(raw) || typeof raw.result_id !== "string" || !raw.result_id || typeof raw.created_at !== "string"
      || !OUTCOMES.includes(raw.outcome as never)) invalid("history record");
  const t = raw.ticket_masked;
  if (!isRecord(t) || typeof t.subject !== "string") invalid("history record");
  const conf = raw.confidence;
  if (conf !== null && conf !== undefined && (typeof conf !== "number" || Number.isNaN(conf) || conf < 0 || conf > 1)) {
    invalid("confidence");
  }
  for (const key of ["classification", "recommendation"] as const) {
    if (raw[key] !== null && raw[key] !== undefined && !isRecord(raw[key])) invalid(key);
  }
  if (isRecord(raw.recommendation) && !Array.isArray(raw.recommendation.steps)) invalid("recommendation");
  const res = raw.resolution;
  if (res !== null && res !== undefined && (!isRecord(res) || typeof res.resolved_at !== "string")) invalid("resolution");

  return {
    id: raw.result_id,
    analysedAt: raw.created_at,
    finishedAt: optText(raw.recommended_at, "recommended_at"),
    analyst: optText(raw.user_id, "user_id"),
    ticket: {
      subject: t.subject,
      description: optText(t.description, "ticket description"),
      service: optText(t.affected_service, "affected_service"),
      environment: optText(t.environment, "environment"),
      channel: optText(t.channel, "channel"),
    },
    outcome: raw.outcome as HistoryEntry["outcome"],
    reasons: optList<string>(raw.reasons, (r) => typeof r === "string", "reasons"),
    confidence: typeof conf === "number" ? conf : null, // missing stays missing: never shown as 0%
    classification: (raw.classification ?? null) as Classification | null,
    recommendation: (raw.recommendation ?? null) as Recommendation | null,
    findings: optList<Finding>(raw.findings, isRecord, "findings"),
    evidence: optList<string>(raw.evidence_ids, (r) => typeof r === "string", "evidence_ids"),
    generator: optText(raw.generator, "generator"),
    classifierVersion: optText(raw.model_version, "model_version"),
    promptVersion: optText(raw.prompt_version, "prompt_version"),
    feedback: optList<unknown>(raw.feedback, () => true, "feedback").map(toFeedbackEntry),
    resolution: isRecord(res) ? { resolvedAt: res.resolved_at as string,
                                  resolvedBy: optText(res.resolved_by, "resolved_by") } : null,
  };
}

/** GET /api/tickets/history data -> HistoryEntry[] (every record through the adapter). */
export function assertHistory(data: unknown): HistoryEntry[] {
  if (!Array.isArray(data)) invalid("history list");
  return data.map(toHistoryEntry);
}

/** "?q=vpn&priority=P1" from the set values only (empty strings are left out). */
export function ticketQueryString(query: TicketQuery): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (typeof value === "string" && value.trim()) params.set(key, value.trim());
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

// ---------- endpoints ----------

export const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;

export function makeEndpoints(client: ApiClient) {
  const id = (value: string) => encodeURIComponent(value);
  return {
    health: (signal?: AbortSignal) => client.request<unknown>("/health", { signal }).then(assertHealth),
    ready: (signal?: AbortSignal) => client.request<unknown>("/health/ready", { signal }).then(assertHealth),
    verifyAudit: (signal?: AbortSignal) =>
      client.request<unknown>("/api/audit/verify", { signal }).then(assertChain),
    results: (signal?: AbortSignal) =>
      client.request<unknown>("/api/audit/results", { signal }).then(assertResults),
    result: (resultId: string, signal?: AbortSignal) =>
      client.request<unknown>(`/api/audit/results/${id(resultId)}`, { signal }).then(assertResult),
    feedback: (resultId: string, signal?: AbortSignal) =>
      client.request<unknown>(`/api/audit/results/${id(resultId)}/feedback`, { signal }).then(assertFeedbackList),
    addFeedback: (resultId: string, body: FeedbackRequest, signal?: AbortSignal) =>
      client.request<FeedbackRecord>(`/api/audit/results/${id(resultId)}/feedback`, { method: "POST", body, signal }),

    // Auth (backend/app/auth/router.py)
    login: (username: string, password: string, signal?: AbortSignal) =>
      client.request<unknown>("/api/auth/login", { method: "POST", body: { username, password }, signal })
        .then(assertLogin),
    me: (signal?: AbortSignal) => client.request<User>("/api/auth/me", { signal }),

    // Tickets (backend/app/api/tickets.py). The analysis runs the whole pipeline, so it gets a longer timeout.
    analyze: (ticket: TicketIn, signal?: AbortSignal) =>
      client.request<unknown>("/api/tickets/analyze", { method: "POST", body: ticket, signal, timeoutMs: 60_000 })
        .then(assertAnalysis),
    presets: (signal?: AbortSignal) => client.request<unknown>("/api/tickets/presets", { signal }).then(assertPresets),
    taxonomy: (signal?: AbortSignal) =>
      client.request<unknown>("/api/tickets/taxonomy", { signal }).then(assertTaxonomy),
    searchTickets: (query: TicketQuery, signal?: AbortSignal) =>
      client.request<unknown>(`/api/tickets${ticketQueryString(query)}`, { signal }).then(assertTicketList),
    history: (limit: number, offset: number, signal?: AbortSignal) =>
      client.request<unknown>(`/api/tickets/history?limit=${limit}&offset=${offset}`, { signal }).then(assertHistory),
    ticket: (ticketId: string, signal?: AbortSignal) =>
      client.request<unknown>(`/api/tickets/${id(ticketId)}`, { signal }).then(assertTicket),
    ticketFeedback: (resultId: string, body: TicketFeedbackIn, signal?: AbortSignal) =>
      client.request<unknown>(`/api/tickets/results/${id(resultId)}/feedback`, { method: "POST", body, signal }),
  };
}

export type Endpoints = ReturnType<typeof makeEndpoints>;

// ---------- user-facing messages ----------

export interface ErrorDescription {
  title: string;
  message: string;
  retryable: boolean;
}

export function describeError(error: unknown): ErrorDescription {
  if (!(error instanceof ApiError)) {
    return { title: "Something went wrong", message: "An unexpected error occurred.", retryable: true };
  }
  switch (error.kind) {
    case "network":
      return { title: "Backend unreachable",
               message: "Cannot reach the backend. Check that the FastAPI server is running.",
               retryable: true };
    case "timeout":
      return { title: "Request timed out", message: "The server took too long to answer.", retryable: true };
    case "aborted":
      return { title: "Request cancelled", message: "The request was cancelled.", retryable: true };
    case "invalid_response":
      return { title: "Unexpected response", message: error.message, retryable: true };
  }
  switch (error.code) {
    case "WARMING_UP":
      return { title: "Still warming up", message: "The backend is still loading. Try again in a moment.", retryable: true };
    case "WARMUP_FAILED":
      return { title: "Warm-up failed", message: error.message, retryable: true };
    case "RESULT_NOT_FOUND":
      return { title: "Result not found", message: "No saved result has this ID.", retryable: false };
    case "DATABASE_UNAVAILABLE":
      return { title: "Database unavailable", message: "The database is not available right now.", retryable: true };
    case "DB_NOT_READY":
      return { title: "Saving not available yet",
               message: "The results database is not connected yet, so this could not be saved.", retryable: false };
    case "INVALID_LOGIN":
      return { title: "Sign-in failed", message: "Wrong username or password.", retryable: false };
    case "TOO_MANY_ATTEMPTS":
      return { title: "Too many attempts", message: "Too many wrong tries. Wait a few minutes and try again.",
               retryable: false };
    case "AUTH_NOT_CONFIGURED":
      return { title: "Sign-in unavailable", message: "Sign-in is not set up on the server yet.", retryable: false };
    case "UNAUTHORIZED":
      return { title: "Please sign in", message: "Your session has ended. Sign in again to continue.", retryable: false };
    case "EMPLOYEE_NOT_ALLOWED":
      return { title: "Staff sign-in only",
               message: "Employee access is not available yet. Please use the employee support portal.",
               retryable: false };
    case "FORBIDDEN":
      return { title: "Not allowed", message: "Your role cannot open this page.", retryable: false };
    case "TICKET_NOT_FOUND":
      return { title: "Ticket not found", message: "No historical ticket has this ID.", retryable: false };
    case "ANALYSIS_FAILED":
      return { title: "Analysis failed", message: "The ticket could not be analysed. Try again.", retryable: true };
  }
  if (error.status === 400) {
    return { title: "Check the form", message: error.message, retryable: false };
  }
  if (error.status === 422) {
    return { title: "Invalid request", message: error.message, retryable: false };
  }
  if (error.status === 404) return { title: "Not found", message: error.message, retryable: false };
  if (error.status !== null && error.status >= 500) {
    return { title: "Server error", message: "The server hit an unexpected error. Try again.", retryable: true };
  }
  return { title: "Request failed", message: error.message, retryable: true };
}
