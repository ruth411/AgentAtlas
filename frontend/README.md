# AgentAtlas — Dashboard

Minimal Next.js demo dashboard for AgentAtlas. Built for the v1 demo video
and as a "first look" surface when someone runs the project locally.

## What's here

Four pages, designed for clarity not flash:

| Route | What it does |
|---|---|
| `/` | Hero + the "Try a query" interactive section with 3 one-click examples. |
| `/tools` | Lists every published canonical `ToolSpec` with verification level + highest risk level chips. |
| `/tools/[tool_id]` | Full spec view: capabilities, commands, auth, risk profile, provenance. |
| `/query` | Standalone `validate_command` playground with a JSON-dump toggle. |

Two reusable components: `RiskBadge` and `VerdictCard`. Everything else is
plain Tailwind on top of Next.js's App Router.

## Local setup

Requires **Node.js 18+** and the AgentAtlas backend running on
`http://localhost:8000` (configurable — see below).

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:3000>.

`next.config.mjs` proxies every `/api/*` request from the dashboard to the
FastAPI backend, so the browser only ever talks to its own origin — no CORS
configuration on the backend side. Override the proxy target if needed:

```bash
AGENTATLAS_API_URL=http://localhost:9000 npm run dev
```

## What it expects

A seeded graph. Run the seed script first:

```bash
# from the repo root
backend/.venv/bin/python scripts/seed_examples.py --reset
```

Without seeded data the `/tools` page renders the empty-state.

## Design choices

- **No design system / no shadcn CLI.** Two hand-written components plus
  Tailwind utilities. The demo's aesthetic is "developer tool, not consumer
  app." Keeps the visual budget under control.
- **Server components for read pages**, client components only where
  interaction is required (`/`'s "Try a query" section and the entire
  `/query` page). The proxy means RSCs talk to the backend directly while
  client components talk to `/api/*`; both end up at the same FastAPI process.
- **Typed API client in `lib/api.ts`** — hand-mirrored from the Pydantic
  response models, not codegen'd. The response shapes are stable now; if
  they ever change the type errors point at the right files to update.
- **No tests in this directory yet.** The demo dashboard is intentionally
  thin; visual review covers v1. End-to-end tests live in the planned
  separate E2E repo per the project owner's roadmap.

## Production build

```bash
npm run build
npm run start
```

Or export static and serve from the FastAPI process (future Stage 12 work).
