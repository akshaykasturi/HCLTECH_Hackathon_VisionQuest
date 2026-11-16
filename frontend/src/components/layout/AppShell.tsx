import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { ChevronRight, Menu, Radar, X } from "lucide-react";
import { APP_NAME, breadcrumbs, NAV_ITEMS } from "../../lib/nav";
import { deriveHealth } from "../../lib/health";
import { useHealth } from "../../state/AppData";
import { HealthBadge } from "../common/status";

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <ul className="space-y-1">
      {NAV_ITEMS.map(({ to, label, Icon, end }) => (
        <li key={to}>
          <NavLink to={to} end={end} onClick={onNavigate}
                   className={({ isActive }) =>
                     `group flex items-center gap-3 rounded-lg border-l-2 px-3 py-2 text-sm font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-400 ${
                       isActive ? "border-indigo-400 bg-white/10 text-white" : "border-transparent text-slate-300 hover:bg-white/5 hover:text-white"}`}>
            <Icon className="h-4 w-4 shrink-0" aria-hidden />
            {label}
          </NavLink>
        </li>
      ))}
    </ul>
  );
}

function Brand() {
  return (
    <Link to="/" className="flex items-center gap-2.5 rounded-lg px-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400">
      <span className="grid h-8 w-8 place-items-center rounded-lg bg-primary text-white"><Radar className="h-4 w-4" aria-hidden /></span>
      <span className="leading-tight">
        <span className="block text-sm font-semibold text-white">{APP_NAME}</span>
        <span className="block text-xs text-slate-400">HCLTech hackathon</span>
      </span>
    </Link>
  );
}

const WARMUP_LABEL = { healthy: "Online", not_ready: "Warming up", unavailable: "Warm-up failed", unknown: undefined };

function BackendStatus() {
  const { data, error, loading } = useHealth();
  const health = deriveHealth(data, error);
  if (loading && !data) return <span className="text-xs text-muted">Checking backend…</span>;
  return (
    <Link to="/system-health" className="flex items-center gap-2 rounded-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
          aria-label={`Backend status: API ${health.api.replace("_", " ")}, warm-up ${health.warmup.replace("_", " ")}. Open System Health`}>
      <span className="hidden text-xs text-muted sm:inline">Backend</span>
      <HealthBadge state={health.api === "healthy" && health.warmup !== "healthy" ? health.warmup : health.api}
                   label={health.api === "healthy" ? WARMUP_LABEL[health.warmup] : undefined} />
    </Link>
  );
}

export function AppShell() {
  const location = useLocation();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const mainRef = useRef<HTMLElement>(null);
  const firstRender = useRef(true);
  const menuButton = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLDivElement>(null);

  // Move focus to the page content after navigation (not on the first load), for keyboard and screen-reader users.
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    mainRef.current?.focus();
  }, [location.pathname]);

  useEffect(() => {
    if (!drawerOpen) return;
    drawerRef.current?.querySelector<HTMLElement>("nav a")?.focus(); // first navigation item, not the logo
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setDrawerOpen(false);
        menuButton.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  const crumbs = breadcrumbs(location.pathname);

  return (
    <div className="min-h-screen bg-page text-ink">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:shadow">
        Skip to content
      </a>

      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col bg-sidebar px-3 py-5 md:flex" aria-label="Sidebar">
        <Brand />
        <nav aria-label="Main navigation" className="mt-8 flex-1"><NavList /></nav>
        <p className="px-3 text-xs leading-relaxed text-slate-500">Code decides, the LLM explains, code checks. Only evidence or a reviewer confirms.</p>
      </aside>

      {drawerOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <button type="button" className="absolute inset-0 bg-slate-900/50" aria-label="Close navigation"
                  onClick={() => setDrawerOpen(false)} tabIndex={-1} />
          <div ref={drawerRef} role="dialog" aria-modal="true" aria-label="Navigation"
               className="absolute inset-y-0 left-0 flex w-72 flex-col bg-sidebar px-3 py-5 shadow-xl">
            <div className="flex items-center justify-between pr-1">
              <Brand />
              <button type="button" onClick={() => { setDrawerOpen(false); menuButton.current?.focus(); }}
                      className="rounded-lg p-2 text-slate-300 hover:bg-white/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400"
                      aria-label="Close navigation">
                <X className="h-5 w-5" aria-hidden />
              </button>
            </div>
            <nav aria-label="Mobile navigation" className="mt-8"><NavList onNavigate={() => setDrawerOpen(false)} /></nav>
          </div>
        </div>
      )}

      <div className="md:pl-64">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-slate-200 bg-white/90 px-4 backdrop-blur md:px-6">
          <button ref={menuButton} type="button" onClick={() => setDrawerOpen(true)} aria-expanded={drawerOpen}
                  className="rounded-lg p-2 text-ink hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary md:hidden"
                  aria-label="Open navigation">
            <Menu className="h-5 w-5" aria-hidden />
          </button>
          <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
            <ol className="flex items-center gap-1 truncate text-sm text-muted">
              {crumbs.map((c, i) => (
                <li key={`${c.label}-${i}`} className="flex min-w-0 items-center gap-1">
                  {i > 0 && <ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden />}
                  {c.to && i < crumbs.length - 1
                    ? <Link to={c.to} className="rounded hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">{c.label}</Link>
                    : <span aria-current={i === crumbs.length - 1 ? "page" : undefined} className="truncate font-medium text-ink">{c.label}</span>}
                </li>
              ))}
            </ol>
          </nav>
          <BackendStatus />
        </header>
        <main id="main" ref={mainRef} tabIndex={-1} className="mx-auto max-w-7xl px-4 py-6 focus:outline-none md:px-6 md:py-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
