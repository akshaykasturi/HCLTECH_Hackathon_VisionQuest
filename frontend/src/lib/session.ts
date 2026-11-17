// The signed-in session: the JWT from POST /api/auth/login, kept in sessionStorage (docs/ARCHITECTURE.md "Login").
// sessionStorage, not localStorage: the token disappears when the tab closes. Nothing else is ever stored.
import type { LoginData, User } from "./types";
import { ROLES } from "./types";

export const SESSION_KEY = "visionquest.session";
/** Fired when the backend answers 401 to a request that carried a token (expired or revoked). */
export const UNAUTHORIZED_EVENT = "visionquest:unauthorized";

export interface Session {
  token: string;
  expiresAt: string;
  user: User;
}

function storage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.sessionStorage : null;
  } catch {
    return null; // storage blocked by the browser: the user simply has to sign in again
  }
}

function isUser(value: unknown): value is User {
  const v = value as User | null;
  return !!v && typeof v.username === "string" && ROLES.includes(v.role);
}

/** The stored session, or null if there is none, it is malformed or it has expired. */
export function readSession(now: Date = new Date()): Session | null {
  const s = storage();
  if (!s) return null;
  try {
    const raw = s.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Session;
    const expires = Date.parse(parsed.expiresAt);
    if (typeof parsed.token !== "string" || !parsed.token || !isUser(parsed.user) || Number.isNaN(expires)) {
      s.removeItem(SESSION_KEY);
      return null;
    }
    if (expires <= now.getTime()) {
      s.removeItem(SESSION_KEY);
      return null;
    }
    return parsed;
  } catch {
    s.removeItem(SESSION_KEY);
    return null;
  }
}

export function saveSession(data: LoginData): Session {
  const session: Session = { token: data.token, expiresAt: data.expires_at, user: data.user };
  try {
    storage()?.setItem(SESSION_KEY, JSON.stringify(session));
  } catch {
    // storage full or blocked: the session still works for this page view
  }
  return session;
}

export function clearSession(): void {
  try {
    storage()?.removeItem(SESSION_KEY);
  } catch {
    // nothing to clear
  }
}
