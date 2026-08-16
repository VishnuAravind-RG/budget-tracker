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

### 2 · Backend on Railway

1. [railway.app](https://railway.app) → **New Project → Deploy from GitHub repo** → pick your repo.
2. Open the service → **Settings → Root Directory** → set to `backend`.
   *(Without this, Railway builds from the repo root and won't find `requirements.txt`.)*
3. **New → Database → Add PostgreSQL.** This sets `DATABASE_URL` on the project automatically.
   If it lands on the database service but not the API service, go to the API service →
   **Variables → New Variable → Add Reference → `DATABASE_URL`**.
4. API service → **Variables**, add:

   | Variable | Value |
   |---|---|
   | `AUTH_TOKEN` | your long random string (the one you'll paste into the app) |
   | `ANTHROPIC_API_KEY` | `sk-ant-…` from [console.anthropic.com](https://console.anthropic.com) |
   | `ANTHROPIC_MODEL` | `claude-opus-5` *(optional — set `claude-haiku-4-5` for a cheaper/faster fallback)* |
   | `TZ_NAME` | `Asia/Kolkata` |
   | `CORS_ORIGINS` | leave unset for now; set it in step 4 |

   **Don't set `DATABASE_URL` by hand** — the Postgres plugin owns it.
5. **Settings → Networking → Generate Domain.** You get something like
   `https://budget-tracker-production.up.railway.app`.
6. Verify: `curl https://<your-app>.up.railway.app/health` → `{"status":"ok"}`

Tables are created on first boot; there's no migration step.

### 3 · Frontend on Vercel *(or Netlify)*

**Vercel:** [vercel.com/new](https://vercel.com/new) → import the repo →

- **Root Directory:** `frontend`
- **Framework Preset:** Vite (auto-detected)
- **Environment Variables:** `VITE_API_URL` = `https://<your-app>.up.railway.app`
  *(no trailing slash)*
- Deploy.

**Netlify:** [app.netlify.com](https://app.netlify.com) → **Add new site → Import an existing project** →

- **Base directory:** `frontend`
- Build command and publish dir come from `netlify.toml`
- **Site configuration → Environment variables:** `VITE_API_URL` = your Railway URL

`vercel.json` / `netlify.toml` already handle the SPA rewrite and stop the service
worker from being cached.

> `VITE_API_URL` is baked in at **build** time. If you change it, redeploy — editing
> the variable alone won't update the live site.

### 4 · Lock down CORS

Back in Railway → API service → Variables → set

```
CORS_ORIGINS = https://your-frontend.vercel.app
```

Railway redeploys automatically. (Auth is a header token, not a cookie, so `*` was
never a security hole — this is just tidiness.)

### 5 · Install the PWA

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
