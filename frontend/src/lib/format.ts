// Display-only formatting. Never changes the underlying values.

/** > 0.99 shows "> 0.99", < 0.01 shows "< 0.01", otherwise two decimals. */
export function formatConfidence(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (value > 0.99) return "> 0.99";
  if (value < 0.01) return "< 0.01";
  return value.toFixed(2);
}

const dateFormat = new Intl.DateTimeFormat("en-GB", {
  year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  timeZone: "UTC", hour12: false,
});

/** ISO timestamp -> "16 Nov 2026, 10:00:00 UTC". Unparseable input is shown as given. */
export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return `${dateFormat.format(date)} UTC`;
}

export function formatClock(date: Date): string {
  return date.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** "token_expired" -> "Token expired". */
export function humanize(identifier: string | null | undefined): string {
  if (!identifier) return "—";
  const text = identifier.replace(/[_-]+/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function shortHash(hash: string | null | undefined, size = 12): string {
  if (!hash) return "—";
  return hash.length > size ? `${hash.slice(0, size)}…` : hash;
}

/** Render any value as plain text (never HTML). */
export function valueToText(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

const dayFormat = new Intl.DateTimeFormat("en-GB", { year: "numeric", month: "short", day: "2-digit", timeZone: "UTC" });

/** ISO timestamp -> "16 Nov 2026" (UTC). Unparseable input is shown as given. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : dayFormat.format(date);
}

/** Time between two ISO timestamps as "3 h 20 min" / "2 d 4 h"; null if either is missing or invalid. */
export function formatDuration(fromIso: string | null | undefined, toIso: string | null | undefined): string | null {
  if (!fromIso || !toIso) return null;
  const ms = new Date(toIso).getTime() - new Date(fromIso).getTime();
  if (Number.isNaN(ms) || ms < 0) return null;
  const minutes = Math.round(ms / 60000);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ${minutes % 60} min`;
  return `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

/** Hours -> "45 min", "5.2 h" or "3.1 d" (one decimal). For durations such as MTTR. */
export function formatHours(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  if (hours < 48) return `${(Math.round(hours * 10) / 10).toFixed(1)} h`;
  return `${(Math.round((hours / 24) * 10) / 10).toFixed(1)} d`;
}

/** 0.4567 -> "46%". */
export function formatPercent(share: number): string {
  return `${Math.round(share * 100)}%`;
}
