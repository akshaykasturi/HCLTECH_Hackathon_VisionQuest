// Priority and status badges for historical tickets. Values are the data's own (P1..P4, open, resolved, ...).
import { humanize } from "../../lib/format";
import { Badge, type Tone } from "../common/ui";

// Colour only: the data says P1..P4 and nothing more, so the badge shows exactly that (P1 most urgent).
const PRIORITY: Record<string, Tone> = { P1: "danger", P2: "warning", P3: "primary", P4: "neutral" };

const STATUS: Record<string, Tone> = { open: "primary", in_progress: "warning", resolved: "success", closed: "neutral" };

export function PriorityBadge({ priority }: { priority: string | null | undefined }) {
  if (!priority) return <span className="text-muted">—</span>;
  return <Badge tone={PRIORITY[priority.toUpperCase()] ?? "neutral"}>{priority}</Badge>;
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="text-muted">—</span>;
  return <Badge tone={STATUS[status.toLowerCase()] ?? "neutral"}>{humanize(status)}</Badge>;
}
