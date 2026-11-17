// Who is signed in. The token lives in sessionStorage (lib/session.ts); api.ts sends it on every request and
// fires UNAUTHORIZED_EVENT on a 401, which signs the user out here and sends them back to the login page.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { ApiError } from "../lib/api";
import { ANALYST_HOME } from "../lib/nav";
import { clearSession, readSession, saveSession, UNAUTHORIZED_EVENT, type Session } from "../lib/session";
import { STAFF_ROLES, type Role, type User } from "../lib/types";
import { useAppData } from "./AppData";

interface AuthState {
  user: User | null;
  /** True after the backend rejected the token, so the login page can say why. */
  expired: boolean;
  login: (username: string, password: string) => Promise<User>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

const isStaff = (user: User) => STAFF_ROLES.includes(user.role);

/** The stored session if it belongs to IT staff. An employee session cannot open the console: remove it. */
function staffSession(): Session | null {
  const session = readSession();
  if (session && !isStaff(session.user)) {
    clearSession();
    return null;
  }
  return session;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const { api } = useAppData();
  const [session, setSession] = useState<Session | null>(() => staffSession());
  const [expired, setExpired] = useState(false);

  useEffect(() => {
    const onUnauthorized = () => {
      clearSession();
      setSession(null);
      setExpired(true);
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const data = await api.login(username, password);
    if (!isStaff(data.user)) {
      // A valid employee login, but this sign-in is the analyst console: keep nothing (the token is dropped
      // here, never stored). The backend would refuse every console request with 403 anyway.
      throw new ApiError("http", "EMPLOYEE_NOT_ALLOWED",
                         "Employee access is not available yet. Please use the employee support portal.", 403);
    }
    const next = saveSession(data);
    setSession(next);
    setExpired(false);
    return next.user;
  }, [api]);

  const logout = useCallback(() => {
    clearSession();
    setSession(null);
    setExpired(false);
  }, []);

  const value = useMemo<AuthState>(() => ({ user: session?.user ?? null, expired, login, logout }),
    [session, expired, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

const ROLE_LEVEL: Record<Role, number> = { employee: 0, analyst: 1, lead: 2 };

/** Renders its children only for a signed-in user with at least `role`; otherwise goes to the login page. */
export function RequireAuth({ children, role = "analyst" }: { children: ReactNode; role?: Role }) {
  const { user } = useAuth();
  const location = useLocation();
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (ROLE_LEVEL[user.role] < ROLE_LEVEL[role]) {
    // Staff without enough rights go to the console home; anyone else back to sign-in.
    return <Navigate to={isStaff(user) ? ANALYST_HOME : "/login"} replace />;
  }
  return <>{children}</>;
}
