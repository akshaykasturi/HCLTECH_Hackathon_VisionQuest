import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { LockKeyhole } from "lucide-react";
import { Button, Card, ErrorAlert } from "../components/common/ui";
import { ANALYST_HOME } from "../lib/nav";
import { useAuth } from "../state/Auth";

const input = "mt-1.5 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-ink shadow-sm placeholder:text-slate-400 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20";

/** Sign-in for IT staff (analyst / lead). POST /api/auth/login; the token is kept in sessionStorage. */
export function LoginPage() {
  const { user, expired, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from;
  const target = from && from.startsWith("/analyst") ? from : ANALYST_HOME;

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  if (user && !busy) return <Navigate to={target} replace />;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!username.trim() || !password) {
      setMissing(true);
      return;
    }
    setMissing(false);
    setError(null);
    setBusy(true);
    try {
      await login(username.trim(), password);
      navigate(target, { replace: true });
    } catch (err) {
      setError(err);
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex max-w-md flex-col px-4 py-16 md:py-24">
      <div className="mb-6 text-center">
        <span className="mx-auto grid h-11 w-11 place-items-center rounded-xl bg-primary-soft text-primary">
          <LockKeyhole className="h-5 w-5" aria-hidden />
        </span>
        <h1 className="mt-4 text-2xl font-semibold tracking-tight text-ink">IT staff sign in</h1>
        <p className="mt-1 text-sm text-muted">For support analysts and team leads.</p>
      </div>
      <Card as="div" className="p-6">
        {expired && !error && (
          <p role="status" className="mb-4 rounded-lg bg-warning-soft px-3 py-2 text-sm text-warning-text">
            Your session has ended. Please sign in again.
          </p>
        )}
        {error !== null && <div className="mb-4"><ErrorAlert error={error} compact /></div>}
        <form onSubmit={onSubmit} noValidate className="space-y-4">
          <div>
            <label htmlFor="username" className="text-sm font-medium text-ink">Username</label>
            <input id="username" name="username" autoComplete="username" value={username}
                   onChange={(e) => setUsername(e.target.value)} className={input}
                   aria-invalid={missing && !username.trim() ? true : undefined} />
          </div>
          <div>
            <label htmlFor="password" className="text-sm font-medium text-ink">Password</label>
            <input id="password" name="password" type="password" autoComplete="current-password" value={password}
                   onChange={(e) => setPassword(e.target.value)} className={input}
                   aria-invalid={missing && !password ? true : undefined} />
          </div>
          {missing && <p role="alert" className="text-sm text-danger-text">Enter your username and password.</p>}
          <Button type="submit" variant="primary" loading={busy} loadingText="Signing in…" className="w-full">Sign in</Button>
        </form>
      </Card>
    </div>
  );
}
