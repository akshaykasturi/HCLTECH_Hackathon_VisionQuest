import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { fail, mockBackend, ok, renderApp } from "./utils";
import { HEALTH_OK, HEALTH_WARMING } from "./fixtures";

describe("app shell", () => {
  it("shows the navigation and the empty Home page", async () => {
    mockBackend();
    renderApp("/");
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    for (const label of ["Home", "Audit History", "System Health"]) {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("heading", { level: 1, name: "Home" })).toBeInTheDocument();
    expect(screen.getByText("Nothing here yet")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /Backend status: API healthy, warm-up healthy/ })).toBeInTheDocument();
  });

  it("navigates between pages", async () => {
    mockBackend();
    const { user } = renderApp("/");
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    await user.click(within(nav).getByRole("link", { name: "System Health" }));
    expect(await screen.findByRole("heading", { level: 1, name: "System Health" })).toBeInTheDocument();
    await user.click(within(nav).getByRole("link", { name: "Audit History" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Audit History" })).toBeInTheDocument();
  });

  it("opens the mobile drawer and closes it with Escape", async () => {
    mockBackend();
    const { user } = renderApp("/");
    await user.click(screen.getByRole("button", { name: "Open navigation" }));
    expect(screen.getByRole("dialog", { name: "Navigation" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows a not-found page for unknown paths", () => {
    mockBackend();
    renderApp("/nope");
    expect(screen.getByRole("heading", { level: 1, name: "Page not found" })).toBeInTheDocument();
  });

  it("says when the backend is still warming up", async () => {
    mockBackend({ "GET /health": ok(HEALTH_WARMING) });
    renderApp("/");
    expect(await screen.findByText("Warming up")).toBeInTheDocument();
  });

  it("never writes to browser storage", async () => {
    mockBackend();
    renderApp("/system-health");
    await screen.findAllByText("Healthy");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});

describe("system health", () => {
  it("shows healthy API, database, warm-up and readiness", async () => {
    mockBackend();
    renderApp("/system-health");
    for (const name of ["API", "Audit database", "Warm-up", "Readiness"]) {
      await waitFor(() => expect(within(screen.getByRole("article", { name })).getByText("Healthy")).toBeInTheDocument());
    }
    expect(screen.getByText(HEALTH_OK.warmup_detail)).toBeInTheDocument();
  });

  it("shows not ready while warming up", async () => {
    mockBackend({ "GET /health": ok(HEALTH_WARMING), "GET /health/ready": fail(503, "WARMING_UP", "Not ready") });
    renderApp("/system-health");
    await waitFor(() => expect(within(screen.getByRole("article", { name: "Warm-up" })).getByText("Not ready")).toBeInTheDocument());
    await waitFor(() => expect(within(screen.getByRole("article", { name: "Readiness" })).getByText("Not ready")).toBeInTheDocument());
  });

  it("shows the database as unavailable", async () => {
    mockBackend({ "GET /health": ok({ ...HEALTH_OK, database: "down" }), "GET /health/ready": fail(503, "DATABASE_UNAVAILABLE", "x") });
    renderApp("/system-health");
    await waitFor(() => expect(within(screen.getByRole("article", { name: "Audit database" })).getByText("Unavailable")).toBeInTheDocument());
  });

  it("recovers on Refresh after the backend was unreachable", async () => {
    let down = true;
    const backend = mockBackend({ "GET /health": () => (down ? "network" : ok(HEALTH_OK)) });
    const { user } = renderApp("/system-health");
    expect(await screen.findByText("Backend unreachable")).toBeInTheDocument();
    expect(within(screen.getByRole("article", { name: "Audit database" })).getByText("Unknown")).toBeInTheDocument();
    down = false;
    await user.click(screen.getAllByRole("button", { name: /Refresh/ })[0]);
    await waitFor(() => expect(within(screen.getByRole("article", { name: "API" })).getByText("Healthy")).toBeInTheDocument());
    expect(backend.callsTo("GET", "/health").length).toBeGreaterThanOrEqual(2);
  });
});
