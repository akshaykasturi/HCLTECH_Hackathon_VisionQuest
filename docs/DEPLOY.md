# Deploy: Neon -> Render -> Vercel

Browser -> **Vercel** (frontend) -> **Render** (FastAPI backend) -> **Neon** (Postgres). About 20 minutes.
Config in git: `render.yaml` (repo root) and `frontend/vercel.json`. **Secrets go only into the dashboards**,
never into a file, a commit or the group chat.

Do it in this order: Neon gives the database URL Render needs, Render gives the API URL Vercel needs,
and Vercel gives the site URL Render's CORS needs.

## 1. Neon (database)

1. Go to https://console.neon.tech and sign in (GitHub login is fine).
2. **New Project**: name `visionquest`, Postgres version as offered. Pick the region **closest to the Render
   region** (default Render region is Oregon -> pick *AWS US West (Oregon)*; if you choose Singapore on Render,
   pick *AWS Asia Pacific (Singapore)*). **Create project**.
3. On the project dashboard click **Connect**. Copy the connection string. It looks like
   `postgresql://<user>:<password>@<host>.neon.tech/<db>?sslmode=require`.
   Check that it ends with **`sslmode=require`**; add it if it is missing.
4. Keep it in your password manager for step 2.4. Don't paste it in chat.

Paste the URL as Neon gives it: `db/engine.py` rewrites `postgres://` / `postgresql://` to
`postgresql+psycopg://` (psycopg 3) itself. The tables and the `DEMO_USERS` are created at startup.

## 2. Render (backend)

1. Go to https://dashboard.render.com and sign in with GitHub.
2. **New +** (top right) -> **Blueprint**.
3. Connect the GitHub repo `HCLTECH_Hackathon_VisionQuest`, branch `main`. Render reads `render.yaml` and shows one
   web service, `visionquest-api` (free plan, root directory `backend`).
4. Render asks for the values marked `sync: false`. Fill in:

   | Key | Value |
   |---|---|
   | `DATABASE_URL` | the Neon string from step 1.3 |
   | `JWT_SECRET` | a long random string: run `python -c "import secrets; print(secrets.token_urlsafe(48))"` locally |
   | `DEMO_USERS` | `username:password:role` pairs separated by commas (roles `analyst`, `lead`); demo passwords only |
   | `GEMINI_API_KEY`, `GROQ_API_KEY` | our keys (leave empty to use the template fallback) |
   | `CORS_ORIGINS` | leave a placeholder like `https://example.invalid` for now; set it in step 4 |

5. **Apply**. The first build takes a few minutes (`pip install`). Wait for **Live**.
6. Copy the service URL, e.g. `https://visionquest-api.onrender.com`.
7. Check: open `https://<render-url>/health/ready`. You want `{"ok": true, ...}`. Right after start it can say
   `WARMING_UP` (503) for up to a minute while the search index and the embedding model load; refresh.
   `https://<render-url>/docs` shows every route.

## 3. Vercel (frontend)

1. Go to https://vercel.com/new and sign in with GitHub.
2. **Import** the repo `HCLTECH_Hackathon_VisionQuest`.
3. **Root Directory**: click **Edit** and choose `frontend`. Framework preset: **Vite** (detected).
   Build command `npm run build` and output directory `dist` are the defaults; leave them.
4. **Environment Variables**: `VITE_API_BASE_URL` = the Render URL from step 2.6, **no trailing slash**.
5. **Deploy**. Copy the production URL, e.g. `https://visionquest.vercel.app`.

`VITE_API_BASE_URL` is baked in at build time: if you change it later, **Redeploy** on Vercel.
`frontend/vercel.json` sends every path to `index.html`, so reloading `/tickets` or `/dashboard` works.

## 4. Connect them: CORS

1. Render -> `visionquest-api` -> **Environment** -> `CORS_ORIGINS` = the Vercel URL from step 3.5, exactly,
   no trailing slash (several: comma-separated; `*` is refused on purpose).
2. **Save changes**. Render redeploys (about a minute).

## 5. Check it end to end

1. `https://<render-url>/health/ready` -> `ok: true`, `warmup: ready`.
2. Open the Vercel URL. The **System Health** page should show the API as up. Open the browser dev tools
   (F12) -> Console: no red CORS errors.
3. Analyze a preset ticket. A CORS error means `CORS_ORIGINS` does not match the Vercel URL exactly.

## Things that bite on the free plans

- **Render sleeps after ~15 min idle**; the first request then takes ~1 min. Open the site 5 minutes before the
  demo and click through once.
- **Render's disk is wiped on every deploy and restart.** That is why feedback lives in Neon. The local audit
  file (`backend/data/audit/audit.db`) starts empty after each deploy.
- **Embedding model.** It downloads to `backend/.cache/fastembed` (or `EMBED_CACHE_DIR`) on first use: 65 MB,
  ~15 s. Checked on an empty folder: works with no extra setup. Because the disk is wiped, it downloads again
  after each restart, during the background warm-up (`/health/ready` says `WARMING_UP` meanwhile).
  Optional, for faster cold starts: download it during the build instead, by changing the build command in
  Render to
  `pip install -r requirements.txt && python -c "from app.common.embed import embed_texts; embed_texts(['warm up'])"`.
  If the startup log then shows no download from huggingface.co, it worked.
- **Memory (free plan: 512 MB).** Measured locally: ~55 MB for the app, **~250 MB peak** with the embedding model
  loaded. If Render restarts the service with "out of memory", add the env var **`DOC_SEARCH_DENSE=0`**:
  similar-ticket and KB search then run on BM25 keywords only (no model), and the logs say "using BM25 only".
  Quality drops on paraphrased tickets; everything else works.
- **Secrets:** if a key was ever pasted somewhere public, rotate it at the provider and update Render.
