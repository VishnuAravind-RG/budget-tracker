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

### Later the same day — dates, dedupe, and two self-inflicted bugs

- **A transaction was dated when it was INGESTED, not when it happened.**
  Fine for a live SMS, wrong for anything backfilled: a fortnight of email
  alerts imported in one go all landed on the import date, drawing the
  spending trend as a flat line and then a vertical cliff.
  `parse_alert_date()` now reads the bank's own date (four formats) and books
  against it. Guarded: unparseable falls back to now, future dates are
  rejected as mis-parses, and a bare 12-digit UPI reference must never be
  read as a date.
- **That immediately broke dedupe, silently.** The duplicate check compared
  `created_at >= now - 120s`, and `created_at` had just become a date hours
  in the past — so it matched nothing and every repeat of an identical alert
  was booked again. MacroDroid retries on flaky mobile data, which is exactly
  what that window exists to absorb, so this would have **double-counted real
  spending**. Fixed by splitting the two meanings: `created_at` = when it
  happened, `ingested_at` = when the row was written, dedupe uses the latter.
  *Lesson: when a field's meaning changes, grep every comparison against it.*
- **Bank footer prose was parsed as a merchant name** — a real card alert was
  booked as "support you in every step of t", from HDFC's sign-off. Two
  causes: the `To <name>` rule added for the Sent/From/To SMS shape matched
  "to" anywhere (now anchored to start-of-line), and the merchant loop only
  examined each pattern's *first* match, so one rejected candidate abandoned
  the whole pattern — `finditer`, not `search`.
- **The Gmail poll workflow reported SUCCESS while returning 401 for two
  days.** It only emitted `::warning::` on failure, which does not fail a
  job. Nothing was being captured while every run showed a green tick. Both
  workflows now `exit 1`, and the poll says explicitly when `APP_AUTH_TOKEN`
  is missing. **A silent capture pipeline is worse than none** — you believe
  your spending is tracked when it isn't.
- Mileage: **one trip-meter fill is enough.** A trip reading is
  self-contained (reset at the last fill, it already encodes that tankful's
  distance); only the odometer route needs two readings to subtract. Applying
  the odometer rule to trip data made a complete entry report no mileage.
- Added: a free-text `note` (asked for when the category is "Other", which
  explains nothing), and `POST /lending/{person}/repaid` for cash repayments
  — previously untrackable, so a settled debt nagged forever.

### Bug-hunt pass — what a systematic audit turned up

Ordered by how much money each could quietly get wrong. All fixed, each
with a test written to fail first.

1. **A race booked one payment six times.** Six simultaneous copies of one
   alert all passed the "does this reference exist?" check before any
   committed: Rs 1,998 counted instead of Rs 333. MacroDroid genuinely
   retries on flaky mobile data, so this was reachable in normal use. An
   application-level check-then-insert cannot hold under concurrency — the
   guarantee now lives in the schema as a UNIQUE index on `bank_ref`, with
   `_ingest` catching the violation and returning the winning row. Verified
   at 8, 12 and 20 concurrent requests: one row every time.
2. **A remembered "paying back what I owe" payee was counted as spending.**
   `_ingest`'s remembered-payee branch hand-rolled its own copy of the kind
   mapping and drifted — `friend_settle` matched no case and fell through to
   a plain expense. Four live payees were stored that way. **This is the
   second time that branch has drifted** (the first was "merchant" vs
   "expense"), so it now calls `_resolve_kind` like every other caller.
3. **Self-transfers were counted as income.** A real Rs 10,000 credit was
   money moved in from the owner's own SBI account; the alert says so
   ("Sender: ... (VPA: rgvishnuaravind@oksbi)") but nothing read that line.
   Credits now yield a sender and are asked about once — scoped to credits
   carrying a real UPI id, so salary credits stay out of the queue.
4. **`bank_ref` was added nullable with no backfill**, so every pre-existing
   row had NULL and reference dedupe protected none of them. That is how the
   Rs 10,000 credit got stored twice in the first place.
5. **One month's total appeared under another month's header.** The snapshot
   effect merged with what was on screen, so a month with no snapshot kept
   the previous month's figures until the fetch landed.
6. **No cap on hand-entered amounts** — fuzzing accepted a fifteen-digit
   figure, and a fat-fingered extra zero would wreck every total it touches.

Clean on the first pass, worth not re-testing blindly: all 175 rows satisfy
kind/direction/category consistency; every total reconciles across
`/budget/summary`, `/stats/summary`, `/stats/daily` and the raw rows; 28 of
29 hostile inputs were correctly rejected (the 29th is Cloudflare's WAF, not
the app); timezone boundaries put a 23:59 IST spend in the right month;
auth drops a revoked token and clears the cache with it; and the app still
renders from cache with the network cut while refusing to fake a write.

## Added in the 2026-08-22 session (six enhancements)

Six things the owner asked for in one go, after asking what was worth doing
next. Each one came out of something visible in their real data.

### 1. Capture-quiet alarm — `/stats/capture-health` + `CaptureWarning.jsx`

**The failure this exists for:** MacroDroid stopped forwarding SMS for two
days and *nothing looked wrong anywhere*. Totals simply grew more slowly. The
gap is still visible in the history (7 Aug 2026 has zero transactions in a
month where every other day has several) and it was only noticed by accident,
days later.

Warns when nothing has arrived automatically for `CAPTURE_QUIET_HOURS`
(default 36, env-tunable). Measured on `ingested_at`, never `created_at` —
back-dated alerts make created_at useless here, since an email arriving today
about last Tuesday would read as five days of silence.

Two details that matter:
- **`manual_since`** counts transactions typed in by hand since the last
  automatic one. That is the tell separating "quiet because you haven't spent
  anything" from "quiet because the pipe is dead".
- **A channel that has never delivered is not reported as broken.** Gmail
  polling was connected long after SMS forwarding; calling a pipe that was
  never plugged in "quiet" is noise, not a warning.

`_ingest()` now records *which* channel delivered (`source` is `"sms"` or
`"gmail"`), so the warning can name what to go and check.

### 2. Remembered answers became correctable — `PATCH /payees/{key}`

**The bug in live data:** RADDLINS FOOD, a bakery, was answered as "a person".
Its payments were filed as money lent out, and it sat in the who-owes-you list
beside actual friends. A remembered payee is never asked about again, so
nothing would ever have surfaced it.

`DELETE /payees` existed but only stops an answer being applied *in future* —
it leaves everything it already mis-filed alone. The new PATCH changes the
answer and, with `apply_to_past`, re-runs every transaction it decided back
through `_resolve_kind()` (the same function that classified them, so the two
cannot disagree about what "friend" means). Off by default: it moves
historical totals, which should be a choice.

`categorizer.business_hint()` returns *why* a counterparty looks like a
business — "a Vyapar shop-billing UPI id", "'agencies' is a trading-name
word" — rather than a bare boolean, so it can be checked against what the
owner actually knows. Shown in Review before "a person" is saved, and against
existing answers in the Remembered list. Verified against every real payee in
production: catches the shops, flags none of the real friends. **Getting this
backwards would be worse than not having it** — auto-filing a real friend as
a shop would silently destroy lending tracking — so it only ever asks.

### 3. Screenshot imports are batched and undoable

Rows used to be POSTed one at a time through `/transactions/manual`, which
meant `source="manual"`: indistinguishable afterwards from things typed by
hand, no way to see what an upload brought in, no way to take one back. And a
six-row screenshot was six round trips to a single-worker backend, so a
failure halfway left half a screenshot imported with no record of which half.

Now `POST /transactions/screenshot-import` writes the lot in one commit with
`source="screenshot"` and a shared `import_batch` id; `GET /imports` lists
them and `DELETE /imports/{batch}` undoes one, touching only its own rows.
The Add tab no longer jumps to Home after an import — the Undo button would
have gone with it.

The endpoint also refuses to book a `Transfer`-categorised row as an expense
even if the client asks it to. The client already had that bug once and
booked a GPay "Self transfer" as ₹10,000 of spending.

### 4. Credit alerts that name a sender — and ones that don't

Three real credits totalling ₹11,912 (one of them ₹10,000) were stored as
merchant `Unknown`. HDFC's email template puts the sender in a lettered field
(`a. Date: … b. Sender: …`) and the pattern required a parenthesised VPA
beside the name, which those messages don't carry.

- `CREDIT_SENDER_NAME_RE` reads a sender named without a VPA. Credits only —
  `From:` in a debit alert means the account the money *left*, and reading
  that as a counterparty is the bug that once booked a card swipe against the
  owner's own bank.
- When nothing names the sender, the row now reads **"Credit to HDFC …9393"**
  instead of "Unknown", and is queued for review. `Unknown` reads as a parse
  failure and hides the real situation: the alert genuinely never said.
- Critically, an unnamed credit gets **no `payee_key`**. Keying off that
  placeholder would fold every anonymous credit from anyone into one
  remembered "payee" that is never asked about again.

### 5. Recurring detection now says whether it has happened — `/stats/recurring`

Detection already existed but only described the past. "You pay this every
month" is a fact, not a prompt. Each repeat is now `paid` / `due` / `overdue`
for the current month, with the day it usually lands and the amount it usually
is — both **medians**, so one unusually large grocery run doesn't drag the
expectation up and one early payment doesn't drag the date to the 1st.

Moved server-side in the process. The client version derived it from an extra
unfiltered fetch of the last 200 transactions — an arbitrary window that
silently truncated the history the detection depends on, and a second full
transaction list down the wire on every refresh. `frontend/src/recurring.js`
is gone; the cache SHAPE was bumped to 2 so v1 entries are discarded rather
than half-read.

Spreadsheet-imported rows (`source="import"`) count as evidence a month
happened, but their *day* is excluded — they're anchored to the 1st, and
counting that would drag every expected date towards the start of the month.

### 6. Nightly encrypted backup — `/export/all` + `.github/workflows/backup.yml`

Everything lived in one free-tier Supabase project with no backup and no
export route, and rows have been deleted by hand more than once while
correcting double-counted months.

**The encryption is not optional and must not be "simplified" away.** This
repository is public, and workflow artifacts on a public repo can be
downloaded by anyone who can see the run. An unencrypted artifact would
publish the complete transaction history. The passphrase is checked *before*
the export runs, so a missing secret can never leave plaintext on the runner;
the plaintext is deleted after encryption; the upload names one file rather
than a glob. A run with no `BACKUP_PASSPHRASE` fails, it does not fall back.

The export deliberately **excludes the `gmail_auth` table**: it holds a Google
OAuth refresh token, which is a live credential, not data. Re-connecting Gmail
after a restore is one visit to `/gmail/auth/start`; leaking a token that
never expires is not undoable.

Needs two repository secrets — `APP_AUTH_TOKEN` and `BACKUP_PASSPHRASE`. Keep
a copy of the passphrase somewhere other than GitHub: a backup you cannot
decrypt is not a backup.

### Also fixed while in there

`smoke_test.py` printed its pass/fail summary **in the middle of the file**.
Every check written after that point printed PASS/FAIL but could not affect
the exit code — a failing one would have exited 0 under "All checks passed".
The report is now last, and says how many checks it counted.

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

**As of 2026-08-22 (end of session):** `smoke_test.py` is at **300 checks**, `test_migration.py` at 22, both passing.
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
- **A green CI tick proves nothing unless the job can actually go red.** Two
  separate jobs here reported success while doing nothing, because a
  `::warning::` does not fail a run.
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
