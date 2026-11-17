import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { CHAIN_BAD, CHAIN_OK, LOGIN_OK, RESULTS, STATS } from "./fixtures";
import { analystRoutes, deferred, fail, mockBackend, ok, renderApp, signIn, type Handler, type Reply } from "./utils";

const LEAD = { username: "lena", role: "lead" as const };
const STATS_PATH = "/api/tickets/stats";

function backend(stats: Handler = ok(STATS), audit: Record<string, Handler> = {}) {
  return mockBackend({
    ...analystRoutes(),
    [`GET ${STATS_PATH}`]: stats,
    "GET /api/audit/verify": ok(CHAIN_OK),
    "GET /api/audit/results": ok(RESULTS),
    ...audit,
  });
}
const tile = (label: string) => screen.getByRole("article", { name: label });

describe("dashboard access", () => {
  it("is in the sidebar for a lead", async () => {
    backend();
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    expect(within(nav).getByRole("link", { name: "Dashboard" })).toHaveAttribute("aria-current", "page");
    expect(await screen.findByRole("heading", { level: 1, name: "Dashboard" })).toBeInTheDocument();
  });

  it("is hidden from analysts, and the address sends them to Analyze without asking for stats", async () => {
    const api = backend();
    signIn(); // analyst
    renderApp("/analyst/dashboard");
    expect(await screen.findByRole("heading", { level: 1, name: "Analyze a ticket" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    expect(within(nav).queryByRole("link", { name: "Dashboard" })).not.toBeInTheDocument();
    for (const label of ["Analyze", "Tickets", "Review History"]) expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    expect(api.callsTo("GET", STATS_PATH)).toHaveLength(0);
  });

  it("shows access denied if the backend refuses (403)", async () => {
    backend(fail(403, "FORBIDDEN", "This needs the lead role."));
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    expect(await screen.findByText("Not allowed")).toBeInTheDocument();
  });

  it("returns to the login page when the session expires (401)", async () => {
    backend(fail(401, "UNAUTHORIZED", "expired"));
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    expect(await screen.findByRole("heading", { level: 1, name: "IT staff sign in" })).toBeInTheDocument();
    expect(sessionStorage.length).toBe(0);
  });
});

describe("dashboard content", () => {
  it("loads with the lead's token and shows the headline figures", async () => {
    const gate = deferred<Reply>();
    const api = backend(() => gate.promise);
    signIn(LEAD, "tok-lead");
    renderApp("/analyst/dashboard");
    expect(screen.getByText("Loading the dashboard…")).toBeInTheDocument();
    gate.resolve(ok(STATS));
    expect(await screen.findByRole("article", { name: "Analyses" })).toHaveTextContent("120");
    expect(tile("Rated useful")).toHaveTextContent("71%");          // 22 useful of 31 ratings
    expect(tile("Rated useful")).toHaveTextContent("22 of 31 analyst ratings");
    expect(tile("Median time to resolve")).toHaveTextContent("18.5 h");
    expect(tile("With the assistant")).toHaveTextContent("12.4 h");
    expect(within(tile("With the assistant")).getByText("Estimate")).toBeInTheDocument();
    const init = api.fetchMock.mock.calls.find(([u]) => String(u).endsWith(STATS_PATH))?.[1];
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer tok-lead");
    expect(LOGIN_OK.token).not.toBe("tok-lead");
  });

  it("shows the outcome mix with count and share for every outcome", async () => {
    backend();
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    const list = await screen.findByRole("list", { name: "Outcome mix" });
    const rows = within(list).getAllByRole("listitem").map((li) => li.textContent);
    expect(rows).toEqual([
      "Recommended a fix72 · 60%", "Asked for more information30 · 25%",
      "Escalated to a team12 · 10%", "Blocked by the safety screen6 · 5%",
    ]);
  });

  it("ranks categories by useful rate and does not invent a rate for unrated ones", async () => {
    backend();
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    const list = await screen.findByRole("list", { name: "Rated useful by category" });
    const rows = within(list).getAllByRole("listitem").map((li) => li.textContent);
    expect(rows).toEqual(["Identity & Access90% · 9 of 10", "Network62% · 13 of 21", "CloudNo feedback yet"]);
  });

  it("shows the incident overview from the backend counts", async () => {
    backend();
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    expect(await screen.findByRole("article", { name: "Total incidents" })).toHaveTextContent("1,100");
    expect(tile("Resolved")).toHaveTextContent("980");
    expect(tile("Pending")).toHaveTextContent("120");
    expect(tile("Critical")).toHaveTextContent("147");
    expect(tile("Resolution rate")).toHaveTextContent("89%");
    expect(tile("Resolution rate")).toHaveTextContent("980 of 1,100 resolved");
  });

  it("shows measured MTTR, the estimate, time saved and improvement, each labelled", async () => {
    backend();
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    await screen.findByRole("article", { name: "Median time to resolve" });
    expect(tile("Median time to resolve")).toHaveTextContent("Measured, without the assistant · 980 tickets");
    expect(within(tile("Median time to resolve")).queryByText("Estimate")).not.toBeInTheDocument();
    for (const label of ["With the assistant", "Time saved", "Improvement"]) {
      expect(within(tile(label)).getByText("Estimate")).toBeInTheDocument();
    }
    expect(tile("Time saved")).toHaveTextContent("6.1 h");
    expect(tile("Improvement")).toHaveTextContent("33%");
    expect(screen.getByText("Analysts accept 60% of recommendations")).toBeInTheDocument();
    expect(screen.getByText(/must not be reported as observed results/)).toBeInTheDocument();
    expect(screen.getByText(/missing, unreadable or negative duration are left out/)).toBeInTheDocument();
  });

  it("shows MTTR by priority and by service, slowest service first", async () => {
    backend();
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    const byPriority = await screen.findByRole("list", { name: "Median time to resolve by priority" });
    expect(within(byPriority).getAllByRole("listitem").map((li) => li.textContent))
      .toEqual(["P14.2 h · 140 tickets", "P215.0 h · 360 tickets"]);
    const byService = screen.getByRole("list", { name: "Median time to resolve by service" });
    expect(within(byService).getAllByRole("listitem").map((li) => li.textContent))
      .toEqual(["SAP ERP30.5 h · 40 tickets", "Corporate VPN12.1 h · 44 tickets"]);
  });

  it("draws the resolution trend with a focusable point per month and a table view", async () => {
    backend();
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    const chart = await screen.findByRole("group", { name: "Median time to resolve by month" });
    const points = within(chart).getAllByRole("img");
    expect(points).toHaveLength(3);
    expect(points[0]).toHaveAttribute("aria-label", "Jun 2026: median 20.3 h, 160 resolved");
    expect(points[0]).toHaveAttribute("tabindex", "0");
    const table = screen.getByRole("table", { name: "Median time to resolve by month" });
    expect(within(table).getAllByRole("row")).toHaveLength(4); // header + 3 months
  });

  it("shows priority P1-P4 in order and folds services past the top seven into Other", async () => {
    backend();
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    const priority = await screen.findByRole("list", { name: "Incidents by priority" });
    expect(within(priority).getAllByRole("listitem").map((li) => li.textContent))
      .toEqual(["P1147 · 13%", "P2386 · 35%", "P3442 · 40%", "P4125 · 11%"]);
    const services = screen.getByRole("list", { name: "Incidents by service" });
    const rows = within(services).getAllByRole("listitem").map((li) => li.textContent);
    expect(rows).toHaveLength(8);
    expect(rows[0]).toBe("Service A200 · 18%");
    expect(rows[7]).toBe("Other services (3)360 · 33%"); // 130 + 120 + 110: nothing dropped
    expect(within(screen.getByRole("list", { name: "Incidents by category" })).getAllByRole("listitem")[0])
      .toHaveTextContent("Network150 · 14%");
  });

  it("says MTTR, incidents and distributions are not available instead of showing numbers", async () => {
    backend(ok({ total_results: 120, outcome_counts: STATS.outcome_counts, feedback_by_category: STATS.feedback_by_category }));
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    expect(await screen.findByText("Incident figures are not available yet: the statistics service does not return them."))
      .toBeInTheDocument();
    for (const label of ["Median time to resolve", "With the assistant", "Time saved", "Improvement"]) {
      expect(tile(label)).toHaveTextContent("—Not available yet");
      expect(within(tile(label)).queryByText("Estimate")).not.toBeInTheDocument();
    }
    expect(screen.queryByRole("group", { name: "Median time to resolve by month" })).not.toBeInTheDocument();
    expect(screen.getByText("Not available yet: the estimate is not connected.")).toBeInTheDocument();
    const distribution = screen.getByRole("region", { name: "Incident distribution" });
    expect(within(distribution).getAllByText("Not available yet.")).toHaveLength(3);
  });

  it("explains that filters are not offered yet instead of filtering a partial list", async () => {
    const api = backend();
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    expect(await screen.findByText(/filters will appear once the statistics service supports them/)).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(api.callsTo("GET", "/api/tickets")).toHaveLength(0); // never counts from the 50-row ticket list
  });

  it("shows no useful rate (not 0%) when there is no feedback", async () => {
    backend(ok({ ...STATS, feedback_by_category: null }));
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    expect(await screen.findByRole("article", { name: "Rated useful" })).toHaveTextContent("—No feedback yet");
    expect(screen.getByText("No analyst feedback has been given yet.")).toBeInTheDocument();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
  });

  it("shows an empty state when nothing has been analysed yet", async () => {
    backend(ok({ total_results: 0, outcome_counts: {} }));
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    expect(await screen.findByText("No analyses yet")).toBeInTheDocument();
    expect(await screen.findByText("Chain intact")).toBeInTheDocument(); // audit still shown
    expect(screen.queryByRole("article", { name: "Analyses" })).not.toBeInTheDocument();
  });

  it("says the dashboard is temporarily unavailable on 503 DB_NOT_READY, without retrying on its own", async () => {
    const api = backend(fail(503, "DB_NOT_READY", "Saving is not available yet"));
    signIn(LEAD);
    const { user } = renderApp("/analyst/dashboard");
    expect(await screen.findByText("Dashboard data is temporarily unavailable. Please try again later.")).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 400));
    expect(api.callsTo("GET", STATS_PATH)).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(api.callsTo("GET", STATS_PATH)).toHaveLength(2));
  });

  it("shows a retryable error on a server failure, and recovers", async () => {
    let failing = true;
    backend(() => (failing ? fail(500, "INTERNAL_ERROR", "x") : ok(STATS)));
    signIn(LEAD);
    const { user } = renderApp("/analyst/dashboard");
    expect(await screen.findByText("Server error")).toBeInTheDocument();
    failing = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("article", { name: "Analyses" })).toBeInTheDocument();
  });

  it("rejects statistics that do not add up", async () => {
    backend(ok({ ...STATS, total_results: 10 })); // 120 outcomes cannot fit in 10 analyses
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    expect(await screen.findByText("Unexpected response")).toBeInTheDocument();
  });
});

describe("dashboard audit trail", () => {
  const trail = () => screen.getByRole("region", { name: "Audit trail" });

  it("shows an intact chain and the newest audit records, with the lead's token", async () => {
    const api = backend();
    signIn(LEAD, "tok-lead");
    renderApp("/analyst/dashboard");
    const integrity = await screen.findByRole("region", { name: "Audit log integrity" });
    expect(await within(integrity).findByText("Chain intact")).toBeInTheDocument();
    expect(within(integrity).getByText("3 events checked")).toBeInTheDocument();
    const list = await screen.findByRole("list", { name: "Recent audit records list" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(RESULTS.length);
    expect(within(rows[0]).getByText("CASE-0002")).toBeInTheDocument(); // newest first, as the backend sends them
    expect(within(rows[1]).getByText(/1 feedback/)).toBeInTheDocument();
    expect(within(trail()).getByRole("link", { name: "Open Audit History" })).toHaveAttribute("href", "/analyst/audit");
    for (const path of ["/api/audit/verify", "/api/audit/results"]) {
      const init = api.fetchMock.mock.calls.find(([u]) => String(u).endsWith(path))?.[1];
      expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer tok-lead");
    }
  });

  it("shows a broken chain with where and why it broke", async () => {
    backend(ok(STATS), { "GET /api/audit/verify": ok(CHAIN_BAD) });
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    const integrity = await screen.findByRole("region", { name: "Audit log integrity" });
    expect(await within(integrity).findByText("Chain broken at event #2")).toBeInTheDocument();
    expect(within(integrity).getByText(CHAIN_BAD.reason!)).toBeInTheDocument();
    expect(within(integrity).queryByText("Chain intact")).not.toBeInTheDocument();
  });

  it("says so when the audit log is still empty", async () => {
    backend(ok(STATS), { "GET /api/audit/verify": ok({ ...CHAIN_OK, events_checked: 0 }), "GET /api/audit/results": ok([]) });
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    expect(await screen.findByText("No events recorded yet")).toBeInTheDocument();
    expect(await screen.findByText("No audit records yet.")).toBeInTheDocument();
  });

  it("still shows the audit trail while the stats are unavailable", async () => {
    backend(fail(503, "DB_NOT_READY", "x"));
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    expect(await screen.findByText("Dashboard data is temporarily unavailable. Please try again later.")).toBeInTheDocument();
    expect(await screen.findByText("Chain intact")).toBeInTheDocument();
    expect(await screen.findByRole("list", { name: "Recent audit records list" })).toBeInTheDocument();
  });

  it("keeps an audit failure inside its card and can retry it", async () => {
    let failing = true;
    const api = backend(ok(STATS), { "GET /api/audit/verify": () => (failing ? fail(500, "INTERNAL_ERROR", "x") : ok(CHAIN_OK)) });
    signIn(LEAD);
    const { user } = renderApp("/analyst/dashboard");
    const integrity = await screen.findByRole("region", { name: "Audit log integrity" });
    expect(await within(integrity).findByText("Server error")).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Analyses" })).toHaveTextContent("120"); // the rest still renders
    failing = false;
    await user.click(within(integrity).getByRole("button", { name: "Retry" }));
    expect(await within(integrity).findByText("Chain intact")).toBeInTheDocument();
    expect(api.callsTo("GET", "/api/audit/verify")).toHaveLength(2);
  });
});
