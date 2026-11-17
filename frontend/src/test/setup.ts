import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  sessionStorage.clear(); // every test starts signed out
  localStorage.clear();
});

// Node 25+ defines its own global localStorage/sessionStorage (undefined without --localstorage-file), which
// hides jsdom's. Use jsdom's real Storage objects so tests can check what the app writes.
const dom = (globalThis as unknown as { jsdom?: { window: Window } }).jsdom;
for (const name of ["localStorage", "sessionStorage"] as const) {
  if (dom && !globalThis[name]) {
    Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
  }
}

// jsdom has no layout: give scrollIntoView a no-op so focus/scroll code paths run.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
}
