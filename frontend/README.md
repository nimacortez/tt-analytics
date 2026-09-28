# TT Analytics frontend

Next.js 16 App Router. Pages are Server Components that call the FastAPI backend
server-side (`lib/api.ts`), so there's no CORS setup and no client-side state:
filters and the points line live in the URL.

```bash
npm install
npm run dev          # http://localhost:3000 (needs the API on :8000)
npx tsc --noEmit && npm run lint
```

`API_URL` overrides the backend address (default `http://localhost:8000`).

- `/` match list: league / status filters, adjustable total-points line, form window
- `/matches/[id]` model cards, points-total distribution, per-player stats with CIs and shrinkage
