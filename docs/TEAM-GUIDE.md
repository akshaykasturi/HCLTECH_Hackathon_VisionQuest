# Team guide

How to get set up, and how we work during the day. Windows PowerShell. Ask in the group if anything fails.

## One-time setup (about 10 minutes)

You need Git, Python 3.12, Node.js 20.19 or newer, and Claude Code.

1. **Clone the repo**

   ```powershell
   git clone https://github.com/akshaykasturi/HCLTECH_Hackathon_VisionQuest.git
   cd HCLTECH_Hackathon_VisionQuest
   ```

2. **Backend: create the venv and install**

   ```powershell
   cd backend
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe -m pytest -q
   ```

3. **Backend: your `.env`**

   ```powershell
   Copy-Item .env.example .env
   ```

   Open `backend\.env` in an editor and paste the keys shared in the group (`GEMINI_API_KEY`, `GROQ_API_KEY`).
   `.env` is git-ignored: never commit it, never paste a key in chat or code. Check the keys work:

   ```powershell
   .\.venv\Scripts\python.exe scripts\check_llm.py
   ```

4. **Frontend: install**

   ```powershell
   cd ..\frontend
   npm ci
   npm run test -- --run
   ```

5. **Start Claude Code in the repo folder** (not in `backend` or `frontend`), so it reads `CLAUDE.md` and our skills:

   ```powershell
   cd ..
   claude
   ```

## Every task: the loop

1. **Start**: in Claude Code, `/start-task <branch-name> <what to build>`.
   It pulls main first, then makes your branch. Example: `/start-task llm-report write the report with the LLM`.
2. **Build** with Claude. Keep the change small. Read what it writes: you must be able to explain it to judges
   (`/explain-change` helps).
3. **Finish**: `/ready-pr`. It runs the tests, checks for secrets, commits, pushes and opens the pull request.
4. **Post the pull request link in the group.** Merge when CI is green (both checks: backend and frontend).
5. Back to step 1. Always pull main before each new task (`/start-task` does it for you).

## Running the app

Two terminals, from the repo folder:

```powershell
cd backend;  .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
cd frontend; npm run dev
```

Open http://127.0.0.1:5173. API docs: http://127.0.0.1:8000/docs.

## Switching laptops

Everything you need is in git. Commit and push your branch (`/ready-pr`, or `git push` if it is not ready),
then on the other laptop: `git fetch`, `git switch <branch>`. Each laptop needs its own `backend\.env`.

## If a teammate's part isn't ready

Don't wait. Use a simple fallback (a rule, a template, fixed sample data clearly marked as such) and leave a note
in the code and the trace (`app/common/trace.py`). Swap it out when their part lands.

## Small rules

- Use `127.0.0.1`, not `localhost`. Call `.\.venv\Scripts\python.exe` directly (no `activate` needed).
- Use the data's own field names. Tune on validation; report on test.
- Never call anything "confirmed" unless the evidence proves it.
