// One HTTP client for the FastAPI backend. Every response is the {ok, data, error} envelope.
// Errors are normalised into ApiError so screens can show one consistent message and retry.
import type {
  AnalysisResult, ChainStatus, Envelope, FeedbackRecord, FeedbackRequest, HealthData, LoginData, Preset,
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
