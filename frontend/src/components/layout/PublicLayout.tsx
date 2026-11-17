// The employee-facing site: top navigation bar, page content, footer. No sidebar, no technical status.
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { LifeBuoy, Menu, X } from "lucide-react";
import { APP_NAME, ANALYST_HOME } from "../../lib/nav";
import { useAuth } from "../../state/Auth";

const focus = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary";

// Employee pages are added here as they are built (Submit a complaint, Track my complaint).
const PUBLIC_NAV = [{ to: "/", label: "Home", end: true }] as const;

function Brand() {
  return (
    <Link to="/" className={`flex items-center gap-2.5 rounded-lg ${focus}`}>
      <span className="grid h-9 w-9 place-items-center rounded-lg bg-navy text-white"><LifeBuoy className="h-5 w-5" aria-hidden /></span>
      <span className="leading-tight">
        <span className="block text-base font-semibold text-ink">{APP_NAME}</span>
        <span className="block text-xs text-muted">IT Support</span>
      </span>
    </Link>
  );
}

function StaffLink({ onClick }: { onClick?: () => void }) {
  const { user } = useAuth();
  return (
    <Link to={user ? ANALYST_HOME : "/login"} onClick={onClick}
          className={`inline-flex items-center justify-center rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm font-medium text-ink hover:bg-slate-50 ${focus}`}>
      {user ? "Analyst console" : "IT staff sign in"}
    </Link>
  );
}

export function PublicLayout() {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setOpen(false), [location.pathname]);

  const links = PUBLIC_NAV.map(({ to, label, end }) => (
    <NavLink key={to} to={to} end={end}
             className={({ isActive }) => `rounded-lg px-3 py-2 text-sm font-medium ${focus} ${
               isActive ? "text-primary" : "text-slate-600 hover:text-ink"}`}>
      {label}
    </NavLink>
  ));

  return (
    <div className="flex min-h-screen flex-col bg-white text-ink">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:shadow">
        Skip to content
      </a>
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-4 md:px-6">
          <Brand />
          <nav aria-label="Site navigation" className="hidden items-center gap-1 md:flex">{links}</nav>
          <div className="hidden md:block"><StaffLink /></div>
          <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-controls="site-menu"
                  aria-label={open ? "Close menu" : "Open menu"}
                  className={`rounded-lg p-2 text-ink hover:bg-slate-100 md:hidden ${focus}`}>
            {open ? <X className="h-5 w-5" aria-hidden /> : <Menu className="h-5 w-5" aria-hidden />}
          </button>
        </div>
        {open && (
          <div id="site-menu" className="border-t border-slate-200 px-4 py-3 md:hidden">
            <nav aria-label="Mobile site navigation" className="flex flex-col gap-1">{links}</nav>
            <div className="mt-3"><StaffLink onClick={() => setOpen(false)} /></div>
          </div>
        )}
      </header>

      <main id="main" tabIndex={-1} className="flex-1 focus:outline-none">
        <Outlet />
      </main>

      <footer className="border-t border-slate-200 bg-page">
        <div className="mx-auto flex max-w-6xl flex-col gap-2 px-4 py-6 text-sm text-muted md:flex-row md:items-center md:justify-between md:px-6">
          <p>© 2026 {APP_NAME} IT Support · HCLTech hackathon prototype</p>
          <p>Demo environment: all tickets and people shown are synthetic.</p>
        </div>
      </footer>
    </div>
  );
}
