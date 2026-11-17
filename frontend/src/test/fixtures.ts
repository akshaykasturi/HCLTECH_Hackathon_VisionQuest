// Backend-shaped test data. Field names are the backend's own.
import type {
  AnalysisResult, ChainStatus, FeedbackRecord, HealthData, LoginData, Preset, ResultSummary, SavedResult, Taxonomy,
} from "../lib/types";

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

// ---------- auth + tickets (backend/app/auth/router.py, backend/app/tickets/models.py) ----------

export const LOGIN_OK: LoginData = {
  token: "test-token-abc", token_type: "bearer", expires_at: "2099-01-01T00:00:00+00:00",
  user: { username: "asha", role: "analyst" },
};

export const PRESETS: Preset[] = [
  { id: "vpn-809", label: "VPN error 809", expected_outcome: "RECOMMEND",
    ticket: { subject: "VPN fails with error 809", description: "Since this morning the VPN fails with error 809 from home.",
              affected_service: "Corporate VPN", environment: "production", channel: "portal" } },
];

export const TAXONOMY: Taxonomy = {
  category: ["Network"], priority: ["P1", "P2"], status: ["resolved"], assigned_team: ["Network Operations"],
  affected_service: ["Corporate VPN", "Corporate DNS"], environment: ["production"], subcategory: { Network: ["VPN"] },
};

const BASE: AnalysisResult = {
  result_id: "res-0001", created_at: "2026-11-17T09:00:00Z", recommended_at: "2026-11-17T09:00:02Z",
  ticket: PRESETS[0].ticket, screen: { blocked: false, safety: false, codes: [] }, classification: null,
  similar: [], kb: [], outcome: "RECOMMEND", reasons: [], confidence: 0.5, question: null, escalation_group: null,
  recommendation: null, findings: [], timings_ms: { total: 1840.4 }, trace: [{ step: "screen", ms: 3.1, detail: "clean" }],
};

export const RESULT_RECOMMEND: AnalysisResult = {
  ...BASE, outcome: "RECOMMEND", reasons: ["STRONG_MATCH"], confidence: 0.86,
  classification: {
    category: { label: "Network", prob: 0.93 }, subcategory: { label: "VPN", prob: 0.88 }, priority: { label: "P2", prob: 0.71 },
    top3_category: [], key_terms: ["vpn", "809"], priority_rule: null, model_version: "test",
  },
  similar: [{ ticket_id: "INC-100045", subject: "VPN drops after gateway change", category: "Network", subcategory: "VPN",
              status: "resolved", score: 0.91, match: "likely_duplicate", why: ["same affected_service: Corporate VPN"],
              lookalike: false, problem_id: null, root_cause: null, resolution_text: null, past_feedback: { useful: 2 } },
            { ticket_id: "INC-100777", subject: "DNS search suffix missing", category: "Network", subcategory: "DNS",
              status: "resolved", score: 0.41, match: "weak", why: ["keyword overlap only"], lookalike: true,
              problem_id: null, root_cause: null, resolution_text: null, past_feedback: {} }],
  kb: [{ section_id: "doc:KB-004#4", title: "Resolution steps by cause", text: "1. Upgrade the VPN client.", score: 0.8 }],
  recommendation: {
    summary: "Upgrade the VPN client to the supported version.", generator: "template", prompt_version: "v1",
    escalation_group: null,
    steps: [{ text: "Upgrade the VPN client to the supported version", source_id: "doc:KB-004#4",
              excerpt: "Upgrade the VPN client to the supported version through the deployment console",
              why: "Matches the cipher mismatch cause" }],
  },
};

export const RESULT_CLARIFY: AnalysisResult = {
  ...BASE, result_id: "res-0002", outcome: "CLARIFY", reasons: ["LOW_CONFIDENCE"], confidence: 0.31,
  question: "Which service is affected, and what error do you see?",
};

export const RESULT_ESCALATE: AnalysisResult = {
  ...BASE, result_id: "res-0003", outcome: "ESCALATE", reasons: ["SAFETY_HAZARD"], confidence: 0.95,
  escalation_group: "Site Reliability",
};

export const RESULT_BLOCKED: AnalysisResult = {
  ...BASE, result_id: "res-0004", outcome: "BLOCKED", reasons: ["INJECTION_PATTERN"], confidence: 1,
  screen: { blocked: true, safety: false, codes: ["INJECTION_PATTERN"] },
};
