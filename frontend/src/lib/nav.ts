import { Activity, House, ScrollText } from "lucide-react";

// Add tomorrow's pages here (and their routes in App.tsx).
export const NAV_ITEMS = [
  { to: "/", label: "Home", Icon: House, end: true },
  { to: "/audit", label: "Audit History", Icon: ScrollText, end: false },
  { to: "/system-health", label: "System Health", Icon: Activity, end: false },
] as const;

export const APP_NAME = "VisionQuest";

/** Breadcrumb trail for a path (labels only; the last item is the current page). */
export function breadcrumbs(pathname: string): { label: string; to?: string }[] {
  const crumbs: { label: string; to?: string }[] = [{ label: APP_NAME, to: "/" }];
  if (pathname === "/") return [...crumbs, { label: "Home" }];
  const [first, second] = pathname.split("/").filter(Boolean);
  const item = NAV_ITEMS.find((n) => n.to === `/${first}`);
  if (!item) return [...crumbs, { label: "Not found" }];
  if (second) return [...crumbs, { label: item.label, to: item.to }, { label: decodeURIComponent(second) }];
  return [...crumbs, { label: item.label }];
}
