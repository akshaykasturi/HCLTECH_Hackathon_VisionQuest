// Who is signed in. The token lives in sessionStorage (lib/session.ts); api.ts sends it on every request and
// fires UNAUTHORIZED_EVENT on a 401, which signs the user out here and sends them back to the login page.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { clearSession, readSession, saveSession, UNAUTHORIZED_EVENT, type Session } from "../lib/session";
import type { Role, User } from "../lib/types";
import { useAppData } from "./AppData";

interface AuthState {
  user: User | null;
  /** True after the backend rejected the token, so the login page can say why. */
  expired: boolean;
  login: (username: string, password: string) => Promise<User>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const { api } = useAppData();
  const [session, setSession] = useState<Session | null>(() => readSession());
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

const ROLE_LEVEL: Record<Role, number> = { analyst: 1, lead: 2 };

/** Renders its children only for a signed-in user with at least `role`; otherwise goes to the login page. */
export function RequireAuth({ children, role = "analyst" }: { children: ReactNode; role?: Role }) {
  const { user } = useAuth();
  const location = useLocation();
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (ROLE_LEVEL[user.role] < ROLE_LEVEL[role]) return <Navigate to="/analyst" replace />;
  return <>{children}</>;
}
