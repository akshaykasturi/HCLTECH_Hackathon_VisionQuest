import { beforeEach, describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { LOGIN_OK, PRESETS, RESULT_BLOCKED, RESULT_CLARIFY, RESULT_ESCALATE, RESULT_RECOMMEND } from "./fixtures";
import { analystRoutes, deferred, fail, mockBackend, ok, renderApp, signIn, type Handler, type Reply } from "./utils";

function backend(extra: Record<string, Handler> = {}) {
  return mockBackend({ ...analystRoutes(), ...extra });
}

async function fillAndSubmit(user: ReturnType<typeof renderApp>["user"]) {
  await user.type(screen.getByLabelText(/Subject/), "VPN fails with error 809");
  await user.type(screen.getByLabelText(/Description/), "Since this morning the VPN fails from home.");
  await user.click(screen.getByRole("button", { name: "Analyze ticket" }));
}

describe("analyze page", () => {
  beforeEach(() => signIn());

  it("starts with a helpful empty state", async () => {
    backend();
    renderApp("/analyst/analyze");
    expect(await screen.findByText("No ticket analysed yet")).toBeInTheDocument();
  });

  it("validates the form before calling the backend", async () => {
    const api = backend();
    const { user } = renderApp("/analyst/analyze");
    await user.click(screen.getByRole("button", { name: "Analyze ticket" }));
    expect(screen.getByText("Enter a short subject.")).toBeInTheDocument();
    expect(screen.getByText("Describe the problem.")).toBeInTheDocument();
    expect(screen.getByLabelText(/Subject/)).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText(/Subject/)).toHaveFocus();
    await user.type(screen.getByLabelText(/Subject/), "ab");
    await user.click(screen.getByRole("button", { name: "Analyze ticket" }));
    expect(screen.getByText("The subject needs at least 3 characters.")).toBeInTheDocument();
    expect(api.callsTo("POST", "/api/tickets/analyze")).toHaveLength(0);
  });

  it("sends the ticket with the login token and shows a loading state", async () => {
    const gate = deferred<Reply>();
    const api = backend({ "POST /api/tickets/analyze": () => gate.promise });
    const { user } = renderApp("/analyst/analyze");
    await user.selectOptions(screen.getByLabelText("Environment"), "production");
    await fillAndSubmit(user);
    expect(screen.getByRole("button", { name: /Analysing/ })).toBeDisabled();
    expect(screen.getByText("Analysing the ticket…")).toBeInTheDocument();
    gate.resolve(ok(RESULT_RECOMMEND));
    expect(await screen.findByRole("region", { name: "Outcome: Recommended fix" })).toBeInTheDocument();

    const call = api.callsTo("POST", "/api/tickets/analyze")[0];
    expect(call.body).toEqual({ subject: "VPN fails with error 809", description: "Since this morning the VPN fails from home.",
                                affected_service: null, environment: "production", channel: null });
    const init = api.fetchMock.mock.calls.find(([u]) => String(u).endsWith("/api/tickets/analyze"))?.[1];
    expect((init?.headers as Record<string, string>).Authorization).toBe(`Bearer ${LOGIN_OK.token}`);
  });

  it("shows a RECOMMEND result with classification, cited steps, similar tickets and KB evidence", async () => {
    backend({ "POST /api/tickets/analyze": ok(RESULT_RECOMMEND) });
    const { user } = renderApp("/analyst/analyze");
    await fillAndSubmit(user);
    const banner = await screen.findByRole("region", { name: "Outcome: Recommended fix" });
    expect(banner).toHaveAttribute("data-outcome", "RECOMMEND");
    expect(within(banner).getByText("86%")).toBeInTheDocument();
    expect(screen.getByText("Confidence 93%")).toBeInTheDocument();
    expect(screen.getByText("Upgrade the VPN client to the supported version")).toBeInTheDocument();
    expect(screen.getByText(/through the deployment console/)).toBeInTheDocument();
    expect(screen.getByText("Likely duplicate")).toBeInTheDocument();
    expect(screen.getByText("Lookalike")).toBeInTheDocument();
    expect(screen.getByText("Resolution steps by cause")).toBeInTheDocument();
  });

  it("shows the clarifying question for CLARIFY and no invented classification", async () => {
    backend({ "POST /api/tickets/analyze": ok(RESULT_CLARIFY) });
    const { user } = renderApp("/analyst/analyze");
    await fillAndSubmit(user);
    const banner = await screen.findByRole("region", { name: "Outcome: More information needed" });
    expect(within(banner).getByText(RESULT_CLARIFY.question!)).toBeInTheDocument();
    expect(screen.getByText(/No classification was returned/)).toBeInTheDocument();
    expect(screen.queryByText(/^Confidence \d+%$/)).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Recommended resolution steps" })).not.toBeInTheDocument();
  });

  it("shows the escalation group for ESCALATE", async () => {
    backend({ "POST /api/tickets/analyze": ok(RESULT_ESCALATE) });
    const { user } = renderApp("/analyst/analyze");
    await fillAndSubmit(user);
    const banner = await screen.findByRole("region", { name: "Outcome: Escalate to a specialist team" });
    expect(within(banner).getByText("Site Reliability")).toBeInTheDocument();
  });

  it("shows BLOCKED without evidence or steps", async () => {
    backend({ "POST /api/tickets/analyze": ok(RESULT_BLOCKED) });
    const { user } = renderApp("/analyst/analyze");
    await fillAndSubmit(user);
    const banner = await screen.findByRole("region", { name: "Outcome: Request blocked" });
    expect(within(banner).getByText("Screen codes: INJECTION_PATTERN")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Similar past tickets" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Classification" })).not.toBeInTheDocument();
  });

  it("shows a retryable error when the analysis fails, and recovers", async () => {
    let failing = true;
    backend({ "POST /api/tickets/analyze": () => (failing ? fail(500, "ANALYSIS_FAILED", "x") : ok(RESULT_CLARIFY)) });
    const { user } = renderApp("/analyst/analyze");
    await fillAndSubmit(user);
    expect(await screen.findByText("Analysis failed")).toBeInTheDocument();
    failing = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("region", { name: "Outcome: More information needed" })).toBeInTheDocument();
  });

  it("rejects a malformed response instead of rendering it", async () => {
    backend({ "POST /api/tickets/analyze": ok({ result_id: "x", outcome: "MAYBE" }) });
    const { user } = renderApp("/analyst/analyze");
    await fillAndSubmit(user);
    expect(await screen.findByText("Unexpected response")).toBeInTheDocument();
  });

  it("returns to the login page when the session has expired", async () => {
    backend({ "POST /api/tickets/analyze": fail(401, "UNAUTHORIZED", "expired") });
    const { user } = renderApp("/analyst/analyze");
    await fillAndSubmit(user);
    expect(await screen.findByRole("heading", { level: 1, name: "IT staff sign in" })).toBeInTheDocument();
    expect(screen.getByText("Your session has ended. Please sign in again.")).toBeInTheDocument();
    expect(sessionStorage.length).toBe(0);
  });

  it("fills the form from a demo ticket", async () => {
    backend();
    const { user } = renderApp("/analyst/analyze");
    await user.selectOptions(await screen.findByLabelText("Load a demo ticket"), PRESETS[0].id);
    expect(screen.getByLabelText(/Subject/)).toHaveValue(PRESETS[0].ticket.subject);
    expect(screen.getByLabelText("Affected service")).toHaveValue("Corporate VPN");
    expect(screen.getByLabelText("Channel")).toHaveValue("portal");
  });
});

describe("analyst feedback", () => {
  beforeEach(() => signIn());

  it("sends Useful feedback with a note", async () => {
    const api = backend({ "POST /api/tickets/analyze": ok(RESULT_RECOMMEND),
                          "POST /api/tickets/results/res-0001/feedback": ok({ feedback_id: "fb-1" }, 201) });
    const { user } = renderApp("/analyst/analyze");
    await fillAndSubmit(user);
    await screen.findByRole("region", { name: "Outcome: Recommended fix" });
    const send = screen.getByRole("button", { name: "Send feedback" });
    expect(send).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Useful" }));
    expect(screen.getByRole("button", { name: "Useful" })).toHaveAttribute("aria-pressed", "true");
    await user.type(screen.getByLabelText("Note (optional)"), "Step 1 fixed it");
    await user.click(send);
    expect(await screen.findByText(/Feedback saved: useful/)).toBeInTheDocument();
    expect(api.callsTo("POST", "/api/tickets/results/res-0001/feedback")[0].body)
      .toEqual({ verdict: "useful", note: "Step 1 fixed it" });
  });

  it("explains that saving is pending while the database is not connected", async () => {
    backend({ "POST /api/tickets/analyze": ok(RESULT_CLARIFY),
              "POST /api/tickets/results/res-0002/feedback": fail(503, "DB_NOT_READY", "Saving is not available yet") });
    const { user } = renderApp("/analyst/analyze");
    await fillAndSubmit(user);
    await screen.findByRole("region", { name: "Outcome: More information needed" });
    await user.click(screen.getByRole("button", { name: "Unresolved" }));
    await user.click(screen.getByRole("button", { name: "Send feedback" }));
    expect(await screen.findByText(/results database is not connected/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send feedback" })).toBeEnabled();
  });
});
