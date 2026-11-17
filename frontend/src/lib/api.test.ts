import { describe, expect, it, vi } from "vitest";
import {
  ApiError, assertAnalysis, assertHealth, assertHistory, assertLogin, assertResult, createApiClient, describeError,
  makeEndpoints, toHistoryEntry,
} from "./api";
import { HISTORY_OPEN, HISTORY_RESOLVED } from "../test/fixtures";
import { HEALTH_OK } from "../test/fixtures";

function fetchReturning(status: number, body: string | object, headers: Record<string, string> = {}) {
  return vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => new Response(typeof body === "string" ? body : JSON.stringify(body),
                                         { status, headers: { "Content-Type": "application/json", ...headers } }));
}

async function caught(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (e) {
    expect(e).toBeInstanceOf(ApiError);
    return e as ApiError;
  }
  throw new Error("expected the request to fail");
}

describe("API client", () => {
  it("returns data from a successful envelope", async () => {
    const fetchImpl = fetchReturning(200, { ok: true, data: [1, 2], error: null });
    const client = createApiClient({ baseUrl: "", fetchImpl });
    await expect(client.request("/api/cases")).resolves.toEqual([1, 2]);
  });

  it("uses the configured base URL and strips trailing slashes", async () => {
    const fetchImpl = fetchReturning(200, { ok: true, data: null, error: null });
    await createApiClient({ baseUrl: "http://127.0.0.1:8000/", fetchImpl }).request("/health");
    expect(fetchImpl).toHaveBeenCalledWith("http://127.0.0.1:8000/health", expect.objectContaining({ method: "GET" }));
  });

  it("sends JSON bodies for POST", async () => {
    const fetchImpl = fetchReturning(201, { ok: true, data: {}, error: null });
    await createApiClient({ baseUrl: "", fetchImpl }).request("/api/items", { method: "POST", body: { case_id: "CASE-0001" } });
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.body).toBe('{"case_id":"CASE-0001"}');
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("turns an error envelope into ApiError with code, status and request id", async () => {
    const fetchImpl = fetchReturning(503, { ok: false, data: null, error: { code: "WARMING_UP", message: "not ready" } },
                                     { "X-Request-ID": "req-123" });
    const err = await caught(createApiClient({ baseUrl: "", fetchImpl }).request("/health/ready"));
    expect(err).toMatchObject({ kind: "http", status: 503, code: "WARMING_UP", requestId: "req-123" });
    expect(describeError(err).title).toBe("Still warming up");
  });

  it.each([
    [404, "RESULT_NOT_FOUND", "Result not found"],
    [404, "NOT_FOUND", "Not found"],
    [422, "VALIDATION_ERROR", "Invalid request"],
    [500, "INTERNAL_ERROR", "Server error"],
    [503, "DATABASE_UNAVAILABLE", "Database unavailable"],
    [503, "WARMUP_FAILED", "Warm-up failed"],
  ])("maps HTTP %i %s to a clear message", async (status, code, title) => {
    const fetchImpl = fetchReturning(status, { ok: false, data: null, error: { code, message: "server text" } });
    const err = await caught(createApiClient({ baseUrl: "", fetchImpl }).request("/x"));
    expect(err.status).toBe(status);
    expect(describeError(err).title).toBe(title);
  });

  it("handles an HTTP error without an envelope", async () => {
    const err = await caught(createApiClient({ baseUrl: "", fetchImpl: fetchReturning(500, "<html>oops</html>") }).request("/x"));
    expect(err).toMatchObject({ kind: "http", status: 500, code: "HTTP_500" });
  });

  it("rejects invalid JSON on success", async () => {
    const err = await caught(createApiClient({ baseUrl: "", fetchImpl: fetchReturning(200, "{not json") }).request("/x"));
    expect(err).toMatchObject({ kind: "invalid_response", code: "INVALID_JSON" });
  });

  it("rejects a 200 response that is not an envelope", async () => {
    const err = await caught(createApiClient({ baseUrl: "", fetchImpl: fetchReturning(200, { cases: [] }) }).request("/x"));
    expect(err.code).toBe("INVALID_ENVELOPE");
  });

  it("reports network failures", async () => {
    const fetchImpl = vi.fn(async () => { throw new TypeError("Failed to fetch"); });
    const err = await caught(createApiClient({ baseUrl: "", fetchImpl }).request("/x"));
    expect(err.kind).toBe("network");
    expect(describeError(err).title).toBe("Backend unreachable");
  });

  function hangingFetch() {
    return vi.fn((_url: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    }));
  }

  it("times out", async () => {
    const err = await caught(createApiClient({ baseUrl: "", fetchImpl: hangingFetch(), timeoutMs: 20 }).request("/x"));
    expect(err).toMatchObject({ kind: "timeout", code: "TIMEOUT" });
  });

  it("can be cancelled with an AbortSignal", async () => {
    const ctl = new AbortController();
    const pending = createApiClient({ baseUrl: "", fetchImpl: hangingFetch() }).request("/x", { signal: ctl.signal });
    ctl.abort();
    expect((await caught(pending)).kind).toBe("aborted");
  });

  it("health checks the shape it gets", async () => {
    const fetchImpl = fetchReturning(200, { ok: true, data: HEALTH_OK, error: null });
    await expect(makeEndpoints(createApiClient({ baseUrl: "", fetchImpl })).health()).resolves.toEqual(HEALTH_OK);
    expect(() => assertHealth({ api: "up" })).toThrow(ApiError);
  });

  it("rejects a saved result without ids", () => {
    expect(assertResult({ result_id: "r-1", case_id: "C-1", anything: 1 })).toMatchObject({ result_id: "r-1" });
    expect(() => assertResult({ result_id: "r-1" })).toThrow(ApiError);
  });

  it("encodes ids in paths", async () => {
    const fetchImpl = fetchReturning(200, { ok: true, data: [], error: null });
    await makeEndpoints(createApiClient({ baseUrl: "", fetchImpl })).feedback("a/b");
    expect(fetchImpl.mock.calls[0][0]).toBe("/api/audit/results/a%2Fb/feedback");
  });
});

describe("login token", () => {
  it("sends the token as a Bearer header when there is one", async () => {
    const fetchImpl = fetchReturning(200, { ok: true, data: null, error: null });
    await createApiClient({ baseUrl: "", fetchImpl, getToken: () => "tok-1" }).request("/api/tickets/presets");
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok-1");
  });

  it("sends no Authorization header when signed out", async () => {
    const fetchImpl = fetchReturning(200, { ok: true, data: null, error: null });
    await createApiClient({ baseUrl: "", fetchImpl, getToken: () => null }).request("/health");
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it("reports a 401 on a request that carried a token", async () => {
    const onUnauthorized = vi.fn();
    const fetchImpl = fetchReturning(401, { ok: false, data: null, error: { code: "UNAUTHORIZED", message: "expired" } });
    const err = await caught(createApiClient({ baseUrl: "", fetchImpl, getToken: () => "old", onUnauthorized })
      .request("/api/tickets/analyze", { method: "POST", body: {} }));
    expect(err.code).toBe("UNAUTHORIZED");
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("does not report a wrong password as an expired session", async () => {
    const onUnauthorized = vi.fn();
    const fetchImpl = fetchReturning(401, { ok: false, data: null, error: { code: "INVALID_LOGIN", message: "x" } });
    await caught(createApiClient({ baseUrl: "", fetchImpl, getToken: () => null, onUnauthorized })
      .request("/api/auth/login", { method: "POST", body: {} }));
    expect(onUnauthorized).not.toHaveBeenCalled();
  });
});

describe("ticket response checks", () => {
  it("accepts a well-formed analysis and rejects unknown outcomes", () => {
    const good = { result_id: "r", outcome: "CLARIFY", similar: [], kb: [], reasons: [], confidence: 0.4 };
    expect(assertAnalysis(good).outcome).toBe("CLARIFY");
    expect(() => assertAnalysis({ ...good, outcome: "MAYBE" })).toThrow(ApiError);
    expect(() => assertAnalysis({ ...good, similar: null })).toThrow(ApiError);
  });

  it("rejects a login response without a token or a known role", () => {
    const good = { token: "t", token_type: "bearer", expires_at: "2099-01-01T00:00:00Z", user: { username: "a", role: "lead" } };
    expect(assertLogin(good).user.role).toBe("lead");
    // employee is a valid backend role (models.Role): not a malformed response
    expect(assertLogin({ ...good, user: { username: "e", role: "employee" } }).user.role).toBe("employee");
    expect(assertLogin({ ...good, user: { username: "a", role: "analyst" } }).user.role).toBe("analyst");
    expect(() => assertLogin({ ...good, token: "" })).toThrow(ApiError);
    expect(() => assertLogin({ ...good, user: { username: "a", role: "admin" } })).toThrow(ApiError);
  });

  it("gives friendly messages for the new backend error codes", () => {
    expect(describeError(new ApiError("http", "DB_NOT_READY", "x", 503)).retryable).toBe(false);
    expect(describeError(new ApiError("http", "INVALID_LOGIN", "x", 401)).title).toBe("Sign-in failed");
    expect(describeError(new ApiError("http", "VALIDATION_ERROR", "subject: too short", 400)).title).toBe("Check the form");
  });
});

describe("history adapter (toHistoryEntry)", () => {
  const BACKEND_NAMES = ["result_id", "created_at", "recommended_at", "user_id", "ticket_masked", "evidence_ids",
                         "model_version", "prompt_version", "note_masked", "reviewer_id", "target_id", "resolved_by",
                         "resolved_at", "affected_service"];
  // Every key in the result, except inside the shared Analyze shapes (classification, recommendation, findings).
  const allKeys = (v: unknown): string[] => (v && typeof v === "object"
    ? Object.entries(v as Record<string, unknown>).flatMap(([k, x]) =>
        ["classification", "recommendation", "findings"].includes(k) ? [k] : [k, ...allKeys(x)])
    : []);

  it("maps a full backend record to the frontend shape", () => {
    const e = toHistoryEntry(HISTORY_RESOLVED);
    expect(e.id).toBe(HISTORY_RESOLVED.result_id);
    expect(e.analysedAt).toBe(HISTORY_RESOLVED.created_at);
    expect(e.analyst).toBe("asha");
    expect(e.ticket).toEqual({ subject: "VPN drops every few minutes", description: HISTORY_RESOLVED.ticket_masked.description,
                               service: "Corporate VPN", environment: "production", channel: "portal" });
    expect(e.evidence).toEqual(["INC-100045", "doc:KB-004#4"]);
    expect(e.confidence).toBe(0.86);
    expect(e.classifierVersion).toBe("tfidf-v1");
    expect(e.feedback).toEqual([
      { verdict: "useful", note: "Step 1 fixed it", reviewer: "asha", at: "2026-11-16T09:10:00+00:00", aboutTicketId: null },
      { verdict: "incorrect", note: null, reviewer: "ravi", at: "2026-11-16T10:00:00+00:00", aboutTicketId: "INC-100777" },
    ]);
    expect(e.resolution).toEqual({ resolvedAt: "2026-11-16T12:20:00+00:00", resolvedBy: "asha" });
  });

  it("keeps backend field names out of what it returns", () => {
    const keys = allKeys(toHistoryEntry(HISTORY_RESOLVED));
    expect(keys.filter((k) => BACKEND_NAMES.includes(k))).toEqual([]);
  });

  it("turns null or missing lists into empty lists and keeps valid values", () => {
    const e = toHistoryEntry({ ...HISTORY_RESOLVED, evidence_ids: null, findings: null, reasons: null, feedback: null });
    expect([e.evidence, e.findings, e.reasons, e.feedback]).toEqual([[], [], [], []]);
    const { evidence_ids: _a, findings: _b, reasons: _c, feedback: _d, resolution: _e, ...lean } = HISTORY_RESOLVED;
    void [_a, _b, _c, _d, _e];
    const m = toHistoryEntry(lean);
    expect([m.evidence, m.findings, m.reasons, m.feedback, m.resolution]).toEqual([[], [], [], [], null]);
    expect(toHistoryEntry(HISTORY_RESOLVED).reasons).toEqual(["STRONG_MATCH"]);
  });

  it("keeps a missing confidence missing (never 0) and keeps a real 0", () => {
    const { confidence: _c, ...noConf } = HISTORY_RESOLVED; void _c;
    expect(toHistoryEntry(noConf).confidence).toBeNull();
    expect(toHistoryEntry({ ...HISTORY_RESOLVED, confidence: null }).confidence).toBeNull();
    expect(toHistoryEntry({ ...HISTORY_RESOLVED, confidence: 0 }).confidence).toBe(0);
  });

  it("handles optional feedback fields and a missing resolution", () => {
    const e = toHistoryEntry(HISTORY_OPEN);
    expect(e.feedback).toEqual([{ verdict: "unresolved", note: null, reviewer: null, at: null, aboutTicketId: null }]);
    expect(e.resolution).toBeNull();
    expect(e.ticket.service).toBeNull();
  });

  it.each([
    ["a list that is not a list", { evidence_ids: "INC-1" }],
    ["a list with wrong items", { reasons: [1, 2] }],
    ["an unknown outcome", { outcome: "MAYBE" }],
    ["an unknown feedback verdict", { feedback: [{ verdict: "great" }] }],
    ["a feedback target that is not result or match", { feedback: [{ verdict: "useful", target: "user" }] }],
    ["a confidence that is not a probability", { confidence: "high" }],
    ["a confidence above 1", { confidence: 86 }],
    ["a resolution without a time", { resolution: { resolved_by: "asha" } }],
    ["a ticket without a subject", { ticket_masked: { description: "x" } }],
    ["a missing result id", { result_id: "" }],
    ["a number where a name should be", { user_id: 42 }],
  ])("rejects %s", (_name, patch) => {
    expect(() => toHistoryEntry({ ...HISTORY_RESOLVED, ...patch })).toThrow(ApiError);
  });

  it("checks every record in a history list", () => {
    expect(assertHistory([HISTORY_RESOLVED, HISTORY_OPEN]).map((e) => e.id)).toEqual([HISTORY_RESOLVED.result_id, "res-clarify-0002"]);
    expect(() => assertHistory([HISTORY_RESOLVED, { result_id: "x" }])).toThrow(ApiError);
    expect(() => assertHistory({ items: [] })).toThrow(ApiError);
  });
});
