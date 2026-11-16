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
