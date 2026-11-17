# Day log

What existed before the event, and what we built during it. Judges may ask: this keeps the answer honest.

## Prepared before the event

Our working habits and generic, reusable parts, carried over from our practice run (the "mock", a UC4 API
failure diagnosis assistant in the `HCLTECH-Mock` repo). Everything problem-specific (UC4 field names, causes,
routes, data, pages) was removed. Nothing here knew the event's problem in advance.

| File | Where it came from |
|---|---|
| `CLAUDE.md` | rewritten from the mock's `CLAUDE.md` (same rules, problem removed) |
| `docs/PLAN.md` | new empty template, sections from the mock's `docs/PLAN.md` |
| `docs/TEAM-GUIDE.md`, `docs/DAY-LOG.md`, `README.md` | new |
| `.claude/settings.json` | copied from the mock as is |
| `.claude/skills/start-task`, `ready-pr`, `explain-change` | copied from the mock; repo link and data-card path made generic |
| `.github/pull_request_template.md` | copied from the mock as is |
| `.github/workflows/ci.yml` | copied from the mock as is (backend and frontend jobs) |
| `.gitattributes` | copied from the mock as is |
| `.gitignore` | new |
| `backend/app/common/redact.py` | mock `app/uc4/redact.py`; UC4 key format replaced by common provider key formats |
| `backend/app/common/docs_loader.py` | mock `app/uc4/docs_loader.py`; default folder now `data/docs` or `DOCS_DIR` |
| `backend/app/common/doc_search.py` | mock `app/uc4/doc_search.py`; settings from environment variables, empty-index safe |
| `backend/app/common/embed.py` | the embedding part of mock `app/uc4/ml/embed_features.py` |
| `backend/app/common/audit.py` | mock `app/uc4/audit.py`; "diagnosis" renamed to "result", `cause_id` to `item_id` |
| `backend/app/common/report_checks.py` + `.yaml` | the generic checks from mock `app/uc4/checks.py` (citations, numbers, certainty, safe steps, secrets) |
| `backend/app/common/trace.py` | the trace timer and `_optional` helper from mock `app/uc4/diagnose.py` |
| `backend/app/common/api.py` | envelope helpers from mock `app/uc4/api.py`, plus generic audit routes |
| `backend/app/common/llm.py`, `env.py`, `paths.py` | new (LLM gateway: Gemini, then Groq, then template fallback) |
| `backend/app/main.py` | mock `app/main.py`; UC4 routes removed, warm-up and readiness added |
| `backend/app/analysis/stats.py` | copied from the mock as is |
| `backend/app/analysis/baselines.py` | mock version, now takes texts and labels as arguments |
| `backend/scripts/check_llm.py` | new |
| `backend/tests/*` | mock tests for the parts above, UC4 data replaced by small made-up examples; new tests for llm, trace, main |
| `backend/requirements.txt`, `backend/.env.example`, `backend/pytest.ini` | trimmed from the mock |
| `frontend/` (Vite, React, TypeScript, Tailwind config, `AppShell`, `src/lib/api.ts`, System Health and Audit History pages, test utilities) | mock `frontend/`; UC4 pages removed, empty Home page added, fonts self-hosted with `@fontsource` |

## Built during the event

One row per merged pull request, from `gh pr list --state merged` (merge time, India Standard Time, 17 Nov 2026).
The problem (UC1) was picked at about 12:25 IST; PR #1 (03:04) was a fix to the prepared LLM gateway before that.
Who = the pull request's author. Commits inside each PR: `git log`.

| Time (IST) | PR | Who | What |
|---|---|---|---|
| 03:04 | #1 | Akshay | llm: send explicit User-Agent and show masked HTTP error bodies |
| 12:27 | #2 | Akshay | docs: plan, architecture and shared ticket shapes |
| 12:30 | #3 | Akshay | docs: add chunking trials to the plan |
| 12:40 | #4 | Akshay | rag: hybrid similar-ticket search |
| 12:50 | #5 | Atheesha | tickets: screen step and ticket masking (steps 1 and 2) |
| 12:51 | #6 | Akshay | rag: code gate for recommend, clarify, escalate and blocked |
| 12:57 | #7 | Atheesha | tickets: check the LLM recommendation before showing it (step 6) |
| 12:59 | #8 | Akshay | rag: analyze pipeline end to end with fallbacks |
| 13:05 | #10 | Akshay | api: ticket routes on top of the pipeline |
| 13:06 | #9 | Atheesha | tickets: LLM writes cited fix steps (step 5) with versioned prompt |
| 13:13 | #11 | Akshay | rag: run template steps through the checks too |
| 13:17 | #12 | Akshay | deploy: Render blueprint, Vercel SPA rewrite and deploy guide |
| 13:19 | #13 | Roshan | data: add reproducible raw and curated ITSM datasets |
| 13:24 | #14 | Atheesha | auth: JWT login route, current_user dependency and demo user seeding |
| 13:36 | #15 | Raswanth | ml: entropy weighted TOPSIS re-ranking of similar tickets, off by default |
| 13:37 | #16 | Akshay | rag: fit search, gate, presets and template to the real data |
| 13:38 | #17 | Atheesha | screen: red-team runner and report, wider attack and hazard patterns |
| 13:48 | #18 | Akshay | rag: gate counts unique words and shows screen codes as escalation reasons |
| 13:54 | #19 | Atheesha | screen: needs_human codes for privileged, legal and repeated-failure tickets |
| 13:55 | #20 | Akshay | rag: KB search and chunking trials |
| 13:57 | #21 | Roshan | frontend: analyst sign-in, Analyze page and employee site layout |
| 14:00 | #22 | Akshay | core: shared shapes for employee complaints |
| 14:00 | #23 | Raswanth | ml: embedding features for the ticket classifier |
| 14:10 | #24 | Atheesha | llm: default Groq model openai/gpt-oss-120b, document Gemini 429 fallback |
| 14:14 | #25 | Atheesha | auth: lock audit routes to analysts, add employee role to demo users |
| 14:23 | #26 | Roshan | frontend: Incident Management tickets page and employee role handling |
| 14:42 | #28 | Roshan | frontend: Review History page with provisional history adapter |
| 14:43 | #27 | Atheesha | tickets: LLM check script on real tickets and fewer false unsafe-step flags |
| 14:55 | #29 | Akhilesh | ml: TF-IDF classifier comparison for category and subcategory (FR-01) |
| 15:10 | #30 | Roshan | frontend: dashboard incident analytics, MTTR sections and data contract |
| 15:14 | #31 | Akhilesh | mttr: measured baseline with SLA breach and CI, assisted MTTR estimate |
| 15:28 | #32 | Raswanth | ml: classify.py with the best of all 9 classifiers, joint decoding, priority rule |
| 15:37 | #33 | Roshan | data: raw-to-clean ticket ingestion pipeline with ground-truth evaluation |
| 15:49 | #34 | Akhilesh | eval: pipeline evaluation over labelled cases, report and worked examples |
| 15:53 | #35 | Atheesha | tickets: land stranded screen and checks fixes |
| 15:54 | #36 | Raswanth | ml: AHP prior weights for the TOPSIS re-ranker, evaluation arms ahp and ahp_entropy |
| 16:02 | #37 | Raswanth | docs: AHP weight sensitivity, one-at-a-time and Monte Carlo |
| 16:20 | #38 | Akshay | db: permanent database for results, feedback, resolutions, users |
