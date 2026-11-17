import { beforeEach, describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { fail, mockBackend, ok, renderApp, signIn } from "./utils";
import { HISTORY_OPEN, HISTORY_RESOLVED } from "./fixtures";

const CHAIN_OK = { ok: true, first_broken_seq: null, n_events: 6 };
const CHAIN_BAD = { ok: false, first_broken_seq: 2, n_events: 2 };

function auditBackend(extra = {}) {
  return mockBackend({
    "GET /api/tickets/history": ok([HISTORY_RESOLVED, HISTORY_OPEN]),
    "GET /api/tickets/audit/verify": ok(CHAIN_OK),
    ...extra,
  });
}

describe("audit history (permanent database)", () => {
  beforeEach(() => signIn());

  it("lists saved results from /api/tickets/history, newest first", async () => {
    const backend = auditBackend();
    renderApp("/analyst/audit");
    const list = await screen.findByRole("list", { name: "Saved results list" });
    const ids = within(list).getAllByRole("button").map((b) => b.textContent);
    expect(ids).toEqual([HISTORY_RESOLVED.result_id, HISTORY_OPEN.result_id]);
    expect(within(list).getByText("VPN drops every few minutes")).toBeInTheDocument();
    expect(within(list).getByText("resolved")).toBeInTheDocument();
    expect(backend.callsTo("GET", "/api/tickets/history")).toHaveLength(1);
    expect(backend.calls.filter((c) => c.path.startsWith("/api/audit/"))).toHaveLength(0);   // not the legacy store
  });

  it("shows an empty state when nothing is saved", async () => {
    mockBackend();
    renderApp("/analyst/audit");
    expect(await screen.findByText("No results saved yet")).toBeInTheDocument();
  });

  it("opens a result with its feedback rows and resolution", async () => {
    auditBackend();
    const { user } = renderApp("/analyst/audit");
    await user.click(await screen.findByRole("button", { name: HISTORY_RESOLVED.result_id }));
    const records = await screen.findByRole("list", { name: "Feedback records" });
    expect(within(records).getByText("Whole result")).toBeInTheDocument();
    expect(within(records).getByText("Match INC-100777")).toBeInTheDocument();
    expect(within(records).getByText("Step 1 fixed it")).toBeInTheDocument();
    expect(screen.getByText(/by asha$/, { selector: "dd" })).toBeInTheDocument();
  });

  it("verifies the database chain", async () => {
    const backend = auditBackend();
    const { user } = renderApp("/analyst/audit");
    await user.click(screen.getByRole("button", { name: "Verify chain" }));
    expect(await screen.findByText("Chain verified")).toBeInTheDocument();
    expect(screen.getByText("6 events checked, no gaps or edits found.")).toBeInTheDocument();
    expect(backend.callsTo("GET", "/api/tickets/audit/verify")).toHaveLength(1);
  });

  it("reports a broken chain with the first broken event", async () => {
    auditBackend({ "GET /api/tickets/audit/verify": ok(CHAIN_BAD) });
    const { user } = renderApp("/analyst/audit");
    await user.click(screen.getByRole("button", { name: "Verify chain" }));
    expect(await screen.findByText("Chain check failed")).toBeInTheDocument();
    expect(screen.getByText(/Event #2 or the row it recorded was changed/)).toBeInTheDocument();
  });

  it("explains that the chain check needs the lead role", async () => {
    auditBackend({ "GET /api/tickets/audit/verify": fail(403, "FORBIDDEN", "This needs the lead role.") });
    const { user } = renderApp("/analyst/audit");
    await user.click(screen.getByRole("button", { name: "Verify chain" }));
    expect(await screen.findByText("Not allowed")).toBeInTheDocument();
  });

  it("never renders backend text as HTML", async () => {
    const evil = { ...HISTORY_RESOLVED, ticket_masked: { subject: "<img src=x onerror=alert(1)>" } };
    auditBackend({ "GET /api/tickets/history": ok([evil]) });
    renderApp("/analyst/audit");
    expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });
});
