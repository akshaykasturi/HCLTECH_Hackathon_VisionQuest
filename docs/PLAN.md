# Plan

The first file everyone (and Claude) reads. Architecture details: [docs/ARCHITECTURE.md](ARCHITECTURE.md).
Keep it short and current; change it through a pull request.

## Problem

HCLTech AI Engineer use case 1, **Intelligent IT Ticket Analysis and Resolution Assistant** (Mail 2a, 2026-11-17).
An analyst submits a ticket; the assistant classifies it (category, subcategory, priority + confidence), finds
similar past tickets and approved KB articles, and recommends evidence-backed fix steps the analyst can review,
with the goal of measurably reducing MTTR. It analyses and recommends only: no automatic closure or ITSM changes.

## Requirements -> where each is met

| Requirement | Where it is met (file, screen or test) | Status (proof) |
|---|---|---|
| FR-01 category / subcategory / priority + confidence | `backend/app/tickets/classify.py` + `classifier_choice.json` (9 candidates compared on val), Analyze page chips | done (PR #29, #32; `docs/ml/all-classifiers-val.md`) |
| FR-02 similar + duplicate tickets, ranked (title, description, service, metadata) | `backend/app/tickets/similar.py` (hybrid BM25 + embeddings + RRF, match labels, lookalike flag) | done (PR #4, #16; retrieval rows in `docs/EVALUATION.md`) |
| FR-03 resolution recommendation from resolved tickets or approved KB | `backend/app/tickets/generate.py` + `backend/app/tickets/kb.py`, template fallback in `pipeline.py` | done (PR #8, #9, #20; `docs/LLM_CHECK.md`) |
| FR-04 evidence: source, excerpt, reason it supports | `Step.source_id / excerpt / why`, verified in `backend/app/tickets/checks.py` | done (PR #7, #11, #35) |
| FR-05 low confidence, no unsupported conclusions, escalation path | `backend/app/tickets/gate.py` (RECOMMEND / CLARIFY / ESCALATE / BLOCKED) | done (PR #6, #18, #19); gate thresholds in `policy.yaml` are hand-set defaults, not tuned on val |
| FR-06 analyst feedback useful / incorrect / unresolved | `POST /api/tickets/results/{id}/feedback`, `backend/app/db/repo.py` | done (PR #38; `tests/test_db_repo.py`) |
| FR-07 search + filters (category, status, team, service, environment, date) | `GET /api/tickets`, Tickets page | done (PR #10, #26) |
| FR-08 audit: input, classification, evidence, recommendation, reviewer decision | `backend/app/db/tables.py` (`analysis_results`, `feedback`, `audit_events` hash chain), `GET /api/tickets/audit/verify`, Audit History page | done (PR #38; Audit page on the database: docs/final-polish) |
| FR-09 MTTR with assistant vs manual baseline | `backend/app/tickets/mttr.py` (baseline measured, assisted estimated), `docs/EVALUATION.md` | partial: numbers in `docs/EVALUATION.md` (PR #31); the Dashboard's stats panel is not wired to them yet (`GET /api/tickets/stats` lacks the `docs/DASHBOARD-CONTRACT.md` fields) |
| Mentor: login | `backend/app/auth/`, Login page | done (PR #14, #21, #25) |
| Mentor: feedback in a permanent DB | Neon Postgres via `DATABASE_URL` (`backend/app/db/`) | done (PR #38) |
| Mentor: screen + classify before masking | `backend/app/tickets/screen.py` + `classify.py` run first, raw text in memory only | done (PR #5, #8, #32) |
| Mentor: compare 3 chunking methods (fixed, heading, contextual) x 3 retrieval methods (BM25, dense, hybrid) | `backend/app/tickets/chunking.py`, `scripts/eval_chunking.py` -> `docs/results/chunking_val.json` / `chunking_test.json`; winner `kb_chunking: heading` | done (PR #20) |
| Sensitive content respected in retrieval, display and export | `backend/app/tickets/guard.py` masks before search, storage, logs, LLM; leak probe in `docs/REDTEAM.md` | done (PR #5, #17) |
| Raw-to-clean data ingestion | `backend/app/ingest/`, `docs/INGESTION.md` | done (PR #33) |
| Evaluation report | `backend/scripts/evaluate.py` -> `docs/EVALUATION.md` | partial: val done (PR #34); the test split has not been scored for the pipeline or the classifiers (`--final` not run) |
| At least 3 worked examples | `docs/WORKED-EXAMPLES.md` | done (PR #34) |
| README: setup, architecture, assumptions, trade-offs | `README.md` | done (docs/final-polish) |
| Employee complaint portal (submit, status, "My requests") | shapes in `backend/app/tickets/models.py`, design in `docs/ARCHITECTURE.md` | not built (shapes only: PR #22) |

## Flow

```
login (JWT) -> 1 screen + classify (raw text, in memory) -> 2 mask + redact -> 3 find evidence (similar tickets + KB)
-> 4 gate -> 5 LLM writes cited steps (RECOMMEND only) -> 6 code checks -> save to Postgres -> analyst review
-> feedback / resolve saved -> history + MTTR dashboard
evaluation runs steps 1-6 over the validation split (test: --final, not run yet)
```

## Shared data shapes

All in `backend/app/tickets/models.py` (merged first): `TicketIn`, `TicketFilters`, `ScreenResult`, `LabelScore`,
`Classification`, `SimilarTicket`, `KBHit`, `GateDecision`, `Step`, `Recommendation`, `AnalysisResult`,
`FeedbackIn`, `LoginIn`, `User`. Test data for working before search exists: `backend/tests/fixtures/sample_evidence.json`.

### Plugs between parts (keep these signatures)

| Function | File | Owner |
|---|---|---|
| `screen(ticket: TicketIn) -> ScreenResult` | `tickets/screen.py` | Atheesha |
| `classify(ticket: TicketIn) -> Classification`; `load_classifier()` (warm-up) | `tickets/classify.py` | Raswanth |
| `mask_ticket(ticket: TicketIn) -> tuple[TicketIn, dict[str, int]]` | `tickets/guard.py` | Atheesha |
| `search_similar(query: str, k: int = 5, filters: TicketFilters \| None = None) -> list[SimilarTicket]`; `get_ticket_index()` (warm-up) | `tickets/similar.py` | Akshay |
| `search_kb(query: str, k: int = 3) -> list[KBHit]` | `tickets/kb.py` | Akshay |
| `decide(ticket, screen, classification, similar, kb) -> GateDecision` | `tickets/gate.py` | Akshay |
| `generate(ticket, classification, similar, kb, allowed_ids: list[str]) -> Recommendation \| None` | `tickets/generate.py` | Atheesha |
| `check_recommendation(rec, similar, kb, allowed_ids) -> tuple[Recommendation \| None, list[Finding]]` | `tickets/checks.py` | Atheesha |
| `analyze(ticket: TicketIn, user: User) -> AnalysisResult` | `tickets/pipeline.py` | Akshay |
| `save_result(result, user)`, `add_feedback(result_id, user, FeedbackIn)`, `resolve(result_id, user) -> datetime`, `history(...)`, `stats() -> dict` | `db/repo.py` | Raswanth |
| `current_user() -> User`, `require_role(role)` (FastAPI dependencies) | `auth/deps.py` | Atheesha |
| `baseline_mttr(tickets) -> dict`, `estimate_assisted(results, assumptions) -> dict` | `tickets/mttr.py` | Akhilesh |
| `make_pipeline(estimator) -> Pipeline`, `load_splits(splits) -> dict[str, list[dict]]` (text = `features.row_text`, masked, TF-IDF) | `tickets/clf_pipeline.py` | Akhilesh |
| classifier comparison on val, winners saved to `backend/models/clf_<label>.joblib` + `clf_manifest.json`; `--final` scores test once | `scripts/compare_classifiers.py` | Akhilesh |

classify.py loads `backend/models/clf_<label>.joblib`. classify() should load backend/models/clf_<label>.joblib and call
predict_proba on rows; the pipeline applies row_text itself. Models exist for category and subcategory only:
priority comes from a rule (TicketIn.impact, plus raising production outages to P1/P2), reported via
Classification.priority_rule, never as a confident ML label.

The pipeline imports each part with `common/trace.optional(...)`: a part that isn't ready gets a simple fallback
and a trace note, so main always runs.

### Data contract

`backend/data/tickets/tickets.csv`, one row per historical ticket (synthetic, documented in `docs/DATA-CARD.md`):
`ticket_id, created_at, resolved_at, status, channel, category, subcategory, priority, assigned_team,
affected_service, environment, subject, description, root_cause, resolution_notes, resolution_steps,
problem_id, dup_group_id, split`. `resolution_steps` = steps separated by ` | `. `split` = train / val / test,
split **by issue group** so no template appears in two splits. Gold labels for CLARIFY / ESCALATE / BLOCKED
and red-team cases: `backend/data/tickets/labelled_cases.jsonl`. KB articles: `backend/data/docs/kb/*.md`
(front matter: `version`, `status: current`, `updated`; first `#` heading = title).

## Who leads what

"Leads" means the main person who builds it first and who to ask, not the only person. One owner per new file.

- **Akshay**: integration + merges; `tickets/models.py`, `tickets/similar.py`, `tickets/kb.py`, `tickets/gate.py`,
  `tickets/pipeline.py`, `tickets/chunking.py`, `tickets/policy.yaml`, `app/main.py`, `api/tickets.py` (taken over
  from Roshan), `scripts/build_index.py`; Render + Neon deploy.
- **Atheesha**: `tickets/screen.py`, `tickets/guard.py` (+ `common/redact.py` additions), `tickets/prompts.py`,
  `tickets/generate.py`, `tickets/checks.py`, `auth/` (`security.py`, `deps.py`, `router.py`),
  `scripts/redteam.py`, `scripts/seed_users.py`, `data/tickets/redteam.jsonl`.
- **Raswanth**: `db/` first (`engine.py`, `tables.py`, `repo.py`; needs no data), then `tickets/classify.py`,
  `scripts/train_classifier.py`, gate thresholds in `policy.yaml` (tuned on validation).
- **Akhilesh**: `scripts/generate_data.py` (start from `generate_tickets.py` in commit 514e4e5), `data/tickets/`,
  `data/docs/kb/`, `docs/DATA-CARD.md`, `tickets/mttr.py`, `scripts/evaluate.py`, `docs/EVALUATION.md`,
  `docs/WORKED-EXAMPLES.md`, Miro board, pitch. KB: 15-25 articles, 400-800 words, 4-6 `##` sections;
  each eval ticket has gold kb `doc_id` + `key_sentence`.
- **Roshan**: frontend pages `LoginPage`, `AnalyzePage`, `TicketsPage`,
  `ReviewHistoryPage`, `DashboardPage` + token handling in `frontend/src/lib/api.ts`; Vercel deploy.

## Decided so far

- Problem: UC1 IT tickets (closest to our mock; strongest fit of the five briefs).
- Fixed pipeline, not agents: every step testable and auditable; the brief asks for explainable output.
- Code decides (gate, checks); the LLM only orders and explains steps copied from cited sources.
- Screen + classify run first on raw text in memory only (mentor); masking right after, before anything is
  searched, stored, logged or sent out.
- BLOCKED outcome for attacks on the assistant; a ticket *about* security is a normal Security ticket.
- Permanent DB = Neon Postgres (Render's free disk is wiped on redeploy); SQLite fallback for local runs and tests.
- Feedback is stored append-only in Postgres and never written into the search index.
- Login with JWT; roles analyst and lead; passwords hashed (PBKDF2); demo users seeded from env.
- MTTR: baseline measured from historical timestamps; assisted MTTR is an estimate with stated assumptions.
- Deploy: Vercel (frontend) -> Render (backend) -> Neon (database).
- Chunking: 3 trials on KB articles, winner chosen on validation and set as `kb_chunking` in `policy.yaml`.
  Tickets are not chunked (one ticket = one unit). Contextual prefixes are for search only; citations use the
  original text. Prefixes are generated once and committed in `backend/data/kb_context.json`.
- Retrieval: hybrid BM25 + dense + RRF, compared against each alone.
