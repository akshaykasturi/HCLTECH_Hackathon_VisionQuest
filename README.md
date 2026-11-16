# VisionQuest: HCLTech hackathon

Our team's repository for the HCLTech hackathon. The problem statement is picked on the day; the plan lives in
[docs/PLAN.md](docs/PLAN.md). Before the event this repo holds only our working habits and generic building blocks
(secret masking, document search, an audit log, an LLM gateway with output checks, an API and a frontend shell).
[docs/DAY-LOG.md](docs/DAY-LOG.md) lists what was prepared before and what was built during the event.

## Quick setup (Windows PowerShell)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env     # then add the keys shared in the group
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

cd ..\frontend
npm ci
npm run dev                     # http://127.0.0.1:5173
```

Full teammate setup and the daily workflow: [docs/TEAM-GUIDE.md](docs/TEAM-GUIDE.md).

## Repository map

| Folder | What is in it |
|---|---|
| `backend/app/common/` | Shared building blocks: secret masking, doc loading and hybrid search, audit log, LLM gateway, report checks, trace |
| `backend/app/analysis/` | Evaluation helpers: statistics (bootstrap, McNemar, Brier) and simple baselines |
| `backend/app/main.py` | FastAPI app: `{ok, data, error}` envelope, request ids, health, readiness, audit routes |
| `backend/scripts/` | Command-line helpers (`check_llm.py` tests our real LLM keys) |
| `backend/tests/` | Backend tests (offline: no real API calls, no model downloads) |
| `frontend/` | Vite + React + TypeScript + Tailwind app: Home, Audit History, System Health |
| `docs/` | Plan, team guide and day log |
| `.claude/` | Shared Claude Code settings and skills (`/start-task`, `/ready-pr`, `/explain-change`) |
| `.github/` | CI (backend and frontend checks) and the pull request template |
