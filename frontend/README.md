# Frontend

Vite + React + TypeScript + Tailwind. It talks to the FastAPI backend through one client (`src/lib/api.ts`) that understands the `{ok, data, error}` envelope.

## Scripts

```powershell
cd frontend
npm ci
npm run dev            # http://127.0.0.1:5173 (the origin the backend's CORS allows)
npm run test -- --run  # all tests once
npm run lint           # ESLint, zero warnings allowed
npm run build          # type-check + production build into dist/
```

Start the backend first, from `backend/`: `.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`

## Pages

| Page | Route | Backend calls |
|---|---|---|
| Home | `/` | none yet (filled in once the problem is known) |
| Audit History | `/audit` | `GET /api/audit/verify`, `GET /api/audit/results`, `GET /api/audit/results/{id}` and `/feedback` |
| System Health | `/system-health` | `GET /health`, `GET /health/ready` |

The page is `/system-health`, not `/health`, so it never collides with the backend's `/health` behind the dev proxy.

## Adding a page

1. Create `src/pages/MyPage.tsx`.
2. Add its route in `src/App.tsx` and its menu entry in `src/lib/nav.ts`.
3. Add its backend calls to `makeEndpoints` in `src/lib/api.ts` and its types to `src/lib/types.ts` (use the backend's field names).
4. Add a test in `src/test/` using `mockBackend` and `renderApp` from `src/test/utils.tsx`.

## Backend URL

| Variable | Default | Meaning |
|---|---|---|
| `VITE_API_BASE_URL` | empty | Empty: same-origin calls, and the dev server proxies `/api` and `/health` to the backend. |
| `VITE_PROXY_TARGET` | `http://127.0.0.1:8000` | Where the dev proxy sends requests. |

Copy `.env.example` to `.env.local` (git-ignored) to change them. Every `VITE_*` value is visible in the browser: never put a secret there.

## Rules

- No HTML from the backend is ever rendered (lint forbids `dangerouslySetInnerHTML`).
- Nothing is written to `localStorage`, `sessionStorage` or cookies.
- Fonts (Inter, JetBrains Mono) are bundled with `@fontsource`, so the app works offline.
