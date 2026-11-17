import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { HISTORY_OPEN, HISTORY_RESOLVED, LOGIN_OK, RESULT_RECOMMEND } from "./fixtures";
import { deferred, fail, mockBackend, ok, renderApp, signIn, type Handler, type Reply } from "./utils";

const HISTORY = "/api/tickets/history";

function backend(history: Handler = ok([HISTORY_RESOLVED, HISTORY_OPEN]), extra: Record<string, Handler> = {}) {
  return mockBackend({ [`GET ${HISTORY}`]: history, ...extra });
}

const rowFor = (resultId: string) =>
  screen.getByRole("button", { name: `Open result ${resultId}` }).closest("tr")!;

describe("review history list", () => {
  beforeEach(() => signIn());

  it("renders the page in the console with Review History active", async () => {
    backend();
    renderApp("/analyst/history");
    expect(screen.getByRole("heading", { level: 1, name: "Review History" })).toBeInTheDocument();
    expect(screen.getByText("Review previous AI analyses, feedback and incident resolution activity.")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    expect(within(nav).getByRole("link", { name: "Review History" })).toHaveAttribute("aria-current", "page");
    for (const label of ["Analyze", "Tickets"]) expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    await screen.findByRole("table");
  });

  it("shows a loading skeleton, then the analyses with outcome, feedback and resolution", async () => {
    const gate = deferred<Reply>();
    const api = backend(() => gate.promise);
    renderApp("/analyst/history");
    expect(screen.getByText("Loading review history…")).toBeInTheDocument();
    gate.resolve(ok([HISTORY_RESOLVED, HISTORY_OPEN]));

    const resolved = await waitFor(() => rowFor(HISTORY_RESOLVED.result_id));
    expect(within(resolved).getByText("VPN drops every few minutes")).toBeInTheDocument();
    expect(within(resolved).getByText("Corporate VPN")).toBeInTheDocument();
    expect(within(resolved).getByText("Network")).toBeInTheDocument();
    expect(within(resolved).getByText("Recommend")).toBeInTheDocument();
    expect(within(resolved).getByText("Incorrect")).toBeInTheDocument(); // newest verdict counts
    expect(within(resolved).getByText("+1")).toBeInTheDocument();
    expect(within(resolved).getByText("Resolved")).toBeInTheDocument();
    expect(within(resolved).getByText("16 Nov 2026")).toBeInTheDocument();

    const open = rowFor(HISTORY_OPEN.result_id);
    expect(within(open).getByText("Clarify")).toBeInTheDocument();
    expect(within(open).getByText("Unresolved")).toBeInTheDocument();
    expect(within(open).getByText("Not classified")).toBeInTheDocument();
    expect(within(open).getByText("Open")).toBeInTheDocument();

    const call = api.callsTo("GET", HISTORY)[0];
    expect([call.query.get("limit"), call.query.get("offset")]).toEqual(["20", "0"]);
    const init = api.fetchMock.mock.calls.find(([u]) => String(u).includes(HISTORY))?.[1];
    expect((init?.headers as Record<string, string>).Authorization).toBe(`Bearer ${LOGIN_OK.token}`);
  });

  it("shows an empty state when nothing has been saved yet", async () => {
    backend(ok([]));
    renderApp("/analyst/history");
    expect(await screen.findByText("No analyses yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Analyze a ticket" })).toHaveAttribute("href", "/analyst/analyze");
  });

  it("pages through older and newer analyses with limit and offset", async () => {
    const page = Array.from({ length: 20 }, (_, i) => ({ ...HISTORY_OPEN, result_id: `res-${String(i).padStart(4, "0")}` }));
    const api = backend((req) => ok(req.query.get("offset") === "0" ? page : [HISTORY_RESOLVED]));
    const { user } = renderApp("/analyst/history");
    expect(await screen.findByText("Showing 1–20, newest first")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Newer/ })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Older/ }));
    expect(await screen.findByText("Showing 21–21, newest first")).toBeInTheDocument();
    expect(api.callsTo("GET", HISTORY).at(-1)!.query.get("offset")).toBe("20");
    expect(screen.getByRole("button", { name: /Older/ })).toBeDisabled(); // fewer than a full page: no more
    await user.click(screen.getByRole("button", { name: /Newer/ }));
    expect(await screen.findByText("Showing 1–20, newest first")).toBeInTheDocument();
  });

  it("says history is temporarily unavailable while the database is not connected, and does not keep retrying", async () => {
    const api = backend(fail(503, "DB_NOT_READY", "Saving is not available yet"));
    const { user } = renderApp("/analyst/history");
    expect(await screen.findByText("Your review history is temporarily unavailable. Please try again later.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 400));
    expect(api.callsTo("GET", HISTORY)).toHaveLength(1); // no automatic retries
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(api.callsTo("GET", HISTORY)).toHaveLength(2));
  });

  it("treats a database outage the same way", async () => {
    backend(fail(503, "DATABASE_UNAVAILABLE", "The database is not available."));
    renderApp("/analyst/history");
    expect(await screen.findByText("Your review history is temporarily unavailable. Please try again later.")).toBeInTheDocument();
  });

  it("shows a retryable error on a server failure, and recovers", async () => {
    let failing = true;
    backend(() => (failing ? fail(500, "INTERNAL_ERROR", "x") : ok([HISTORY_OPEN])));
    const { user } = renderApp("/analyst/history");
    expect(await screen.findByText("Server error")).toBeInTheDocument();
    failing = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(rowFor(HISTORY_OPEN.result_id)).toBeInTheDocument());
  });

  it("rejects records that do not have the expected shape", async () => {
    backend(ok([{ result_id: "x", outcome: "MAYBE" }]));
    renderApp("/analyst/history");
    expect(await screen.findByText("Unexpected response")).toBeInTheDocument();
  });

  it("returns to the login page when the session expires (401)", async () => {
    backend(fail(401, "UNAUTHORIZED", "expired"));
    renderApp("/analyst/history");
    expect(await screen.findByRole("heading", { level: 1, name: "IT staff sign in" })).toBeInTheDocument();
    expect(sessionStorage.length).toBe(0);
  });

  it("shows access denied for a 403", async () => {
    backend(fail(403, "FORBIDDEN", "This needs the analyst role."));
    renderApp("/analyst/history");
    expect(await screen.findByText("Not allowed")).toBeInTheDocument();
  });
});

describe("review history detail", () => {
  beforeEach(() => signIn());

  it("opens a saved analysis with the reused result cards, and goes back", async () => {
    const { user } = (backend(), renderApp("/analyst/history"));
    await user.click(await screen.findByRole("button", { name: `Open result ${HISTORY_RESOLVED.result_id}` }));

    expect(await screen.findByRole("heading", { level: 1, name: "VPN drops every few minutes" })).toBeInTheDocument();
    expect(screen.getByText(`Result ${HISTORY_RESOLVED.result_id}`)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Outcome: Recommended fix" })).toBeInTheDocument();
    expect(screen.getByText(/VPN disconnects since the gateway change/)).toBeInTheDocument();
    expect(screen.getByText("Confidence 93%")).toBeInTheDocument(); // the Analyze classification card
    expect(screen.getByText(RESULT_RECOMMEND.recommendation!.steps[0].text)).toBeInTheDocument(); // the Analyze steps card
    const evidence = screen.getByRole("heading", { name: "Evidence used" }).closest("section")!;
    expect(within(evidence).getByRole("link", { name: "INC-100045" })).toHaveAttribute("href", "/analyst/tickets/INC-100045");
    expect(within(evidence).getByText("doc:KB-004#4")).toBeInTheDocument();
    expect(screen.getByText(/full text of similar tickets and knowledge-base articles is not stored/)).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "Back to history" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Review History" })).toBeInTheDocument();
  });

  it("lists every feedback entry, newest first, with what the backend recorded", async () => {
    backend();
    renderApp(`/analyst/history?result=${HISTORY_RESOLVED.result_id}`);
    const entries = await screen.findByRole("list", { name: "Feedback entries" });
    const items = within(entries).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(within(items[0]).getByText("Incorrect")).toBeInTheDocument();
    expect(within(items[0]).getByText("Current")).toBeInTheDocument();
    expect(within(items[0]).getByText(/ravi/)).toBeInTheDocument();
    expect(within(items[0]).getByText("INC-100777")).toBeInTheDocument();
    expect(within(items[1]).getByText("Useful")).toBeInTheDocument();
    expect(within(items[1]).getByText("Step 1 fixed it")).toBeInTheDocument();
    expect(within(items[1]).getByText("16 Nov 2026, 09:10:00 UTC")).toBeInTheDocument();
  });

  it("says when feedback time and reviewer were not recorded, and shows an unresolved analysis", async () => {
    backend();
    renderApp(`/analyst/history?result=${HISTORY_OPEN.result_id}`);
    const entries = await screen.findByRole("list", { name: "Feedback entries" });
    expect(within(entries).getByText("Time not recorded")).toBeInTheDocument();
    expect(within(entries).getByText(/Reviewer not recorded/)).toBeInTheDocument();
    expect(screen.getByText("This analysis has not been marked as resolved.")).toBeInTheDocument();
    expect(screen.getByText("No classification was stored with this analysis.")).toBeInTheDocument();
    expect(screen.queryByText(/classifier is not connected/)).not.toBeInTheDocument(); // the Analyze wording
    expect(screen.getByText("No evidence references were stored for this analysis.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Recommended resolution steps" })).not.toBeInTheDocument();
  });

  it("shows resolution activity", async () => {
    backend();
    renderApp(`/analyst/history?result=${HISTORY_RESOLVED.result_id}`);
    const card = (await screen.findByRole("heading", { name: "Resolution" })).closest("section")!;
    expect(within(card).getByText("Resolved")).toBeInTheDocument();
    expect(within(card).getByText("16 Nov 2026, 12:20:00 UTC")).toBeInTheDocument();
    expect(within(card).getByText("asha")).toBeInTheDocument();
    expect(within(card).getByText("3 h 20 min")).toBeInTheDocument();
  });

  it("adds new feedback (append-only) and reloads the history", async () => {
    const api = backend(ok([HISTORY_RESOLVED, HISTORY_OPEN]),
                        { [`POST /api/tickets/results/${HISTORY_OPEN.result_id}/feedback`]: ok({ id: 9 }, 201) });
    const { user } = renderApp(`/analyst/history?result=${HISTORY_OPEN.result_id}`);
    await screen.findByRole("list", { name: "Feedback entries" });
    await user.click(screen.getByRole("button", { name: "Useful" }));
    await user.click(screen.getByRole("button", { name: "Send feedback" }));
    expect(await screen.findByText(/Feedback saved: useful/)).toBeInTheDocument();
    expect(api.callsTo("POST", `/api/tickets/results/${HISTORY_OPEN.result_id}/feedback`)[0].body).toEqual({ verdict: "useful" });
    await waitFor(() => expect(api.callsTo("GET", HISTORY)).toHaveLength(2));
  });

  it("shows the stored decision confidence when there is one", async () => {
    backend();
    renderApp(`/analyst/history?result=${HISTORY_RESOLVED.result_id}`);
    const banner = await screen.findByRole("region", { name: "Outcome: Recommended fix" });
    expect(within(banner).getByText(/Decision confidence/)).toBeInTheDocument();
    expect(within(banner).getByText("86%")).toBeInTheDocument();
  });

  it("hides the confidence line when none was stored, instead of showing 0%", async () => {
    const { confidence: _ignored, ...noConfidence } = HISTORY_RESOLVED; void _ignored;
    backend(ok([noConfidence, { ...HISTORY_OPEN, confidence: null }]));
    const { user } = renderApp(`/analyst/history?result=${HISTORY_RESOLVED.result_id}`);
    const banner = await screen.findByRole("region", { name: "Outcome: Recommended fix" });
    expect(within(banner).queryByText(/Decision confidence/)).not.toBeInTheDocument();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
    expect(within(banner).getByText("STRONG_MATCH")).toBeInTheDocument(); // reasons still shown
    await user.click(screen.getByRole("link", { name: "Back to history" }));
    await user.click(await screen.findByRole("button", { name: `Open result ${HISTORY_OPEN.result_id}` }));
    expect(within(await screen.findByRole("region", { name: "Outcome: More information needed" }))
      .queryByText(/Decision confidence/)).not.toBeInTheDocument();
  });

  it("does not crash when the backend sends null for list fields", async () => {
    backend(ok([{ ...HISTORY_RESOLVED, evidence_ids: null, findings: null, reasons: null, feedback: null, resolution: null }]));
    renderApp(`/analyst/history?result=${HISTORY_RESOLVED.result_id}`);
    expect(await screen.findByRole("heading", { level: 1, name: "VPN drops every few minutes" })).toBeInTheDocument();
    expect(screen.getByText("No evidence references were stored for this analysis.")).toBeInTheDocument();
    expect(screen.getByText("No feedback has been given on this analysis yet.")).toBeInTheDocument();
    expect(screen.getByText("This analysis has not been marked as resolved.")).toBeInTheDocument();
  });

  it("does not crash the list when the backend sends null lists either", async () => {
    backend(ok([{ ...HISTORY_OPEN, evidence_ids: null, findings: null, reasons: null, feedback: null }]));
    renderApp("/analyst/history");
    const row = await waitFor(() => rowFor(HISTORY_OPEN.result_id));
    expect(within(row).getByText("None yet")).toBeInTheDocument();
  });

  it("says when the selected result is not on the loaded page", async () => {
    backend();
    renderApp("/analyst/history?result=res-missing");
    expect(await screen.findByText("Result res-missing is not on this page of the history.")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
  });
});
