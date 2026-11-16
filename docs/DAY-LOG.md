# Day log

What existed before the event, and what we built during it. Judges may ask: this keeps the answer honest.

## Prepared before the event

Our working habits and generic, reusable parts, carried over from our practice run (the "mock", a UC4 API
failure diagnosis assistant in the `HCLTECH-Mock` repo). Everything problem-specific (UC4 field names, causes,
routes, data, pages) was removed. Nothing here knows tomorrow's problem.

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

| Time | Who | What |
|---|---|---|
| | | |
