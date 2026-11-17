# Plan

The first file everyone (and Claude) reads. Architecture details: [docs/ARCHITECTURE.md](ARCHITECTURE.md).
Keep it short and current; change it through a pull request.

## Problem

HCLTech AI Engineer use case 1, **Intelligent IT Ticket Analysis and Resolution Assistant** (Mail 2a, 2026-11-17).
An analyst submits a ticket; the assistant classifies it (category, subcategory, priority + confidence), finds
similar past tickets and approved KB articles, and recommends evidence-backed fix steps the analyst can review,
with the goal of measurably reducing MTTR. It analyses and recommends only: no automatic closure or ITSM changes.

## Requirements -> where each is met

| Requirement | Where it is met (file, screen or test) | Status |
|---|---|---|
| FR-01 category / subcategory / priority + confidence | `backend/app/tickets/classify.py`, Analyze page chips | todo |
| FR-02 similar + duplicate tickets, ranked (title, description, service, metadata) | `backend/app/tickets/similar.py` (hybrid BM25 + embeddings + RRF, match labels, lookalike flag) | todo |
| FR-03 resolution recommendation from resolved tickets or approved KB | `backend/app/tickets/generate.py` + `backend/app/tickets/kb.py` | todo |
| FR-04 evidence: source, excerpt, reason it supports | `Step.source_id / excerpt / why`, verified in `backend/app/tickets/checks.py` | todo |
| FR-05 low confidence, no unsupported conclusions, escalation path | `backend/app/tickets/gate.py` (RECOMMEND / CLARIFY / ESCALATE / BLOCKED) | todo |
| FR-06 analyst feedback useful / incorrect / unresolved | `POST /api/tickets/results/{id}/feedback`, `backend/app/db/repo.py` | todo |
| FR-07 search + filters (category, status, team, service, environment, date) | `GET /api/tickets`, Tickets page | todo |
| FR-08 audit: input, classification, evidence, recommendation, reviewer decision | `backend/app/db/tables.py` (`analysis_results`, `feedback`, `audit_events` hash chain) | todo |
| FR-09 MTTR with assistant vs manual baseline | `backend/app/tickets/mttr.py`, Dashboard (baseline measured, assisted estimated) | todo |
| Mentor: login | `backend/app/auth/`, Login page | todo |
| Mentor: feedback in a permanent DB | Neon Postgres via `DATABASE_URL` (`backend/app/db/`) | todo |
| Mentor: screen + classify before masking | `backend/app/tickets/screen.py` + `classify.py` run first, raw text in memory only | todo |
| Mentor: compare 3 chunking methods (fixed, heading, contextual) x 3 retrieval methods (BM25, dense, hybrid) | `backend/app/tickets/chunking.py` + `retrieval_eval()`, results table in `docs/EVALUATION.md` | todo |
| Sensitive content respected in retrieval, display and export | `backend/app/tickets/guard.py` masks before search, storage, logs, LLM | todo |
| Evaluation report | `backend/scripts/evaluate.py` -> `docs/EVALUATION.md` | todo |
| At least 3 worked examples | `docs/WORKED-EXAMPLES.md` | todo |
| README: setup, architecture, assumptions, trade-offs | `README.md` | todo |

## Flow

```
login (JWT) -> 1 screen + classify (raw text, in memory) -> 2 mask + redact -> 3 find evidence (similar tickets + KB)
-> 4 gate -> 5 LLM writes cited steps (RECOMMEND only) -> 6 code checks -> save to Postgres -> analyst review
-> feedback / resolve saved -> history + MTTR dashboard
evaluation runs steps 1-6 over the validation and test splits
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
