import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { LOGIN_OK, TICKET_DETAIL, TICKET_TAXONOMY, TICKETS } from "./fixtures";
import { deferred, fail, mockBackend, ok, renderApp, signIn, type Handler, type Reply, type Request } from "./utils";

/** A fake GET /api/tickets that filters TICKETS the way the backend does (exact, case-insensitive). */
function listHandler(req: Request): Reply {
  const keys = ["affected_service", "category", "subcategory", "priority", "status"] as const;
  const rows = TICKETS.filter((t) => keys.every((k) => {
    const want = req.query.get(k);
    return !want || (t[k] ?? "").toLowerCase() === want.toLowerCase();
  }));
  const q = req.query.get("q")?.toLowerCase();
  return ok(q ? rows.filter((t) => (t.subject ?? "").toLowerCase().includes(q)).map((t) => ({ ...t, score: 0.8, match: "related" })) : rows);
}

function backend(extra: Record<string, Handler> = {}) {
  return mockBackend({
    "GET /api/tickets": listHandler,
    "GET /api/tickets/taxonomy": ok(TICKET_TAXONOMY),
    "GET /api/tickets/INC-100045": ok(TICKET_DETAIL),
    ...extra,
  });
}

const lastList = (api: ReturnType<typeof backend>) => {
  const calls = api.callsTo("GET", "/api/tickets");
  return calls[calls.length - 1].query;
};
const table = () => screen.getByRole("table");
const rowFor = (id: string) => within(table()).getByRole("link", { name: id }).closest("tr")!;

describe("tickets page", () => {
  beforeEach(() => signIn());

  it("renders the page in the analyst console with Tickets active", async () => {
    backend();
    renderApp("/analyst/tickets");
    expect(screen.getByRole("heading", { level: 1, name: "Incident Management" })).toBeInTheDocument();
    expect(screen.getByText("Search, review and manage historical IT support incidents.")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Search by ticket ID, subject or description...")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    expect(within(nav).getByRole("link", { name: "Tickets" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Analyze" })).not.toHaveAttribute("aria-current");
    await screen.findByRole("table");
  });

  it("shows a loading skeleton, then the tickets with badges, newest first", async () => {
    const gate = deferred<Reply>();
    const api = backend({ "GET /api/tickets": () => gate.promise });
    renderApp("/analyst/tickets");
    expect(screen.getByText("Loading tickets…")).toBeInTheDocument();
    gate.resolve(ok(TICKETS));
    expect(await screen.findByRole("link", { name: "INC-100045" })).toBeInTheDocument();
    expect(within(rowFor("INC-100045")).getByText("P2")).toBeInTheDocument();
    expect(within(rowFor("INC-100031")).getByText("P1")).toBeInTheDocument();
    expect(within(rowFor("INC-100031")).getByText("Open")).toBeInTheDocument();
    expect(within(rowFor("INC-100045")).getByText("02 Oct 2026")).toBeInTheDocument();
    expect(screen.getByText("2 tickets, newest first")).toBeInTheDocument();
    expect(lastList(api).toString()).toBe(""); // no filters sent
    // the request carries the login token
    const init = api.fetchMock.mock.calls.find(([u]) => String(u).endsWith("/api/tickets"))?.[1];
    expect((init?.headers as Record<string, string>).Authorization).toBe(`Bearer ${LOGIN_OK.token}`);
  });

  it("searches with debounced text and can clear the search", async () => {
    const api = backend();
    const { user } = renderApp("/analyst/tickets");
    await screen.findByRole("table");
    await user.type(screen.getByRole("searchbox", { name: "Search tickets" }), "vpn");
    expect(await screen.findByText("1 best match for “vpn”")).toBeInTheDocument();
    expect(lastList(api).get("q")).toBe("vpn");
    // typing three letters sent one search request, not three
    expect(api.callsTo("GET", "/api/tickets").filter((c) => c.query.get("q"))).toHaveLength(1);
    expect(screen.queryByRole("link", { name: "INC-100031" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear search" }));
    expect(screen.getByRole("searchbox", { name: "Search tickets" })).toHaveValue("");
    expect(await screen.findByRole("link", { name: "INC-100031" })).toBeInTheDocument();
    expect(lastList(api).get("q")).toBeNull();
  });

  it("shows priority as the data's own P1-P4 values, with no invented wording", async () => {
    backend();
    renderApp("/analyst/tickets");
    await screen.findByRole("table");
    expect(within(rowFor("INC-100031")).getByText("P1")).toBeInTheDocument();
    expect(screen.queryByText(/Critical|High|Medium|Low/)).not.toBeInTheDocument();
  });

  it("opens an exact ticket ID directly when no filters are set", async () => {
    const api = backend();
    const { user } = renderApp("/analyst/tickets");
    await screen.findByRole("table");
    await user.type(screen.getByRole("searchbox", { name: "Search tickets" }), "inc-100045");
    expect(await screen.findByText("Exact ticket ID match")).toBeInTheDocument();
    expect(api.callsTo("GET", "/api/tickets/INC-100045")).toHaveLength(1);
    expect(within(table()).getAllByRole("row")).toHaveLength(2); // header + one ticket
  });

  it("shows an exact ticket ID when it matches the active filters", async () => {
    backend();
    renderApp("/analyst/tickets?q=INC-100045&affected_service=Corporate%20VPN&priority=p2");
    expect(await screen.findByText("Exact ticket ID match")).toBeInTheDocument();
    expect(within(table()).getByRole("link", { name: "INC-100045" })).toBeInTheDocument();
    expect(within(screen.getByLabelText("Active filters")).getByText("Corporate VPN")).toBeInTheDocument();
  });

  it("does not show an exact ticket ID that the active filters exclude, and offers to clear them", async () => {
    const api = backend();
    const { user } = renderApp("/analyst/tickets?q=INC-100045&priority=P1");
    expect(await screen.findByText("INC-100045 does not match the active filters")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText("Exact ticket ID match")).not.toBeInTheDocument();
    await user.click(within(screen.getByRole("main")).getAllByRole("button", { name: "Clear filters" }).at(-1)!);
    expect(await screen.findByText("Exact ticket ID match")).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Search tickets" })).toHaveValue("INC-100045"); // the search stays
    expect(screen.queryByLabelText("Active filters")).not.toBeInTheDocument();
    expect(api.callsTo("GET", "/api/tickets/INC-100045").length).toBeGreaterThanOrEqual(2);
  });

  it("says so when an exact ticket ID does not exist", async () => {
    backend({ "GET /api/tickets/INC-999999": fail(404, "TICKET_NOT_FOUND", "x") });
    renderApp("/analyst/tickets?q=INC-999999");
    expect(await screen.findByText("There is no historical ticket with the ID INC-999999.")).toBeInTheDocument();
  });

  it("filters by service", async () => {
    const api = backend();
    const { user } = renderApp("/analyst/tickets");
    await screen.findByRole("table");
    await user.selectOptions(screen.getByLabelText("Service"), "Corporate VPN");
    await waitFor(() => expect(lastList(api).get("affected_service")).toBe("Corporate VPN"));
    await waitFor(() => expect(screen.queryByRole("link", { name: "INC-100031" })).not.toBeInTheDocument());
    expect(screen.getByRole("link", { name: "INC-100045" })).toBeInTheDocument();
  });

  it("filters by category and then offers that category's subcategories", async () => {
    const api = backend();
    const { user } = renderApp("/analyst/tickets");
    await screen.findByRole("table");
    expect(screen.getByLabelText("Subcategory")).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Category"), "Network");
    await waitFor(() => expect(lastList(api).get("category")).toBe("Network"));
    const sub = screen.getByLabelText("Subcategory");
    expect(sub).toBeEnabled();
    expect(within(sub).getAllByRole("option").map((o) => o.textContent)).toEqual(["All subcategory", "DNS", "VPN"]);
    await user.selectOptions(sub, "VPN");
    await waitFor(() => expect(lastList(api).get("subcategory")).toBe("VPN"));
    // changing the category resets the subcategory
    await user.selectOptions(screen.getByLabelText("Category"), "Identity & Access");
    await waitFor(() => expect(lastList(api).get("category")).toBe("Identity & Access"));
    expect(lastList(api).get("subcategory")).toBeNull();
  });

  it("filters by priority", async () => {
    const api = backend();
    const { user } = renderApp("/analyst/tickets");
    await screen.findByRole("table");
    await user.selectOptions(screen.getByLabelText("Priority"), "P1");
    await waitFor(() => expect(lastList(api).get("priority")).toBe("P1"));
    await waitFor(() => expect(screen.queryByRole("link", { name: "INC-100045" })).not.toBeInTheDocument());
    expect(screen.getByRole("link", { name: "INC-100031" })).toBeInTheDocument();
  });

  it("combines filters, shows them as chips, and clears them", async () => {
    const api = backend();
    const { user } = renderApp("/analyst/tickets");
    await screen.findByRole("table");
    await user.selectOptions(screen.getByLabelText("Service"), "Corporate VPN");
    await user.selectOptions(screen.getByLabelText("Priority"), "P2");
    await user.selectOptions(screen.getByLabelText("Status"), "resolved");
    await waitFor(() => {
      const q = lastList(api);
      expect([q.get("affected_service"), q.get("priority"), q.get("status")]).toEqual(["Corporate VPN", "P2", "resolved"]);
    });
    const chips = screen.getByLabelText("Active filters");
    expect(within(chips).getByText("Corporate VPN")).toBeInTheDocument();

    await user.click(within(chips).getByRole("button", { name: "Remove Priority filter" }));
    await waitFor(() => expect(lastList(api).get("priority")).toBeNull());
    expect(lastList(api).get("status")).toBe("resolved");

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(lastList(api).toString()).toBe(""));
    expect(screen.queryByLabelText("Active filters")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Service")).toHaveValue("");
  });

  it("shows an empty state when nothing matches, with a way out", async () => {
    backend({ "GET /api/tickets": (req) => (req.query.get("q") ? ok([]) : ok(TICKETS)) });
    const { user } = renderApp("/analyst/tickets");
    await screen.findByRole("table");
    await user.type(screen.getByRole("searchbox", { name: "Search tickets" }), "printer on fire");
    expect(await screen.findByText("No tickets match your search")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear search and filters" }));
    expect(await screen.findByRole("link", { name: "INC-100045" })).toBeInTheDocument();
  });

  it("clears both the search and the filters in one click", async () => {
    const api = backend({ "GET /api/tickets": (req) => (req.query.toString() ? ok([]) : ok(TICKETS)) });
    const { user } = renderApp("/analyst/tickets?q=printer&priority=P1");
    expect(await screen.findByText("No tickets match your search")).toBeInTheDocument();
    const before = api.callsTo("GET", "/api/tickets").length;
    await user.click(screen.getByRole("button", { name: "Clear search and filters" }));
    expect(await screen.findByRole("link", { name: "INC-100045" })).toBeInTheDocument();
    // exactly one request, with nothing in it: no half-cleared request in between
    expect(api.callsTo("GET", "/api/tickets").slice(before).map((c) => c.query.toString())).toEqual([""]);
    expect(screen.getByRole("searchbox", { name: "Search tickets" })).toHaveValue("");
    expect(screen.getByLabelText("Priority")).toHaveValue("");
    expect(lastList(api).toString()).toBe("");
  });

  it("shows a retryable error when the backend fails, and recovers", async () => {
    let failing = true;
    backend({ "GET /api/tickets": (req) => (failing ? fail(500, "INTERNAL_ERROR", "x") : listHandler(req)) });
    const { user } = renderApp("/analyst/tickets");
    expect(await screen.findByText("Server error")).toBeInTheDocument();
    failing = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("link", { name: "INC-100045" })).toBeInTheDocument();
  });

  it("explains a network failure", async () => {
    backend({ "GET /api/tickets": "network" });
    renderApp("/analyst/tickets");
    expect(await screen.findByText("Backend unreachable")).toBeInTheDocument();
  });

  it("returns to the login page when the session expires (401)", async () => {
    backend({ "GET /api/tickets": fail(401, "UNAUTHORIZED", "expired") });
    renderApp("/analyst/tickets");
    expect(await screen.findByRole("heading", { level: 1, name: "IT staff sign in" })).toBeInTheDocument();
    expect(sessionStorage.length).toBe(0);
  });

  it("shows access denied for a 403", async () => {
    backend({ "GET /api/tickets": fail(403, "FORBIDDEN", "This needs the analyst role.") });
    renderApp("/analyst/tickets");
    expect(await screen.findByText("Not allowed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("keeps working when the filter values cannot be loaded", async () => {
    backend({ "GET /api/tickets/taxonomy": fail(500, "INTERNAL_ERROR", "x") });
    renderApp("/analyst/tickets");
    expect(await screen.findByText("Filter values could not be loaded. Search still works.")).toBeInTheDocument();
    expect(screen.getByLabelText("Category")).toBeDisabled();
    expect(await screen.findByRole("link", { name: "INC-100045" })).toBeInTheDocument();
  });
});

describe("ticket detail", () => {
  beforeEach(() => signIn());

  it("opens from the list and goes back with the filters kept", async () => {
    const api = backend();
    const { user } = renderApp("/analyst/tickets");
    await screen.findByRole("table");
    await user.selectOptions(screen.getByLabelText("Service"), "Corporate VPN");
    await waitFor(() => expect(screen.queryByRole("link", { name: "INC-100031" })).not.toBeInTheDocument());
    await user.click(screen.getByRole("link", { name: "INC-100045" }));
    expect(await screen.findByRole("heading", { level: 1, name: TICKET_DETAIL.subject! })).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "Back to tickets" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Incident Management" })).toBeInTheDocument();
    expect(screen.getByLabelText("Service")).toHaveValue("Corporate VPN");
    await waitFor(() => expect(lastList(api).get("affected_service")).toBe("Corporate VPN"));
  });

  it("shows the ticket in clear sections", async () => {
    backend();
    renderApp("/analyst/tickets/INC-100045");
    expect(await screen.findByRole("heading", { level: 1, name: TICKET_DETAIL.subject! })).toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByText("INC-100045")).toBeInTheDocument();
    expect(screen.getByText(TICKET_DETAIL.description!)).toBeInTheDocument();
    expect(screen.getByText(TICKET_DETAIL.root_cause!)).toBeInTheDocument();
    expect(screen.getByText("Root cause: gateway cipher change. Fix confirmed with the requester.")).toBeInTheDocument();
    const steps = screen.getByRole("heading", { name: "Steps taken" }).nextElementSibling!;
    expect(within(steps as HTMLElement).getAllByRole("listitem").map((li) => li.textContent))
      .toEqual(["Confirm the rekey failure in the gateway log", "Upgrade the VPN client to the supported version"]);
    expect(screen.getByText("Network › VPN")).toBeInTheDocument();
    expect(screen.getByText("Network Operations")).toBeInTheDocument();
    expect(screen.getByText("3 h 20 min")).toBeInTheDocument();
    expect(screen.getByText("PRB-0040")).toBeInTheDocument();
    // breadcrumb shows where we are
    expect(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByText("INC-100045")).toBeInTheDocument();
  });

  it("says when a ticket does not exist", async () => {
    backend({ "GET /api/tickets/INC-999999": fail(404, "TICKET_NOT_FOUND", "No historical ticket has this ID.") });
    renderApp("/analyst/tickets/INC-999999");
    expect(await screen.findByText("Ticket not found")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to tickets" })).toBeInTheDocument();
  });

  it("shows when a ticket has no resolution yet", async () => {
    backend({ "GET /api/tickets/INC-100031": ok({ ...TICKETS[1], resolved_at: null, channel: null, description: "x",
                                                    root_cause: null, problem_id: null, resolution_text: null }) });
    renderApp("/analyst/tickets/INC-100031");
    expect(await screen.findByText("No resolution has been recorded yet (status: open).")).toBeInTheDocument();
    expect(screen.getByText("Not resolved")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Related" })).not.toBeInTheDocument();
  });
});
