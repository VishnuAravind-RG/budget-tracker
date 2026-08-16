# Budget Tracker — Project Context

Personal budget tracker: FastAPI backend + React PWA frontend. Single user,
bearer-token auth. Bank SMS auto-ingested via a MacroDroid webhook,
categorized via rules + a Claude fallback, tracked against monthly budgets.

**This file is a handoff note for continuing work in a fresh Claude Code
session.** Written 2026-08-16 after the initial build and deployment, so a
session with no memory of that conversation can get oriented fast. Update it
as things change — it should stay a live "state of the world" doc, not a
frozen snapshot.

## What's live right now

| Component | Where | URL |
|---|---|---|
| Frontend (PWA) | Vercel | https://budget-tracker-blue-gamma.vercel.app |
| Backend API | Render (free tier) | https://budget-tracker-api-jkub.onrender.com |
| Database | Supabase Postgres (Mumbai / ap-south-1) | via the connection pooler |
| Source | GitHub (public) | https://github.com/VishnuAravind-RG/budget-tracker |

Installed on the owner's phone as a PWA (Add to Home Screen). MacroDroid on
their phone forwards bank SMS to `/sms/ingest`.

## Where secrets actually live (never in this repo)

- `AUTH_TOKEN` — the app's single shared login secret. Lives in the owner's
  local `backend/.env` (gitignored) and in Render's dashboard → service →
  Environment. If lost: generate a new one
  (`python -c "import secrets; print(secrets.token_urlsafe(32))"`), set it in
  both places, and re-log-in on the phone.
- `DATABASE_URL` — Supabase pooler connection string, includes the DB
  password. Only in Render's dashboard → Environment. Recoverable from
  Supabase's dashboard → Project Settings → Database → Connection string
  (use the **transaction pooler**, not the direct connection — see below).
- `ANTHROPIC_API_KEY` — not set. Left blank deliberately; unrecognized
  merchants land in the "Needs review" queue instead of being AI-categorized.
  Optional to add later.
- GitHub Actions secrets (`VERCEL_TOKEN`, `VERCEL_ORG_ID`,
  `VERCEL_PROJECT_ID`) — encrypted in this repo's Settings → Secrets, used
  only by `.github/workflows/deploy-frontend.yml`. A new Vercel token can be
  minted at vercel.com/account/tokens if the current one is ever revoked.

## Architecture

- `backend/` — FastAPI. `main.py` (routes), `auth.py` (single bearer-token
  dependency — **the app refuses to boot without `AUTH_TOKEN` set**, by
  design), `categorizer.py` (SMS regex parsing + ~50 hardcoded Indian
  merchant rules, Claude fallback only for unrecognized ones — JSON-schema
  constrained output, low effort, thinking off since it's classification not
  reasoning), `db.py` / `models.py` (SQLAlchemy — SQLite locally, Postgres in
  prod), `timeutil.py` (timestamps stored as naive UTC; month boundaries
  computed in `TZ_NAME` so late-night spends land in the right month).
- `frontend/` — React 18 + Vite. No chart/UI library — the trend line and
  budget meters are hand-rolled SVG/CSS (~53KB gzipped total). Five tabs:
  Home (dashboard), Review (AI-unsure transactions), Add (manual entry),
  History (all transactions), Budgets (limits + sign-out). PWA: manifest +
  service worker (network-first with offline-shell fallback).
- `backend/smoke_test.py` — ~60 end-to-end checks (auth, SMS parsing,
  dedupe, budget math, timezone handling). Run it after backend changes.

## Why these specific hosting choices

The explicit requirement was free-forever, no card, anywhere.

- **Railway** was the original plan but requires a card on file for real
  deployments (trial credit only, then paid) — dropped after directly
  asking "will it work forever free."
- **Render** free web service: no card, but sleeps after 15 min idle
  (~30–50s cold start on wake). Mitigated by the keep-alive workflow below
  rather than accepted as a permanent tradeoff.
- **Supabase** free Postgres: no card, no forced expiry. Uses the
  **transaction-mode pooler** (`aws-0-ap-south-1.pooler.supabase.com:6543`),
  *not* the direct connection — the direct host is IPv6-only and most hosts
  (Render included) can't reach it.
- **Vercel** free static hosting for the frontend — standard, no caveats.

## CI/CD — a plain `git push` updates everything, from any machine

- `render.yaml` is a Render Blueprint. Render's own GitHub integration is
  connected, so the **backend auto-deploys on push already** — no workflow
  needed for it.
- `.github/workflows/deploy-frontend.yml` — Vercel has no equivalent native
  Git connection here (that needs a one-time *interactive browser*
  authorization of Vercel's GitHub App, which was never completed — a CLI
  token isn't sufficient to grant that). This workflow does the same job via
  the Vercel CLI + the repo secrets above. Only triggers on changes under
  `frontend/**`.
- `.github/workflows/keep-alive.yml` — pings the Render `/health` endpoint
  every 10 minutes, forever, so the backend doesn't sleep. **Requires the
  repo to stay public** — private repos get only 2,000 free Actions
  min/month, and this ping alone needs ~4,300. Made public deliberately for
  this; confirmed no secrets or financial data live in the repo, only code
  (verified by grepping the full git history for every secret used before
  making that call).

## Fixed in the session that built this

- **Accidental sign-out bug**: `App.jsx` used to render a full-width "Sign
  out" button unconditionally, directly under the Budgets tab's "Save
  budgets" button, with no confirmation — a mistapped tap logged the user
  out. Fixed by moving it to a small, visually separated, `confirm()`-gated
  text link in `Budgets.jsx`. First place to check if sign-out/auth
  complaints come up again.
- Backend bugs from the original build (full detail in git log): a
  month-boundary timezone bug (UTC vs local calendar month), `postgres://`
  vs `postgresql://` URL scheme handling, categories with spend but no
  budget limit being invisible in the summary, junk SMS (OTPs/promos) being
  stored as real transactions, salary credits incorrectly queued for AI
  review instead of auto-filing as `Income`, and a merchant-name regex that
  missed the common "debited **for** X" SMS phrasing.

## Known non-issues — don't "fix" these

- `CORS_ORIGINS=*` on Render — auth is a bearer token via header, not a
  cookie, so this was never a real vulnerability. Tightening it to the exact
  Vercel origin is optional tidiness, not a security requirement.
- Render cold starts — mitigated by the keep-alive ping above; shouldn't
  recur in normal use. If it does, check the Actions tab for the workflow's
  run history first before assuming the app itself is broken.

## Working habits worth continuing

- Everything shipped was verified against the **real deployed
  infrastructure**, not just a local build — e.g. confirming the exact JS
  bundle served in production contains the intended backend URL / fix text,
  and running real HTTP requests against the live API before calling
  something done.
- Before committing anything, check `git status`/`git diff` for secrets —
  this project already had one near-miss (`.claude/settings.local.json`
  captured a raw token in a local permission log; caught before it was
  staged, now gitignored). Re-check this instinct especially given the repo
  is public.
- `README.md` has the full setup/deploy/MacroDroid walkthrough and is kept
  up to date — read it for anything not covered here.
