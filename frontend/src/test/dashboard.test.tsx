import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { LOGIN_OK, STATS } from "./fixtures";
import { analystRoutes, deferred, fail, mockBackend, ok, renderApp, signIn, type Handler, type Reply } from "./utils";

const LEAD = { username: "lena", role: "lead" as const };
const STATS_PATH = "/api/tickets/stats";

function backend(stats: Handler = ok(STATS)) {
  return mockBackend({ ...analystRoutes(), [`GET ${STATS_PATH}`]: stats });
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
    expect(tile("Median time to resolve")).toHaveTextContent("Measured, without the assistant");
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

  it("shows the measured baseline by priority and labels the assisted figure as an estimate with its assumptions", async () => {
    backend();
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    const baseline = await screen.findByRole("region", { name: "Measured baseline" });
    expect(within(baseline).getByText(/Median of 980 resolved historical tickets/)).toBeInTheDocument();
    expect(within(baseline).getByText(/mean 26.3 h/)).toBeInTheDocument();
    expect(within(baseline).getByRole("row", { name: /P1 4.2 h 140/ })).toBeInTheDocument();
    const estimate = screen.getByRole("region", { name: "Estimated with the assistant" });
    expect(within(estimate).getByText("Estimate")).toBeInTheDocument();
    expect(within(estimate).getByText("Estimated, not observed. Based on these assumptions:")).toBeInTheDocument();
    expect(within(estimate).getByText("Analysts accept 60% of recommendations")).toBeInTheDocument();
    expect(screen.getByText(/must not be reported as an observed result/)).toBeInTheDocument();
  });

  it("says MTTR is not available instead of showing numbers when it is missing", async () => {
    backend(ok({ ...STATS, mttr_baseline: null, mttr_assisted: null }));
    signIn(LEAD);
    renderApp("/analyst/dashboard");
    await screen.findByRole("article", { name: "Analyses" });
    expect(tile("Median time to resolve")).toHaveTextContent("—Not available yet");
    expect(tile("With the assistant")).toHaveTextContent("—Not available yet");
    expect(within(tile("With the assistant")).queryByText("Estimate")).not.toBeInTheDocument();
    expect(screen.getByText("Not available yet: the baseline calculation is not connected.")).toBeInTheDocument();
    expect(screen.getByText("Not available yet: the estimate is not connected.")).toBeInTheDocument();
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
