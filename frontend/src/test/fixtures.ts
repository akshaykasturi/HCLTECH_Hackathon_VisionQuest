// Backend-shaped test data. Field names are the backend's own.
import type { ChainStatus, FeedbackRecord, HealthData, ResultSummary, SavedResult } from "../lib/types";

export const HEALTH_OK: HealthData = { api: "up", database: "up", warmup: "ready", warmup_detail: "1 warm-up step(s) done" };
export const HEALTH_WARMING: HealthData = { api: "up", database: "up", warmup: "warming", warmup_detail: "running get_index" };

export const RESULTS: ResultSummary[] = [
  { result_id: "r-2", case_id: "CASE-0002", recorded_at: "2026-11-16T10:05:00.000Z", request_id: "req-2",
    outcome: "CLARIFY", summary: "Not enough evidence yet.", feedback_count: 0 },
  { result_id: "r-1", case_id: "CASE-0001", recorded_at: "2026-11-16T10:00:00.000Z", request_id: "req-1",
    outcome: "RECOMMEND", summary: "Likely answer A.", feedback_count: 1 },
];

export const RESULT_1: SavedResult = { result_id: "r-1", case_id: "CASE-0001", outcome: "RECOMMEND", summary: "Likely answer A." };

export const FEEDBACK_1: FeedbackRecord[] = [
  { feedback_id: "fb-1", result_id: "r-1", case_id: "CASE-0001", verdict: "confirm", item_id: null,
    note: "Checked the log.", created_at: "2026-11-16T10:06:00.000Z", request_id: "req-3" },
];

export const CHAIN_OK: ChainStatus = { valid: true, events_checked: 3, head_hash: "ab".repeat(32), first_bad_seq: null, reason: null };
export const CHAIN_BAD: ChainStatus = {
  valid: false, events_checked: 1, head_hash: "cd".repeat(32), first_bad_seq: 2, reason: "hash does not match the event's contents",
};
