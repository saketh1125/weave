# Weave Frontend

Vite + React + TypeScript + Tailwind CSS + shadcn/ui.

## Dev

```bash
npm ci
npm run dev      # http://localhost:5174
npm run build    # production build
```

## Env

Copy `.env.example` to `.env`:

```
VITE_API_URL=http://localhost:8001/api
```

## Routes

- `/` — Repos
- `/findings` — Findings
- `/pr-risk` — PR Risk
- `/score` — Score

API client: `src/lib/api.ts` (typed fetch, `VITE_API_URL`).
