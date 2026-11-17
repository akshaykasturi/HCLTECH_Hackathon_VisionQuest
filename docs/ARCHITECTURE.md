# Architecture

IT Ticket Analysis and Resolution Assistant (HCLTech AI Engineer use case 1). Version 2, after the mentor review
(2026-11-17 12:07). Who owns which file: [docs/PLAN.md](PLAN.md).

**Rule:** code decides, models suggest, the LLM only words steps it can cite, and code checks every LLM output
before anyone sees it.

## Where it runs

```
Browser ── Vercel: React frontend (Login, Analyze, Tickets, Review history, Dashboard, Audit, System health)
   │  HTTPS + Authorization: Bearer <JWT>
   ▼
Render: one FastAPI service
   routes (api/tickets.py, auth/router.py, common/api.py)      thin: validate, check login, call the pipeline
   pipeline (tickets/*.py)                                     steps 1-6 below
   repository (db/repo.py)                                     the only code that talks to the database
   files baked in at build: search indexes, classifier, KB articles
   │                                    │
   ▼                                    ▼
Neon Postgres (permanent)          Gemini -> Groq (template fallback if both fail)
```

Routes never touch SQL; the pipeline never touches HTTP. One service: no network hops between parts and one
thing to deploy in 4.5 hours.

## One request: `POST /api/tickets/analyze`

| # | Step | What it does | File |
|---|---|---|---|
| 0 | Auth check | Valid JWT and role, else 401/403 | `auth/deps.py` |
| 1 | Screen + classify | On the **raw** text, in memory only. Screen: prompt injection, jailbreak, requests for credentials or other users' data -> BLOCKED; hazard words (smoke, fire, spill) -> ESCALATE as P1. Classify: category, subcategory (valid pairs only), priority (model + rules), calibrated confidence | `tickets/screen.py`, `tickets/classify.py` |
| 2 | Mask + redact | Mask PII and secrets in every field. Nothing raw goes past this step: not to search, storage, logs, trace or the LLM | `tickets/guard.py`, `common/redact.py` |
| 3 | Find evidence | Similar tickets: hybrid BM25 + embeddings + RRF, same-service boost, filters first; labels likely_duplicate / related / weak, "why it matched", lookalike flag, past analyst feedback counts. KB: `common/doc_search` over approved articles, chunked by the method in `policy.yaml` (`kb_chunking`), picked on validation from fixed, heading and contextual chunking, each tried with BM25, dense and hybrid search (results in `docs/EVALUATION.md`) | `tickets/similar.py`, `tickets/kb.py`, `tickets/chunking.py` |
| 4 | Gate | RECOMMEND / CLARIFY / ESCALATE + reason codes from: classifier confidence, top similarity, agreement, conflicting fixes, out-of-distribution. CLARIFY asks one concrete question. Thresholds in `policy.yaml`, tuned on validation | `tickets/gate.py` |
| 5 | LLM writes | RECOMMEND only. `common/llm.generate_json` -> `Recommendation`; steps must be copied from cited evidence; the LLM orders them and explains why. Evidence is wrapped as data, never as instructions | `tickets/prompts.py`, `tickets/generate.py` |
| 6 | Code checks | Source ID allowed, excerpt verbatim, numbers from evidence, no "confirmed", no destructive steps, no secrets. Failed step dropped; no steps left -> ESCALATE | `tickets/checks.py`, `common/report_checks.py` |
| 7 | Save | Result + audit event in Postgres | `db/repo.py` |

Outcomes: **RECOMMEND** (cited steps), **CLARIFY** (one question), **ESCALATE** (group + what's missing),
**BLOCKED** (attack on the assistant; logged masked; no search, no LLM). A ticket *about* security
("I got a phishing email") is a normal Security ticket.

## Database (Neon Postgres; SQLite file when `DATABASE_URL` is unset, for local runs and tests)

| Table | Columns | Notes |
|---|---|---|
| `users` | id, username, password_hash, role, created_at | PBKDF2 hash; roles analyst, lead |
| `analysis_results` | result_id, created_at, recommended_at, user_id, ticket_masked (JSON), classification (JSON), evidence_ids (JSON), outcome, reasons (JSON), confidence, recommendation (JSON), findings (JSON), generator, model_version, prompt_version | Masked data only |
| `feedback` | id, result_id, reviewer_id, target (result/match), target_id, verdict (useful/incorrect/unresolved), note_masked, created_at | Append-only: a changed mind is a new row; latest counts |
| `resolutions` | result_id, resolved_by, resolved_at | MTTR end time |
| `audit_events` | seq, event_type, result_id, payload_hash, prev_hash, hash, created_at | Hash chain over the rows above; reuse `canonical_json` / `compute_hash` from `common/audit.py` |

Feedback is read back by `/history` and as `SimilarTicket.past_feedback`. It is **never** written into the search
index: a click is an opinion, not a verified fix.

## API (all responses use the existing `{ok, request_id, data, error}` envelope)

| Method + path | Role | What |
|---|---|---|
| `POST /api/auth/login` | public | `LoginIn` -> token + `User` |
| `GET /api/auth/me` | any | current `User` |
| `POST /api/tickets/analyze` | analyst | `TicketIn` -> `AnalysisResult` |
| `GET /api/tickets` | analyst | search historical tickets with `TicketFilters` |
| `GET /api/tickets/{ticket_id}` | analyst | one historical ticket |
| `POST /api/tickets/results/{result_id}/feedback` | analyst | `FeedbackIn` |
| `POST /api/tickets/results/{result_id}/resolve` | analyst | store resolved_at |
| `GET /api/tickets/history` | analyst | past results with their feedback |
| `GET /api/tickets/stats` | lead | outcome mix, useful rate per category, MTTR baseline vs assisted |
| `GET /api/tickets/taxonomy` | analyst | categories, subcategories, priorities |
| `/health`, `/health/ready`, `/api/audit/*` | public / lead | existing |

## Login

Login returns a JWT (PyJWT, HS256, 8 h expiry, secret from `JWT_SECRET`). The frontend keeps it in
sessionStorage and sends `Authorization: Bearer`. Every `/api/tickets/*` route depends on `current_user`.
Demo users come from `DEMO_USERS` in the environment via `scripts/seed_users.py`, never from code.
Trade-off: an httpOnly cookie is safer against XSS, but cross-site cookies between Vercel and Render are
fiddly; that's the next step.

## Deployment

Click-by-click steps: [docs/DEPLOY.md](DEPLOY.md).

| Service | Runs | Settings (secrets only in the host's dashboard, never in git) |
|---|---|---|
| Vercel | `frontend/` | `VITE_API_BASE_URL` = Render URL; SPA rewrite to `index.html` |
| Render | `backend/` web service | Blueprint `render.yaml`. Build: `pip install -r requirements.txt` (the index is built at startup warm-up). Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Health check: `/health/ready`. Env: `DATABASE_URL`, `JWT_SECRET`, `DEMO_USERS`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `CORS_ORIGINS` = Vercel URL |
| Neon | Postgres | `DATABASE_URL` with `sslmode=require` |

Free Render sleeps after ~15 min idle: open the site 5 min before the demo. If memory runs out, set
`DOC_SEARCH_DENSE=0` and search falls back to keyword only.

## Offline (before the demo)

`scripts/generate_data.py` -> `data/tickets/` + `data/docs/kb/` -> `scripts/train_classifier.py` and
`scripts/build_index.py` -> `scripts/evaluate.py` + `scripts/redteam.py` -> `docs/EVALUATION.md` -> Dashboard.
Thresholds are tuned on validation; final numbers come from the test split only.
