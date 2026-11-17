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
