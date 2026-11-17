// Compact badges for the history table. The big outcome banner is reused from components/analyze.
import type { Outcome, TicketVerdict } from "../../lib/types";
import { Badge, type Tone } from "../common/ui";

const OUTCOME: Record<Outcome, { tone: Tone; label: string }> = {
  RECOMMEND: { tone: "success", label: "Recommend" },
  CLARIFY: { tone: "warning", label: "Clarify" },
  ESCALATE: { tone: "danger", label: "Escalate" },
  BLOCKED: { tone: "neutral", label: "Blocked" },
};

const VERDICT: Record<TicketVerdict, { tone: Tone; label: string }> = {
  useful: { tone: "success", label: "Useful" },
  incorrect: { tone: "danger", label: "Incorrect" },
  unresolved: { tone: "warning", label: "Unresolved" },
};

export function OutcomeBadge({ outcome }: { outcome: Outcome }) {
  return <Badge tone={OUTCOME[outcome].tone}>{OUTCOME[outcome].label}</Badge>;
}

export function VerdictBadge({ verdict }: { verdict: TicketVerdict }) {
  const v = VERDICT[verdict] ?? { tone: "neutral" as Tone, label: verdict };
  return <Badge tone={v.tone}>{v.label}</Badge>;
}
