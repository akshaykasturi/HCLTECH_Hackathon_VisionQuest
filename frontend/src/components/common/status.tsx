import { AlertOctagon, CheckCircle2, CircleDashed, Clock3, HelpCircle } from "lucide-react";
import { formatConfidence } from "../../lib/format";
import type { HealthState } from "../../lib/health";
import { Badge, type Tone } from "./ui";

const healthLabel: Record<HealthState, string> = {
  healthy: "Healthy", not_ready: "Not ready", unavailable: "Unavailable", unknown: "Unknown",
};
const healthTone: Record<HealthState, Tone> = {
  healthy: "success", not_ready: "warning", unavailable: "danger", unknown: "neutral",
};

export function HealthBadge({ state, label }: { state: HealthState; label?: string }) {
  const Icon = { healthy: CheckCircle2, not_ready: Clock3, unavailable: AlertOctagon, unknown: HelpCircle }[state];
  return <Badge tone={healthTone[state]} icon={<Icon className="h-3.5 w-3.5" aria-hidden />}>{label ?? healthLabel[state]}</Badge>;
}

/** Any outcome string from the backend. RECOMMEND and CLARIFY get colours; anything else is neutral. */
export function OutcomeBadge({ outcome }: { outcome: string | null }) {
  if (!outcome) return <Badge>No outcome</Badge>;
  if (outcome === "RECOMMEND") {
    return <Badge tone="primary" icon={<CheckCircle2 className="h-3.5 w-3.5" aria-hidden />}>RECOMMEND</Badge>;
  }
  if (outcome === "CLARIFY") {
    return <Badge tone="warning" icon={<CircleDashed className="h-3.5 w-3.5" aria-hidden />}>CLARIFY</Badge>;
  }
  return <Badge>{outcome}</Badge>;
}

export function ConfidenceText({ value }: { value: number | null }) {
  return <span className="font-mono text-xs tabular-nums text-slate-700" title="Model confidence, not certainty">{formatConfidence(value)}</span>;
}
