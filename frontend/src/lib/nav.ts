import { Activity, History, LayoutDashboard, ScrollText, Sparkles, Ticket, type LucideIcon } from "lucide-react";
import type { Role } from "./types";

export const APP_NAME = "VisionQuest";
export const ANALYST_HOME = "/analyst/analyze";

export interface NavItem {
  to: string;
  label: string;
  Icon: LucideIcon;
  end: boolean;
  /** Lowest role that sees the item (default analyst). The route is guarded too; the backend has the final say. */
  minRole?: Role;
}

// Analyst console pages (sidebar).
export const NAV_ITEMS: readonly NavItem[] = [
  { to: "/analyst/dashboard", label: "Dashboard", Icon: LayoutDashboard, end: false, minRole: "lead" },
  { to: "/analyst/analyze", label: "Analyze", Icon: Sparkles, end: false },
  { to: "/analyst/tickets", label: "Tickets", Icon: Ticket, end: false },
  { to: "/analyst/history", label: "Review History", Icon: History, end: false },
  { to: "/analyst/audit", label: "Audit History", Icon: ScrollText, end: false },
  { to: "/analyst/system-health", label: "System Health", Icon: Activity, end: false },
];

/** Breadcrumb trail for an analyst path (labels only; the last item is the current page). */
export function breadcrumbs(pathname: string): { label: string; to?: string }[] {
  const crumbs: { label: string; to?: string }[] = [{ label: "Analyst console", to: ANALYST_HOME }];
  const [, page, detail] = pathname.split("/").filter(Boolean);
  const item = NAV_ITEMS.find((n) => n.to === `/analyst/${page}`);
  if (!item) return [...crumbs, { label: "Not found" }];
  if (detail) return [...crumbs, { label: item.label, to: item.to }, { label: decodeURIComponent(detail) }];
  return [...crumbs, { label: item.label }];
}
