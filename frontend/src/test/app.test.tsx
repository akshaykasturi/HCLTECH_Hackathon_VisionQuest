import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { analystRoutes, fail, mockBackend, ok, renderApp, signIn } from "./utils";
import { HEALTH_OK, HEALTH_WARMING } from "./fixtures";

describe("public site", () => {
  it("shows the employee home page with site navigation and footer", () => {
    mockBackend();
    renderApp("/");
    expect(screen.getByRole("heading", { level: 1, name: "How can we help you today?" })).toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "Site navigation" })).getByRole("link", { name: "Home" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "IT staff sign in" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("contentinfo")).toHaveTextContent("synthetic");
    // the analyst sign-in is a prominent button; the complaint form is honestly "coming soon"
    expect(screen.getByRole("link", { name: "IT staff: sign in to analyse tickets" })).toHaveAttribute("href", "/login");
    expect(screen.getByText(/complaint submission for employees is coming soon/)).toBeInTheDocument();
    // employees never see backend status or technical details
    expect(screen.queryByRole("link", { name: /Backend status/ })).not.toBeInTheDocument();
  });

  it("opens and closes the mobile menu", async () => {
    mockBackend();
    const { user } = renderApp("/");
    await user.click(screen.getByRole("button", { name: "Open menu" }));
    expect(screen.getByRole("navigation", { name: "Mobile site navigation" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Close menu" }));
    expect(screen.queryByRole("navigation", { name: "Mobile site navigation" })).not.toBeInTheDocument();
  });

  it("shows a not-found page for unknown paths", () => {
    mockBackend();
    renderApp("/nope");
    expect(screen.getByRole("heading", { level: 1, name: "Page not found" })).toBeInTheDocument();
  });

  it("never writes to browser storage before sign-in", () => {
    mockBackend();
    renderApp("/");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});

describe("analyst console shell", () => {
  it("sends signed-out visitors to the login page", () => {
    mockBackend();
    renderApp("/analyst/analyze");
    expect(screen.getByRole("heading", { level: 1, name: "IT staff sign in" })).toBeInTheDocument();
  });

  it("shows the sidebar navigation and the signed-in user", async () => {
    mockBackend(analystRoutes());
    signIn();
    renderApp("/analyst");
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    for (const label of ["Analyze", "Audit History", "System Health"]) {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(await screen.findByRole("heading", { level: 1, name: "Analyze a ticket" })).toBeInTheDocument();
    expect(screen.getByText("asha")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /Backend status: API healthy, warm-up healthy/ })).toBeInTheDocument();
  });

  it("navigates between console pages", async () => {
    mockBackend(analystRoutes());
    signIn();
    const { user } = renderApp("/analyst/analyze");
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    await user.click(within(nav).getByRole("link", { name: "System Health" }));
    expect(await screen.findByRole("heading", { level: 1, name: "System Health" })).toBeInTheDocument();
    await user.click(within(nav).getByRole("link", { name: "Audit History" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Audit History" })).toBeInTheDocument();
  });

  it("opens the mobile drawer and closes it with Escape", async () => {
    mockBackend(analystRoutes());
    signIn();
    const { user } = renderApp("/analyst/analyze");
    await user.click(screen.getByRole("button", { name: "Open navigation" }));
    expect(screen.getByRole("dialog", { name: "Navigation" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("signs out and returns to the login page", async () => {
    mockBackend(analystRoutes());
    signIn();
    const { user } = renderApp("/analyst/analyze");
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { level: 1, name: "IT staff sign in" })).toBeInTheDocument();
    expect(sessionStorage.length).toBe(0);
  });

  it("says when the backend is still warming up", async () => {
    mockBackend({ ...analystRoutes(), "GET /api/health": ok(HEALTH_WARMING) });
    signIn();
    renderApp("/analyst/system-health");
    expect((await screen.findAllByText("Warming up")).length).toBeGreaterThan(0);
  });
});

describe("system health", () => {
  beforeEach(() => signIn());

  it("shows healthy API, database, warm-up and readiness", async () => {
    mockBackend();
    renderApp("/analyst/system-health");
    for (const name of ["API", "Audit database", "Warm-up", "Readiness"]) {
      await waitFor(() => expect(within(screen.getByRole("article", { name })).getByText("Healthy")).toBeInTheDocument());
    }
    expect(screen.getByText(HEALTH_OK.warmup_detail)).toBeInTheDocument();
  });

  it("shows not ready while warming up", async () => {
    mockBackend({ "GET /api/health": ok(HEALTH_WARMING), "GET /api/health/ready": fail(503, "WARMING_UP", "Not ready") });
    renderApp("/analyst/system-health");
    await waitFor(() => expect(within(screen.getByRole("article", { name: "Warm-up" })).getByText("Not ready")).toBeInTheDocument());
    await waitFor(() => expect(within(screen.getByRole("article", { name: "Readiness" })).getByText("Not ready")).toBeInTheDocument());
  });

  it("shows the database as unavailable", async () => {
    mockBackend({ "GET /api/health": ok({ ...HEALTH_OK, database: "down" }), "GET /api/health/ready": fail(503, "DATABASE_UNAVAILABLE", "x") });
    renderApp("/analyst/system-health");
    await waitFor(() => expect(within(screen.getByRole("article", { name: "Audit database" })).getByText("Unavailable")).toBeInTheDocument());
  });

  it("recovers on Refresh after the backend was unreachable", async () => {
    let down = true;
    const backend = mockBackend({ "GET /api/health": () => (down ? "network" : ok(HEALTH_OK)) });
    const { user } = renderApp("/analyst/system-health");
    expect(await screen.findByText("Backend unreachable")).toBeInTheDocument();
    expect(within(screen.getByRole("article", { name: "Audit database" })).getByText("Unknown")).toBeInTheDocument();
    down = false;
    await user.click(screen.getAllByRole("button", { name: /Refresh/ })[0]);
    await waitFor(() => expect(within(screen.getByRole("article", { name: "API" })).getByText("Healthy")).toBeInTheDocument());
    expect(backend.callsTo("GET", "/api/health").length).toBeGreaterThanOrEqual(2);
  });
});
