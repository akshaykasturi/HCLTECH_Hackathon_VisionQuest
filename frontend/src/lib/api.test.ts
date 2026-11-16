import { describe, expect, it, vi } from "vitest";
import { ApiError, assertHealth, assertResult, createApiClient, describeError, makeEndpoints } from "./api";
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
    [503, "DATABASE_UNAVAILABLE", "Audit database unavailable"],
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
