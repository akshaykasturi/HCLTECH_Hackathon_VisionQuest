import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { SESSION_KEY } from "../lib/session";
import { LOGIN_OK } from "./fixtures";
import { analystRoutes, deferred, fail, mockBackend, ok, renderApp, type Reply } from "./utils";

describe("login", () => {
  it("asks for both fields before calling the backend", async () => {
    const backend = mockBackend();
    const { user } = renderApp("/login");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter your username and password.");
    expect(backend.callsTo("POST", "/api/auth/login")).toHaveLength(0);
  });

  it("signs in, keeps the token in sessionStorage only, and opens the requested console page", async () => {
    const gate = deferred<Reply>();
    const backend = mockBackend({ ...analystRoutes(), "POST /api/auth/login": () => gate.promise });
    const { user } = renderApp("/analyst/analyze");
    await user.type(screen.getByLabelText("Username"), "asha");
    await user.type(screen.getByLabelText("Password"), "s3cret-pass");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByRole("button", { name: /Signing in/ })).toBeDisabled();
    gate.resolve(ok(LOGIN_OK));

    expect(await screen.findByRole("heading", { level: 1, name: "Analyze a ticket" })).toBeInTheDocument();
    expect(backend.callsTo("POST", "/api/auth/login")[0].body).toEqual({ username: "asha", password: "s3cret-pass" });
    const stored = JSON.parse(sessionStorage.getItem(SESSION_KEY) ?? "{}");
    expect(stored.token).toBe(LOGIN_OK.token);
    expect(JSON.stringify(stored)).not.toContain("s3cret-pass"); // the password is never stored
    expect(localStorage.length).toBe(0);
  });

  it("shows a friendly message for a wrong password and clears the password field", async () => {
    mockBackend({ "POST /api/auth/login": fail(401, "INVALID_LOGIN", "Wrong username or password.") });
    const { user } = renderApp("/login");
    await user.type(screen.getByLabelText("Username"), "asha");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Sign-in failed")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(sessionStorage.length).toBe(0);
  });

  it("explains when sign-in is locked after too many tries", async () => {
    mockBackend({ "POST /api/auth/login": fail(429, "TOO_MANY_ATTEMPTS", "x") });
    const { user } = renderApp("/login");
    await user.type(screen.getByLabelText("Username"), "asha");
    await user.type(screen.getByLabelText("Password"), "x");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Too many attempts")).toBeInTheDocument();
  });
});

describe("roles", () => {
  async function signInAs(role: "employee" | "analyst" | "lead", token = `tok-${role}`) {
    const backend = mockBackend({ ...analystRoutes(),
      "POST /api/auth/login": ok({ ...LOGIN_OK, token, user: { username: `${role}-user`, role } }) });
    const view = renderApp("/analyst/analyze");
    await view.user.type(screen.getByLabelText("Username"), `${role}-user`);
    await view.user.type(screen.getByLabelText("Password"), "pass-123");
    await view.user.click(screen.getByRole("button", { name: "Sign in" }));
    return { backend, ...view };
  }

  it("refuses an employee at the staff sign-in and stores nothing", async () => {
    const { backend } = await signInAs("employee");
    expect(await screen.findByText("Employee access is not available yet. Please use the employee support portal."))
      .toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "IT staff sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Main navigation" })).not.toBeInTheDocument();
    expect(sessionStorage.length).toBe(0); // the employee token is never kept
    expect(localStorage.length).toBe(0);
    expect(screen.getByLabelText("Password")).toHaveValue("");
    // nothing protected was requested with the employee's token
    expect(backend.calls.filter((c) => c.path.startsWith("/api/tickets"))).toHaveLength(0);
  });

  it("lets an analyst into the console and sends the analyst's token", async () => {
    const { backend } = await signInAs("analyst");
    expect(await screen.findByRole("heading", { level: 1, name: "Analyze a ticket" })).toBeInTheDocument();
    expect(screen.getByText("analyst-user")).toBeInTheDocument();
    await screen.findByLabelText("Load a demo ticket");
    const init = backend.fetchMock.mock.calls.find(([u]) => String(u).endsWith("/api/tickets/presets"))?.[1];
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer tok-analyst");
  });

  it("lets a lead into the console and sends the lead's token", async () => {
    const { backend } = await signInAs("lead");
    expect(await screen.findByRole("heading", { level: 1, name: "Analyze a ticket" })).toBeInTheDocument();
    expect(screen.getByText("lead")).toBeInTheDocument(); // role under the name
    await screen.findByLabelText("Load a demo ticket");
    const init = backend.fetchMock.mock.calls.find(([u]) => String(u).endsWith("/api/tickets/presets"))?.[1];
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer tok-lead");
  });

  it("removes a stored employee session instead of opening the console with it", () => {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify({ token: "emp-token", expiresAt: LOGIN_OK.expires_at,
                                                          user: { username: "emp", role: "employee" } }));
    const backend = mockBackend(analystRoutes());
    renderApp("/analyst/tickets");
    expect(screen.getByRole("heading", { level: 1, name: "IT staff sign in" })).toBeInTheDocument();
    expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
    expect(backend.calls.filter((c) => c.path.startsWith("/api/tickets"))).toHaveLength(0);
  });

  it("drops a stored session with an unknown role", () => {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify({ token: "x", expiresAt: LOGIN_OK.expires_at,
                                                          user: { username: "a", role: "admin" } }));
    mockBackend();
    renderApp("/analyst/analyze");
    expect(screen.getByRole("heading", { level: 1, name: "IT staff sign in" })).toBeInTheDocument();
    expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
  });
});
