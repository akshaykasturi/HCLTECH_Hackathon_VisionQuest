// Mirrors the backend contract: backend/app/main.py (health) and backend/app/common/api.py + audit.py (audit).
// Field names are the backend's own; do not rename them. Problem-specific types go here tomorrow.

export interface Envelope<T> {
  ok: boolean;
  data: T | null;
  error: { code: string; message: string } | null;
}

/** GET /health data. */
export interface HealthData {
  api: string; // "up"
  database: string; // "up" | "down"
  warmup: string; // "warming" | "ready" | "failed"
  warmup_detail: string;
}

/** GET /api/audit/results item (newest first). */
export interface ResultSummary {
  result_id: string;
  case_id: string;
  recorded_at: string;
  request_id: string | null;
  outcome: string | null;
  summary: string | null;
  feedback_count: number;
}

/** GET /api/audit/results/{id}: the saved result, masked. Its shape is decided in docs/PLAN.md. */
export type SavedResult = { result_id: string; case_id: string } & Record<string, unknown>;

export type Verdict = "confirm" | "reject" | "annotate";

export interface FeedbackRequest {
  verdict: Verdict;
  item_id?: string;
  note?: string;
}

export interface FeedbackRecord {
  feedback_id: string;
  result_id: string;
  case_id: string;
  verdict: string;
  item_id: string | null;
  note: string | null;
  created_at: string;
  request_id: string | null;
}

/** GET /api/audit/verify data. */
export interface ChainStatus {
  valid: boolean;
  events_checked: number;
  head_hash: string;
  first_bad_seq: number | null;
  reason: string | null;
}

// ---------- Auth: backend/app/auth/router.py ----------

export type Role = "analyst" | "lead";

export interface User {
  username: string;
  role: Role;
}

/** POST /api/auth/login data. */
export interface LoginData {
  token: string;
  token_type: string;
  expires_at: string;
  user: User;
}

// ---------- Tickets: backend/app/tickets/models.py and backend/app/api/tickets.py ----------

export type Outcome = "RECOMMEND" | "CLARIFY" | "ESCALATE" | "BLOCKED";
export const OUTCOMES: readonly Outcome[] = ["RECOMMEND", "CLARIFY", "ESCALATE", "BLOCKED"];
export type MatchLabel = "likely_duplicate" | "related" | "weak";
export type TicketVerdict = "useful" | "incorrect" | "unresolved";

/** A new ticket as the analyst submits it (TicketIn). */
export interface TicketIn {
  subject: string;
  description: string;
  affected_service?: string | null;
  environment?: string | null;
  channel?: string | null;
}

export interface ScreenResult {
  blocked: boolean;
  safety: boolean;
  codes: string[];
}

export interface LabelScore {
  label: string;
  prob: number;
}

export interface Classification {
  category: LabelScore;
  subcategory: LabelScore;
  priority: LabelScore;
  top3_category: LabelScore[];
  key_terms: string[];
  priority_rule: string | null;
  model_version: string;
}

export interface SimilarTicket {
  ticket_id: string;
  subject: string;
  category: string;
  subcategory: string | null;
  status: string;
  score: number;
  match: MatchLabel;
  why: string[];
  lookalike: boolean;
  problem_id: string | null;
  root_cause: string | null;
  resolution_text: string | null;
  past_feedback: Record<string, number>;
}

export interface KBHit {
  section_id: string;
  title: string;
  text: string;
  score: number;
}

export interface Step {
  text: string;
  source_id: string;
  excerpt: string;
  why: string;
}

export interface Recommendation {
  summary: string;
  steps: Step[];
  escalation_group: string | null;
  generator: string;
  prompt_version: string;
}

export interface Finding {
  code: string;
  severity: "info" | "warn" | "block";
  message: string;
  where: string;
}

export interface TraceStep {
  step: string;
  ms: number;
  detail: string;
}

/** POST /api/tickets/analyze data. The ticket inside is the masked copy. */
export interface AnalysisResult {
  result_id: string;
  created_at: string;
  recommended_at: string | null;
  ticket: TicketIn;
  screen: ScreenResult;
  classification: Classification | null;
  similar: SimilarTicket[];
  kb: KBHit[];
  outcome: Outcome;
  reasons: string[];
  confidence: number;
  question: string | null;
  escalation_group: string | null;
  recommendation: Recommendation | null;
  findings: Finding[];
  timings_ms: Record<string, number>;
  trace: TraceStep[];
}

/** GET /api/tickets/presets item. */
export interface Preset {
  id: string;
  label: string;
  expected_outcome: Outcome | null;
  ticket: TicketIn;
}

/** GET /api/tickets/taxonomy data. */
export interface Taxonomy {
  category: string[];
  priority: string[];
  status: string[];
  assigned_team: string[];
  affected_service: string[];
  environment: string[];
  subcategory: Record<string, string[]>;
}

/** POST /api/tickets/results/{result_id}/feedback body (FeedbackIn). */
export interface TicketFeedbackIn {
  verdict: TicketVerdict;
  note?: string;
  target?: "result" | "match";
  target_id?: string;
}
