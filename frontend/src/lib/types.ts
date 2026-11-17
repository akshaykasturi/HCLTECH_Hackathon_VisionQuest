// Mirrors the backend contract: backend/app/main.py (health) and backend/app/common/api.py + audit.py (audit).
// Field names are the backend's own; do not rename them.

export interface Envelope<T> {
  ok: boolean;
  data: T | null;
  error: { code: string; message: string } | null;
}

/** GET /api/health data (also served at /health). */
export interface HealthData {
  api: string; // "up"
  database: string; // "up" | "down" (the permanent database: SELECT 1)
  database_detail?: string; // e.g. "postgresql: SELECT 1 ok", or the error class when down
  warmup: string; // "warming" | "ready" | "failed"
  warmup_detail: string;
}

/** LEGACY local audit store: GET /api/audit/results item (newest first). */
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

/** GET /api/tickets/audit/verify: the permanent database's audit hash chain (backend/app/db/repo.py verify_chain). */
export interface DbChainStatus {
  ok: boolean;
  first_broken_seq: number | null;
  n_events: number;
}

// ---------- Auth: backend/app/auth/router.py ----------

/** Roles from backend/app/tickets/models.py, lowest first: employee < analyst < lead. */
export type Role = "employee" | "analyst" | "lead";
export const ROLES: readonly Role[] = ["employee", "analyst", "lead"];
/** Roles that may use the analyst console (the backend enforces the same with require_role("analyst")). */
export const STAFF_ROLES: readonly Role[] = ["analyst", "lead"];

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

/** GET /api/tickets item (LIST_FIELDS in backend/app/api/tickets.py). score and match only when searching with q. */
export interface TicketSummary {
  ticket_id: string;
  created_at: string | null;
  status: string | null;
  category: string | null;
  subcategory: string | null;
  priority: string | null;
  assigned_team: string | null;
  affected_service: string | null;
  environment: string | null;
  subject: string | null;
  score?: number;
  match?: MatchLabel;
}

/** GET /api/tickets/{ticket_id} data (DETAIL_FIELDS + resolution_text). Text is masked by the backend. */
export interface TicketDetail extends TicketSummary {
  resolved_at: string | null;
  channel: string | null;
  description: string | null;
  root_cause: string | null;
  problem_id: string | null;
  resolution_text: string | null;
}

/** GET /api/tickets query parameters (TicketFilters). Only set values are sent. */
export interface TicketQuery {
  q?: string;
  affected_service?: string;
  category?: string;
  subcategory?: string;
  priority?: string;
  status?: string;
}

// ---------- Review history: what the page works with ----------
// Frontend shape only. The backend record (db/repo.history, see HistoryRecordApi) and the adapter
// that converts it live in lib/api.ts (HistoryRecordApi, toHistoryEntry). Pages never see backend field names.

/** One feedback entry. Feedback is append-only: a changed opinion is a new entry and the newest counts. */
export interface FeedbackEntry {
  verdict: TicketVerdict;
  note: string | null;          // masked by the backend
  reviewer: string | null;      // null = not recorded
  at: string | null;            // ISO time; null = not recorded
  aboutTicketId: string | null; // set when the feedback is about one similar ticket, not the whole result
}

export interface ResolutionInfo {
  resolvedAt: string;
  resolvedBy: string | null;
}

/** One saved analysis as the Review History page shows it. */
export interface HistoryEntry {
  id: string;
  analysedAt: string;
  finishedAt: string | null;
  analyst: string | null;
  ticket: {
    subject: string;
    description: string | null;
    service: string | null;
    environment: string | null;
    channel: string | null;
  };
  outcome: Outcome;
  reasons: string[];
  confidence: number | null;    // null = not stored: never shown as a number
  classification: Classification | null;
  recommendation: Recommendation | null;
  findings: Finding[];
  evidence: string[];           // IDs of the tickets / KB sections used (their text is not stored)
  generator: string | null;
  classifierVersion: string | null;
  promptVersion: string | null;
  feedback: FeedbackEntry[];
  resolution: ResolutionInfo | null;
}

/** POST /api/tickets/results/{result_id}/feedback body (FeedbackIn). */
export interface TicketFeedbackIn {
  verdict: TicketVerdict;
  note?: string;
  target?: "result" | "match";
  target_id?: string;
}

// ---------- Dashboard: what the page works with ----------
// Frontend shape only. The backend stats record (PROPOSED: db/repo.stats() does not send these fields yet)
// and its adapter live in lib/api.ts (StatsApi, toDashboardStats).

export interface OutcomeShare {
  outcome: Outcome;
  count: number;
}

export interface CategoryFeedback {
  category: string;
  useful: number;
  incorrect: number;
  unresolved: number;
  rated: number;              // useful + incorrect + unresolved
  usefulRate: number | null;  // null when nothing was rated: never shown as 0%
}

/** A count per named group (priority, service, category). */
export interface CountRow {
  label: string;
  count: number;
}

/** Historical incidents (every ticket counted once). */
export interface IncidentOverview {
  total: number;
  resolved: number;   // resolved or closed
  pending: number;    // open or in progress
  critical: number;   // priority P1
}

/** Measured from historical ticket timestamps (created -> resolved). */
export interface MttrBaseline {
  medianHours: number;
  meanHours: number | null;
  tickets: number;
  byPriority: { priority: string; medianHours: number; tickets: number }[];
  byService: { service: string; medianHours: number; tickets: number }[];
  trend: { month: string; medianHours: number; resolved: number }[];   // oldest first
}

/** An ESTIMATE with stated assumptions: never shown as an observed value (docs/DATA-CARD.md). */
export interface MttrAssisted {
  medianHours: number;
  assumptions: string[];
}

export interface DashboardStats {
  analyses: number;
  outcomes: OutcomeShare[];           // always all four outcomes, in OUTCOMES order
  categories: CategoryFeedback[];
  incidents: IncidentOverview | null; // null = not available yet
  byPriority: CountRow[];             // [] = not available yet
  byService: CountRow[];
  byCategory: CountRow[];
  mttrBaseline: MttrBaseline | null;  // null = not available yet
  mttrAssisted: MttrAssisted | null;  // null = not available yet
  /** ESTIMATED saving: baseline median minus assisted median. null unless both exist. */
  timeSaved: { hours: number; percent: number | null } | null;
}
