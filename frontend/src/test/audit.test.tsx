import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { fail, mockBackend, ok, renderApp } from "./utils";
import { CHAIN_BAD, CHAIN_OK, FEEDBACK_1, RESULT_1, RESULTS } from "./fixtures";

function auditBackend(extra = {}) {
  return mockBackend({
    "GET /api/audit/results": ok(RESULTS),
    "GET /api/audit/results/r-1": ok(RESULT_1),
    "GET /api/audit/results/r-1/feedback": ok(FEEDBACK_1),
    "GET /api/audit/verify": ok(CHAIN_OK),
    ...extra,
  });
}

describe("audit history", () => {
  it("lists saved results newest first", async () => {
    auditBackend();
    renderApp("/audit");
    expect(await screen.findByRole("button", { name: "r-2" })).toBeInTheDocument();
    const rows = screen.getAllByRole("button", { name: /^r-\d$/ }).map((b) => b.textContent);
    expect(rows).toEqual(["r-2", "r-1"]);
    expect(screen.getByText("Likely answer A.")).toBeInTheDocument();
  });

  it("shows an empty state when nothing is saved", async () => {
    mockBackend();
    renderApp("/audit");
    expect(await screen.findByText("No results saved yet")).toBeInTheDocument();
  });

  it("verifies the chain", async () => {
    const backend = auditBackend();
    const { user } = renderApp("/audit");
    await user.click(screen.getByRole("button", { name: "Verify chain" }));
    expect(await screen.findByText("Chain verified")).toBeInTheDocument();
    expect(screen.getByText("3 events checked, no gaps or edits found.")).toBeInTheDocument();
    expect(backend.callsTo("GET", "/api/audit/verify")).toHaveLength(1);
  });

  it("reports a broken chain", async () => {
    auditBackend({ "GET /api/audit/verify": ok(CHAIN_BAD) });
    const { user } = renderApp("/audit");
    await user.click(screen.getByRole("button", { name: "Verify chain" }));
    expect(await screen.findByText("Chain check failed")).toBeInTheDocument();
    expect(screen.getByText(/At event #2: hash does not match/)).toBeInTheDocument();
  });

  it("opens a result and its feedback from the list", async () => {
    auditBackend();
    const { user } = renderApp("/audit");
    await user.click(await screen.findByRole("button", { name: "r-1" }));
    const records = await screen.findByRole("list", { name: "Feedback records" });
    expect(within(records).getByText("confirm")).toBeInTheDocument();
    expect(within(records).getByText("Whole result")).toBeInTheDocument();
    expect(screen.getByLabelText("Result ID")).toHaveValue("r-1");
  });

  it("validates the ID before calling the backend", async () => {
    const backend = auditBackend();
    const { user } = renderApp("/audit");
    await user.type(screen.getByLabelText("Result ID"), "bad id!");
    await user.click(screen.getByRole("button", { name: "Look up" }));
    expect(screen.getByText(/Enter a result ID/)).toBeInTheDocument();
    expect(backend.calls.filter((c) => c.path.startsWith("/api/audit/results/"))).toHaveLength(0);
  });

  it("shows a clear message for an unknown result", async () => {
    auditBackend({ "GET /api/audit/results/r-404": fail(404, "RESULT_NOT_FOUND", "No saved result has this ID.") });
    const { user } = renderApp("/audit");
    await user.type(screen.getByLabelText("Result ID"), "r-404");
    await user.click(screen.getByRole("button", { name: "Look up" }));
    expect(await screen.findByText("Result not found")).toBeInTheDocument();
  });

  it("never renders backend text as HTML", async () => {
    auditBackend({ "GET /api/audit/results": ok([{ ...RESULTS[0], summary: "<img src=x onerror=alert(1)>" }]) });
    renderApp("/audit");
    expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });
});
