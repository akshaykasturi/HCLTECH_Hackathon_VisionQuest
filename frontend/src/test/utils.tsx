// Controlled HTTP mock for the FastAPI backend + a render helper for the whole app.
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { AppProviders, AppRoutes } from "../App";
import type { Endpoints } from "../lib/api";
import { HEALTH_OK, LOGIN_OK, PRESETS, TAXONOMY } from "./fixtures";
import { SESSION_KEY } from "../lib/session";
import type { User } from "../lib/types";

export type Reply = { status?: number; body?: unknown; raw?: string; delayMs?: number } | "network" | "hang";
export type Handler = Reply | ((req: { method: string; path: string; body: unknown }) => Reply | Promise<Reply>);

export const ok = (data: unknown, status = 200): Reply => ({ status, body: { ok: true, data, error: null } });
export const fail = (status: number, code: string, message = "error"): Reply =>
  ({ status, body: { ok: false, data: null, error: { code, message } } });

export function defaultRoutes(): Record<string, Handler> {
  return {
    "GET /health": ok(HEALTH_OK),
    "GET /health/ready": ok(HEALTH_OK),
    "GET /api/audit/results": ok([]),
  };
}

/** Routes the Analyze page loads on open (demo tickets and service names). */
export function analystRoutes(): Record<string, Handler> {
  return { "GET /api/tickets/presets": ok(PRESETS), "GET /api/tickets/taxonomy": ok(TAXONOMY) };
}

/** Replace global fetch with a router over "METHOD /path" keys. Unknown routes answer 404 in the envelope. */
export function mockBackend(routes: Record<string, Handler> = {}) {
  const table = { ...defaultRoutes(), ...routes };
  const calls: { method: string; path: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://127.0.0.1:5173");
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    const req = { method, path: url.pathname, body };
    calls.push(req);
    const handler = table[`${method} ${url.pathname}`];
    const reply: Reply = handler === undefined ? fail(404, "NOT_FOUND", "Not Found")
      : typeof handler === "function" ? await handler(req) : handler;
    if (reply === "network") throw new TypeError("Failed to fetch");
    if (reply === "hang") {
      return new Promise<Response>((_, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      });
    }
    if (reply.delayMs) await new Promise((r) => setTimeout(r, reply.delayMs));
    return new Response(reply.raw ?? JSON.stringify(reply.body), {
      status: reply.status ?? 200,
      headers: { "Content-Type": "application/json", "X-Request-ID": "test-request-0001" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    calls,
    fetchMock,
    callsTo: (method: string, path: string) => calls.filter((c) => c.method === method && c.path === path),
  };
}

/** Start the test signed in (what a successful POST /api/auth/login leaves in sessionStorage). */
export function signIn(user: User = LOGIN_OK.user, token = LOGIN_OK.token) {
  sessionStorage.setItem(SESSION_KEY, JSON.stringify({ token, expiresAt: LOGIN_OK.expires_at, user }));
}

export function renderApp(path = "/", options: { endpoints?: Endpoints } = {}) {
  const user = userEvent.setup();
  const view = render(
    <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AppProviders endpoints={options.endpoints}><AppRoutes /></AppProviders>
    </MemoryRouter>,
  );
  return { user, ...view };
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}
