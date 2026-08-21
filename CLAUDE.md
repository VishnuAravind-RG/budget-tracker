# Budget Tracker — Project Context

Personal budget tracker: FastAPI backend + React PWA frontend. Single user,
bearer-token auth. Bank SMS auto-ingested via a MacroDroid webhook,
categorized via rules + a Claude fallback, tracked against monthly budgets.

**This file is a handoff note for continuing work in a fresh Claude Code
session.** Written 2026-08-16 after the initial build and deployment; updated
2026-08-17 after a second session (working from a *different* local clone at
`C:\bt-dev\repo` on the owner's office laptop — that session initially built
an unrelated standalone local-only prototype before discovering this repo
already existed and was live, then ported the useful ideas in properly). A
session with no memory of either conversation should be able to get oriented
fast from this file alone — keep updating it, don't let it go stale.

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
  budget meters are hand-rolled SVG/CSS (~57KB gzipped total). Seven tabs:
  Home (dashboard, now includes a lending card), Review ("who is this?" —
  see below), Add (manual entry), History (all transactions), Fuel
  (vehicles/mileage), To-do (plain checklist), Budgets (limits + sign-out).
  PWA: manifest + service worker (network-first with offline-shell
  fallback). The tabbar is horizontally scrollable, not a fixed grid — it
  was a 5-column grid sized for exactly 5 tabs; don't revert that when
  adding a tab, it'll squish everything.
- `backend/smoke_test.py` — ~96 end-to-end checks (auth, SMS parsing,
  dedupe, budget math, timezone handling, payee memory, kind-aware
  budgeting, fuel mileage, to-dos, lending). Run it after backend changes.
- `backend/test_migration.py` — simulates an old-schema database with real
  rows already in it, then boots the current code against that same file.
  Run this specifically before any future model change that touches an
  *existing* table/column — `Base.metadata.create_all()` only creates
  missing tables, it never alters one that's already live, so a plain field
  addition needs a matching `_ensure_columns()` entry in `db.py` or it 500s
  in production. This is not optional ceremony; it already caught a real
  bug once (see below).

## Money-movement kinds — added 2026-08-17, this is load-bearing

`Transaction.kind` is what keeps the numbers honest: `expense`, `income`,
`transfer`, `topup`, `lend`, `repayment`. **Only `expense` counts as
spending** — `budget_summary()` and `daily_trend()` in `main.py` filter on
`kind`, not `direction`. Loading a wallet or lending money to a friend is a
real debit but not a purchase; counting it as one is exactly the kind of bug
this field exists to prevent. Don't "simplify" this back to direction-based
filtering.

**Payee memory** (`Payee` table, `backend/categorizer.py`'s `payee_key_for`):
the first time an SMS involves a counterparty with no rule match — a UPI id,
or a card swipe with no VPA at all — it lands in Review asking "who is this:
a shop / a person / my wallet / my own account?", *even if Claude was
confident about the category*, because neither rules nor the AI can tell "OK
but is this actually lending" from a category guess alone. Answering is
remembered by `payee_key` (the UPI id, or `name:<merchant text>` for a
card swipe) and never asked again for that same counterparty. The endpoint
is `PATCH /transactions/{id}/classify`, not the older plain
`/category` PATCH — the History tab still uses the simple one for
re-categorising an already-resolved transaction; Review uses `/classify`.

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

## Fixed in the 2026-08-17 session (payee memory / fuel / to-dos / lending)

- **The actual migration bug `test_migration.py` was written to catch**: the
  first draft of `_ensure_columns()` backfilled the new `kind` column with a
  flat `'expense'` default for every pre-existing row. That's wrong for any
  row that was a `credit` — a historical salary or refund would have been
  silently relabelled as spending, inflating every past month's total the
  next time it was viewed. Fixed to backfill from the existing `direction`
  column instead (`debit`→`expense`, `credit`→`income`) — a `CASE` expression
  in the `UPDATE`, not a flat default. `test_migration.py` asserts this
  exact distinction and will fail loudly if it regresses.
- **Payee-kind vocabulary mismatch**: `classify_transaction()` stores
  `Payee.kind` using `TransactionClassify`'s vocabulary (`expense` /
  `friend` / `wallet` / `self`), but `_ingest()`'s lookup was checking for
  `known.kind == "merchant"` — a value that's never actually stored anywhere.
  Silent bug: a remembered merchant payee would skip Review correctly (the
  `if known:` branch matched) but its category would never get applied,
  landing every repeat transaction in `Uncategorized`. Caught by
  `smoke_test.py`'s "repeat card-swipe merchant gets its remembered
  category" check before this ever reached production. Fixed by matching
  `"expense"`, and documented in a comment on that line so it doesn't
  regress — if you're renaming either vocabulary, grep for the other one
  first.
- **Frontend doesn't know about SMS that arrive while the tab is already
  open**: `App.jsx`'s `refresh()` only ran on mount and after the app's own
  actions (classify/add/delete/save budget) — a real MacroDroid SMS landing
  in the background while the PWA sat open wouldn't show up until something
  else triggered a refetch. Added a `visibilitychange`/`focus` listener that
  re-fetches whenever the tab regains focus, so reopening the app (the
  normal way you'd notice a new SMS) is enough — no polling while
  backgrounded.
- Windows console `UnicodeEncodeError` on the ₹ symbol in `smoke_test.py`'s
  own `print()` output (cp1252 default codepage) — added
  `sys.stdout.reconfigure(encoding="utf-8")` at the top. Doesn't affect the
  Linux deploy target; only mattered for running the suite locally on
  Windows, but worth keeping so that keeps working too.

## Fixed in the 2026-08-21 session (speed, capture, categorisation)

- **"Endless loading" was a sleeping server, not slow code.** Render's free
  tier spins down after ~15 min idle; the next request pays a ~60s cold start
  (measured `/health` at 61s). `.github/workflows/keep-alive.yml` was supposed
  to prevent it, but **GitHub silently drops scheduled runs on free repos** —
  observed gaps between consecutive runs were 34 and 51 minutes, far past the
  spin-down window. Don't trust that cron for anything time-sensitive. The
  service now self-pings its own `RENDER_EXTERNAL_URL` every 9 min from a
  lifespan task in `main.py`. Verified: after 12 min of zero external traffic,
  `/health` answered in **0.38s**. This costs ~744 of the 750 free
  instance-hours/month, so **it must stay the only free web service in that
  Render account**.
- **First paint went 3673ms → 90ms** via `frontend/src/cache.js`: the last
  successful month is snapshotted to localStorage and painted immediately,
  with the network refresh overwriting it. Verified it still renders in ~100ms
  with the API entirely blocked. The cache is deliberately weak — written only
  after a *fully* successful refresh, never suppresses the fetch,
  shape-versioned, 3 months max, cleared on sign-out.
- **The AI was never the problem — the parser was.** `parse_sms()` preferred
  the VPA over the payee name sitting right beside it in parentheses, so every
  merchant was stored as `q743985996@ybl`, and *that* opaque handle was what
  got handed to the categoriser. Nothing can categorise it, so nearly
  everything fell into review. The name now wins for display and for the AI;
  the VPA keeps its real job as the identity key, so existing payee memory
  still matches. Backfilled 44 transaction names and 18 payee labels; review
  queue went 29 → 12, and the 12 left are all *personal names*, which
  correctly still need a human answer. **Guard:** some banks put a reference
  number in those same parentheses (`(UPI Ref 402913)`) —
  `_looks_like_reference()` rejects those, and an existing test caught the
  first cut that didn't.
- **Vercel deployed twice and emailed a failure on every push.** Its own git
  integration built from the repo root, where there is no `package.json`,
  while the Actions workflow quietly succeeded and served the real site. Root
  `vercel.json` points that second build into `frontend/`.
- **Fuel takes a trip-meter reading now**, not just an odometer — the trip
  reading at a fill *is* the distance that tankful covered, so mileage comes
  from one record. First cut was silently dead: the leg filter demanded a
  distance on both fills, but the earlier one has none by definition (the trip
  was only just reset). Only the later fill carries distance + litres.
- **A remembered answer can be forgotten** (`DELETE /payees/{key}` plus a
  control in the Remembered list). It was a one-way door before: a mis-tap in
  Review mis-filed that payee forever. Deliberately does not rewrite existing
  transactions — that would move historical totals under the user.
- Categoriser order is now Claude → **Azure OpenAI** → Gemini. Gemini's free
  tier is 20 requests **per day per model** (confirmed from Google's own error
  body), which cannot survive a backlog pass. Also `gemini-flash-latest`
  silently resolved to a preview model carrying that tiny quota — **pin the
  model, never use a `-latest` alias here**.

## Verification before this was pushed

Both `smoke_test.py` (96 checks) and `test_migration.py` (17 checks) passed
locally against SQLite before anything was pushed. After pushing: watched the
Render deploy via the Render API until `status: live`, then re-verified
against the **real production API and Postgres database** — `/health`,
`/categories` (confirms the new `Lending` category deployed), `/vehicles`
(confirms the three-vehicle seed ran), and a manual add-then-delete round
trip to confirm the `kind` field works end-to-end in production, not just
locally. Existing production data (four budget limits, ₹12,000 total) was
confirmed intact after the migration — the transactions table itself was
empty at deploy time (no real SMS had landed yet), so there was nothing at
risk there specifically, but the migration path is what protects whatever
lands from here on. Frontend: confirmed the `deploy-frontend.yml` GitHub
Action ran and succeeded automatically (it triggers on `frontend/**`
changes), then fetched the live bundle from Vercel and confirmed its byte
size matched the local build exactly, plus grepped for new-feature strings
(`"Log a fill-up"`, `"Money lent out"`) to confirm it wasn't a stale cache.

**As of 2026-08-21:** `smoke_test.py` is at **144 checks**, still passing.
There is also a browser suite (22 checks, Playwright, driven against real
production) covering every tab rendering *real content* rather than a
spinner, the Review and Add lend-vs-settle questions, the fuel trip/odo
toggle, the Remembered list, an add-then-verify-then-delete round trip, dark
mode, and a zero-console-errors assertion. Playwright is deliberately **not**
a repo dependency — install it in the OS temp dir when you need it.

Two habits that paid off and are worth keeping:
- **Distrust a "deployed" signal that can't distinguish states.** Polling
  `DELETE /payees/x` for a 404 proved nothing: a missing *route* and a
  missing *payee* both return 404. Check `openapi.json` for the route, or
  grep the served bundle for a new string.
- **When a test fails, decide whether the test or the code is wrong before
  touching either.** Both happened this session: the `(UPI Ref 402913)` case
  was a real bug in new code, while `remembered names are readable` was an
  assertion too absolute for a message that genuinely carries no name.

## Open questions for the owner — not this session's call to make

- **The transactions table was empty at the start of this session.** Worth
  confirming next time you talk to the owner: is MacroDroid actually
  configured and forwarding SMS yet, or is that still a pending phone-side
  step from the original setup? The backend/frontend are both ready either
  way, but "is it actually capturing real spends" is a different question
  from "is it deployed."
- **`ANTHROPIC_API_KEY` is still unset.** Unrecognised merchants (that also
  aren't a brand-new payee needing the who-is-this prompt) land in
  Uncategorized/needs_review rather than getting an AI guess. Fine as a
  default; ask before adding a paid key on the owner's behalf.
- A **native Android app for reading SMS directly** came up as an idea in a
  parallel local-prototype conversation the same day — deliberately *not*
  pursued here, because MacroDroid already does this without any app-store
  presence, code signing, or maintenance burden. Don't build one unless the
  owner explicitly says MacroDroid isn't sufficient for some concrete
  reason.

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
