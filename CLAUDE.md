# How we work in this repo

HCLTech hackathon, team VisionQuest. The problem and the plan live in docs/PLAN.md (filled in on the day).
Read it first. What we built before the event is listed in docs/DAY-LOG.md.

These are guidance, not locks: anyone may help on any part, and people may switch laptops.

## Design rules

- Code decides; the LLM explains; code checks the LLM's output before anyone sees it
  (`backend/app/common/report_checks.py`).
- Never call something "confirmed" unless the evidence proves it. Say "likely" or "probable".
- No secrets in output or in git: mask with `backend/app/common/redact.py` before logging, storing or showing.
- Use the data's own field names. Don't rename them.
- Tune on the validation split. Report final numbers on the test split only.
- Never fake AI results with hardcoded outputs.
- If a teammate's part isn't ready, use a simple fallback with a note (in the trace or the code)
  instead of waiting (`backend/app/common/trace.py`: `optional(...)`).

## Git

- One branch per piece of work. Pull main before starting: `/start-task <branch> <what>`.
- When done: `/ready-pr` (tests, secret check, commit, push, pull request). Post the link in the group.
- Commit messages: `<area>: <what>`, no quotes inside. Never force-push.
- Pull requests with `gh pr create`; check CI with `gh pr checks`. If `gh` is not logged in, use
  https://github.com/akshaykasturi/HCLTECH_Hackathon_VisionQuest/pull/new/<branch>

## Running things (Windows)

- Python 3.12. From `backend`, call the venv's Python directly: `.\.venv\Scripts\python.exe -m pytest -q`.
- Use `127.0.0.1`, not `localhost`.
- Backend: `.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`.
- Frontend: `npm run dev`, `npm run test -- --run`, `npm run lint`, `npm run build` in `frontend`.
- Never read or print `.env`; never put a key in code, tests, logs or chat. Test keys with `scripts\check_llm.py`.
- No new dependency without saying so in the pull request.

## Where things go

- Shared building blocks: `backend/app/common/` (masking, docs, search, audit, LLM, checks, trace).
- Problem code: a new folder under `backend/app/` named after the problem. Tests: `backend/tests/`.
- Evaluation helpers: `backend/app/analysis/` (`stats.py`, `baselines.py`).

## Talking to the human

- Explain every change simply: everyone must be able to explain their code to the judges.
  `/explain-change` helps prepare.
- When unsure, ask one short question instead of guessing.
