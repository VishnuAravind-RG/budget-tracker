# Budget Tracker

A personal budget tracker. Bank SMS land on your phone, MacroDroid forwards them to
the API, they get parsed and categorized automatically, and you check the dashboard
from an installable PWA.

Single user. One shared token. No accounts, no multi-tenancy.

```
  bank SMS ──▶ MacroDroid ──▶ POST /sms/ingest ──▶ FastAPI ──▶ Postgres
   (phone)     (your phone)     (Railway)          rules first,   │
                                                   Claude second  │
                                    React PWA ◀────────────────────┘
                                  (Vercel/Netlify)
```

## What's in here

| Path | What it is |
|---|---|
| `backend/main.py` | API routes |
| `backend/auth.py` | Bearer-token dependency (every route but `/health`) |
| `backend/categorizer.py` | SMS parsing + categorization (merchant rules, then Claude) |
| `backend/models.py` · `db.py` | SQLAlchemy schema and session |
| `backend/timeutil.py` | Naive-UTC storage, local-timezone month boundaries |
| `backend/smoke_test.py` | 60-check end-to-end test — run it after any change |
| `frontend/src/` | React PWA (dashboard, history, review queue, add, budgets) |
| `frontend/public/sw.js` · `manifest.webmanifest` | Service worker + install manifest |

No chart library — the trend line and budget meters are hand-rolled SVG/CSS.
The whole frontend is 53 KB gzipped.

---

## Run it locally

**Backend** (SQLite, no Postgres needed):

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env          # then edit AUTH_TOKEN
uvicorn main:app --reload     # http://127.0.0.1:8000
```

Generate a token for `.env`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

The app **refuses to start without `AUTH_TOKEN`** — that's deliberate, so you can't
accidentally deploy an open API. `ANTHROPIC_API_KEY` is optional; without it,
anything the merchant rules don't recognise goes to the **Needs review** queue
instead of being guessed.

Check it works:

```bash
cd backend && python smoke_test.py     # should print "All checks passed."
```

**Frontend:**

```bash
cd frontend
npm install
cp .env.example .env.local    # VITE_API_URL=http://127.0.0.1:8000
npm run dev                   # http://localhost:5173
```

Open it, paste the same `AUTH_TOKEN`, and you're in.

---

## Deploy

Free-forever stack, no card required anywhere: **Supabase** (Postgres) +
**Render** (backend) + **Vercel** (frontend). The one real tradeoff: Render's
free web service sleeps after 15 min idle and takes ~30–50s to wake on the next
request — fine for a personal app, annoying if you want instant loads every time.
(Railway has no cold starts but requires a payment method on file — swap it in
by pointing `render.yaml`'s DB steps at Railway's Postgres plugin instead, if
you'd rather pay ~$0–5/mo for that.)

### 1 · Push to GitHub

```bash
cd "budget tracker"
git init
git add .
git commit -m "Budget tracker"
git remote add origin https://github.com/<you>/budget-tracker.git
git push -u origin main
```

`.gitignore` already excludes `.env`, `*.db`, `node_modules/` and `dist/`.
**Double-check `git status` doesn't list `.env` before pushing.**

### 2 · Database on Supabase

1. [supabase.com/dashboard](https://supabase.com/dashboard) → sign up (free, no card) → **New Project**.
2. Pick a region close to you, set a database password, create.
3. **Project Settings → Database → Connection string → Transaction pooler.**
   Use the **pooler** connection (port `6543`), not the direct one — the direct
   host is IPv6-only and most hosts (Render included) can't reach it.
   It looks like:
   ```
   postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
   ```
   That whole string is your `DATABASE_URL`.

### 3 · Backend on Render

The repo includes [`render.yaml`](render.yaml) — a Blueprint that pre-fills
everything.

1. [dashboard.render.com](https://dashboard.render.com) → sign up (free, no card).
2. **New → Blueprint** → connect your GitHub account → select the repo.
3. Render reads `render.yaml` and shows a form for the four secret values:

   | Variable | Value |
   |---|---|
   | `AUTH_TOKEN` | your long random string (the one you paste into the app to log in) |
   | `DATABASE_URL` | the Supabase pooler string from step 2 |
   | `ANTHROPIC_API_KEY` | `sk-ant-…` from [console.anthropic.com](https://console.anthropic.com) — optional, leave blank to skip AI categorization |
   | *(everything else)* | pre-filled from `render.yaml` |

4. **Apply.** First build takes a few minutes. You get a URL like
   `https://budget-tracker-api.onrender.com`.
5. Verify: `curl https://<your-app>.onrender.com/health` → `{"status":"ok"}`
   *(if it just woke from sleep, the first request can take ~30–50s)*

Tables are created on first boot; there's no migration step.

### 4 · Frontend on Vercel *(or Netlify)*

**Vercel:** [vercel.com/new](https://vercel.com/new) → import the repo →

- **Root Directory:** `frontend`
- **Framework Preset:** Vite (auto-detected)
- **Environment Variables:** `VITE_API_URL` = `https://<your-app>.onrender.com`
  *(no trailing slash)*
- Deploy.

**Netlify:** [app.netlify.com](https://app.netlify.com) → **Add new site → Import an existing project** →

- **Base directory:** `frontend`
- Build command and publish dir come from `netlify.toml`
- **Site configuration → Environment variables:** `VITE_API_URL` = your Render URL

`vercel.json` / `netlify.toml` already handle the SPA rewrite and stop the service
worker from being cached.

> `VITE_API_URL` is baked in at **build** time. If you change it, redeploy — editing
> the variable alone won't update the live site.

### 5 · Lock down CORS

Back in Render → your service → **Environment** → set

```
CORS_ORIGINS = https://your-frontend.vercel.app
```

Render redeploys automatically. (Auth is a header token, not a cookie, so `*` was
never a security hole — this is just tidiness.)

### 6 · Install the PWA

- **Android/Chrome:** open the site → **⋮ → Add to Home screen** → it installs as a
  standalone app with its own icon.
- **iOS/Safari:** **Share → Add to Home Screen.**
- **Desktop Chrome/Edge:** install icon in the address bar.

---

## MacroDroid setup

This is the "no manual entry" part. One macro, three parts.

**Install [MacroDroid](https://play.google.com/store/apps/details?id=com.arlosoft.macrodroid), then: Add Macro (+).**

### Trigger

- **Add trigger → Device Events → SMS Received**
- **Sender:** leave blank for any sender, or enter your bank's sender ID.
  Wildcards work — `*HDFC*` catches `AD-HDFCBK`, `VM-HDFCBK`, and friends.
  Comma-separate several: `*HDFC*,*ICICI*,*SBI*`
- **Content:** leave as *Any*. The backend already discards OTPs, promos and
  payment reminders, so over-matching here is harmless.

### Action

- **Add action → Connectivity → HTTP Request**
- **Method:** `POST`
- **URL:** `https://<your-app>.up.railway.app/sms/ingest`
- **Content Type:** `application/json`
- **Body:**

  ```json
  {"text": "[sms_message]"}
  ```

- **Headers → Add header:**

  | Name | Value |
  |---|---|
  | `Authorization` | `Bearer YOUR_AUTH_TOKEN` |

  (Literally the word `Bearer`, a space, then the token.)

### Constraints

None. Leave empty.

Name it "Bank SMS → Budget" and save.

**Test it:** send yourself a text like
`Rs.499.00 debited from A/c XX1234 to VPA swiggy@icici` — it should appear in the
app within a couple of seconds, already filed under Food & Dining.

**If a bank SMS contains a double quote** it will break the JSON body and MacroDroid
will get a 422. Switch that macro to the raw endpoint instead — same headers, but:

- **URL:** `https://<your-app>.up.railway.app/sms/ingest/raw`
- **Content Type:** `text/plain`
- **Body:** `[sms_message]` (no JSON, no quotes)

> `[sms_message]` and `{sms_message}` are equivalent in MacroDroid. Square brackets
> are used above so the placeholder can't be confused with the JSON braces around it.

---

## How categorization works

1. **Rule match (free, instant).** ~50 common Indian merchants — Swiggy, Blinkit,
   Uber, IRCTC, Netflix, Zerodha… — matched against the merchant *and* the raw SMS.
   Most transactions stop here and never touch the API.
2. **Credits skip the AI entirely** and file as `Income`.
3. **Claude fallback.** Anything left goes to the Messages API with a JSON schema
   constraining the reply to a known category plus a `confident` flag. Low effort,
   thinking off — it's a classification, not a reasoning task.
4. **`confident: false` → the Needs review queue**, where you tap the right category.
   Nothing is ever silently mis-filed.

If the API call fails for any reason, the transaction is still saved as
`Uncategorized` + needs review. **A categorization failure never loses a transaction.**

### Cost

Only step 3 costs anything, and only for merchants the rules miss. Each call is a
couple of hundred tokens. If you want it cheaper, set `ANTHROPIC_MODEL=claude-haiku-4-5`,
or add your regular merchants to `OBVIOUS_MERCHANTS` in `backend/categorizer.py` —
rules are free and instant.

---

## API reference

Every route except `/health` needs `Authorization: Bearer <AUTH_TOKEN>`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Unauthenticated healthcheck |
| `GET` | `/me` | Token validation (used by the login screen) |
| `POST` | `/sms/ingest` | `{"text": "..."}` — the MacroDroid webhook |
| `POST` | `/sms/ingest/raw` | Same, body is the raw SMS text |
| `POST` | `/transactions/manual` | `{amount, direction, category, merchant?}` |
| `GET` | `/transactions?month=&year=` | Newest first |
| `GET` | `/transactions/needs-review` | The review queue |
| `PATCH` | `/transactions/{id}/category` | Recategorize; clears the review flag |
| `DELETE` | `/transactions/{id}` | Delete one |
| `GET` | `/budget/summary?month=&year=` | Per-category spent vs limit |
| `GET` | `/budget/limits` | Current limits |
| `POST` | `/budget/set` | `{category, monthly_limit}` — 0 clears it |
| `GET` | `/stats/daily?month=&year=` | Per-day totals for the trend chart |
| `GET` | `/stats/capture-health` | Has automatic capture gone quiet? |
| `GET` | `/stats/recurring` | Monthly repeats, each paid / due / overdue |
| `GET` | `/stats/summary?period=day\|week\|month` | Period review with a comparison |
| `POST` | `/ai/scan-statement` | Screenshot of a transaction list -> rows (books nothing) |
| `POST` | `/transactions/screenshot-import` | `{rows: [...]}` — books a screenshot as one batch |
| `GET` | `/imports` | Recent screenshot imports |
| `DELETE` | `/imports/{batch}` | Undo one import, and only its own rows |
| `GET` | `/payees` | Every remembered "who is this?" answer |
| `PATCH` | `/payees/{key}` | Correct one; `apply_to_past` re-files what it decided |
| `DELETE` | `/payees/{key}` | Forget one (future transactions ask again) |
| `GET` | `/export/all` | The whole database as JSON, for backups |
| `GET` | `/categories` | The category list |

Interactive docs at `https://<your-app>.up.railway.app/docs`.

### Ingest responses

`/sms/ingest` always returns 200 so your phone never retries a message it shouldn't:

| `status` | Meaning |
|---|---|
| `ok` | Stored |
| `ignored` | OTP / promo / reminder — deliberately not stored |
| `duplicate` | Identical SMS within 120s; returns the existing row |

---

## Backups

`.github/workflows/backup.yml` snapshots the database nightly (02:00 IST) and
stores it as an encrypted workflow artifact, kept 90 days.

**The encryption is not optional.** This repository is public, and workflow
artifacts on a public repo can be downloaded by anyone who can see the run — an
unencrypted artifact would publish every amount, merchant, and person lent to.
The workflow checks for the passphrase *before* it fetches anything, so a
missing secret can never leave plaintext sitting on the runner, and it fails
rather than falling back to storing something readable.

Two repository secrets are required, under
**Settings → Secrets and variables → Actions**:

| Secret | What |
|---|---|
| `APP_AUTH_TOKEN` | The same bearer token the app signs in with |
| `BACKUP_PASSPHRASE` | Any long random string — **keep a copy somewhere other than GitHub** |

A backup you cannot decrypt is not a backup, and one nobody has restored is a
guess. To restore:

```bash
openssl enc -d -aes-256-cbc -pbkdf2 -in backup.json.enc -out backup.json

# Look before you leap - reports what would happen, writes nothing:
python backend/restore_backup.py backup.json --into "sqlite:///C:/temp/check.db" --dry-run

# Then for real. --replace is required if the target already holds data.
python backend/restore_backup.py backup.json --into "$DATABASE_URL" --replace
```

`restore_backup.py` refuses to restore on top of existing transactions without
`--replace` (it would double every figure), refuses a backup carrying no
transactions, and checks the file's stated row counts against what it actually
contains so a truncated download is caught rather than half-restored.

**Worth doing once now, not when you need it.** Restore into a throwaway SQLite
file and confirm the numbers match what the app shows.

`/export/all` deliberately omits the `gmail_auth` table: it holds a Google
OAuth refresh token, which is a credential rather than data. Re-connecting
Gmail after a restore is one visit to `/gmail/auth/start`; leaking a token that
never expires is not undoable.

---

## Notes & gotchas

- **Timestamps** are stored as naive UTC and month windows are computed in `TZ_NAME`,
  so an 11pm spend on the 31st counts in the right month.
- **Rotating the token:** change `AUTH_TOKEN` in Railway, then sign out and back in
  on the phone and update the MacroDroid header. Old tokens stop working immediately.
- **Railway free tier sleeps.** The first request after idle takes a few seconds;
  MacroDroid's default timeout usually rides it out.
- **The review badge** in the tab bar is the count of transactions awaiting a category.
- **Adding a category:** append to `CATEGORIES` in `backend/categorizer.py`. It flows
  through to validation, the picker, and the budget screen automatically.
- **Regenerating the app icon:** edit the colours in `frontend/tools/make_icons.py`
  and run `python tools/make_icons.py` (needs Pillow).

Sources for the MacroDroid syntax: [Magic text — MacroDroid Wiki](https://macrodroidforum.com/wiki/index.php/Magic_text),
[Trigger: SMS Received — MacroDroid Wiki](https://wiki.macrodroid.com/wiki/index.php/Trigger:_SMS_Received)
