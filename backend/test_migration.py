"""
Simulates the exact real-world scenario: a database created by the OLD schema
(no kind/payee_key/counterparty), with real rows already in it, then boots the
NEW code against that same file and verifies nothing broke and nothing was lost.

    python test_migration.py

This is what stands between "the migration looks right" and "I'm confident
running this against the real production Postgres database."
"""
import pathlib
import sqlite3
import sys

DB_PATH = pathlib.Path(__file__).parent / "test_migration.db"
DB_PATH.unlink(missing_ok=True)

failures = []


def check(label, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {label}{'' if condition else f'  -> {detail}'}")
    if not condition:
        failures.append(label)


# --- Step 1: build the OLD schema by hand (exactly what's live in prod today) ---
conn = sqlite3.connect(DB_PATH)
conn.execute("""
    CREATE TABLE transactions (
        id INTEGER PRIMARY KEY,
        raw_text VARCHAR,
        merchant VARCHAR,
        amount FLOAT NOT NULL,
        direction VARCHAR NOT NULL,
        category VARCHAR NOT NULL DEFAULT 'Uncategorized',
        source VARCHAR NOT NULL DEFAULT 'manual',
        needs_review BOOLEAN NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL
    )
""")
conn.execute("CREATE TABLE budgets (id INTEGER PRIMARY KEY, category VARCHAR UNIQUE, monthly_limit FLOAT)")

# Real-shaped rows, as if this were actual production data.
conn.execute(
    "INSERT INTO transactions (raw_text, merchant, amount, direction, category, source, needs_review, created_at) "
    "VALUES ('Sent Rs.249 to SWIGGY', 'SWIGGY', 249.0, 'debit', 'Food & Dining', 'sms', 0, '2026-08-16 10:00:00')"
)
conn.execute(
    "INSERT INTO transactions (raw_text, merchant, amount, direction, category, source, needs_review, created_at) "
    "VALUES ('Rs.500 credited', 'Salary', 500.0, 'credit', 'Income', 'sms', 0, '2026-08-16 11:00:00')"
)
conn.execute("INSERT INTO budgets (category, monthly_limit) VALUES ('Food & Dining', 5000)")
conn.commit()

pre_migration_rows = conn.execute("SELECT id, merchant, amount FROM transactions ORDER BY id").fetchall()
conn.close()

check("seeded 2 pre-existing transactions", len(pre_migration_rows) == 2)

# --- Step 2: point the NEW code at this same file and boot it, exactly like a real deploy ---
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import os  # noqa: E402
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH.as_posix()}"

import db  # noqa: E402  (imports the NEW db.py against the OLD-shaped file)

db.init_db()

# --- Step 3: verify the migration did the right thing ---
inspector_cols = {c["name"] for c in db.inspect(db.engine).get_columns("transactions")}
check("kind column added", "kind" in inspector_cols)
check("payee_key column added", "payee_key" in inspector_cols)
check("counterparty column added", "counterparty" in inspector_cols)
check("bank_ref column added", "bank_ref" in inspector_cols)
check("ingested_at column added", "ingested_at" in inspector_cols)
check("import_batch column added", "import_batch" in inspector_cols)

with db.engine.connect() as conn:
    batches = conn.execute(db.text("SELECT import_batch FROM transactions")).fetchall()
# No backfill, deliberately: a row written before screenshot imports existed
# genuinely belongs to no batch, and inventing one would make it undoable as
# part of an import that never happened.
check("import_batch is null for pre-existing rows", all(b[0] is None for b in batches), batches)

with db.engine.connect() as conn:
    rows = conn.execute(db.text("SELECT id, merchant, amount, kind, payee_key, counterparty FROM transactions ORDER BY id")).fetchall()

check("no rows lost", len(rows) == 2, f"got {len(rows)}")
check("pre-existing amounts unchanged", [r[2] for r in rows] == [249.0, 500.0])
check("pre-existing merchants unchanged", [r[1] for r in rows] == ["SWIGGY", "Salary"])
check("existing debit row backfilled to kind='expense'", rows[0][3] == "expense", rows[0])
check(
    "existing credit row backfilled to kind='income', NOT 'expense' "
    "(a flat default here would silently relabel old salary/refunds as spending)",
    rows[1][3] == "income",
    rows[1],
)
check("payee_key is null for backfilled rows (correct — we don't know it)", rows[0][4] is None)
check("counterparty is null for backfilled rows", rows[0][5] is None)

with db.engine.connect() as conn:
    budget_limit = conn.execute(db.text("SELECT monthly_limit FROM budgets WHERE category='Food & Dining'")).scalar()
check("budgets table untouched", budget_limit == 5000)

# --- Step 4: run it a SECOND time (simulates the next deploy) — must be a clean no-op ---
db.init_db()
with db.engine.connect() as conn:
    rows2 = conn.execute(db.text("SELECT id FROM transactions")).fetchall()
check("second init_db() call is a no-op, no duplicate columns/errors", len(rows2) == 2)

# --- Step 5: new tables exist too ---
all_tables = set(db.inspect(db.engine).get_table_names())
for t in ("payees", "vehicles", "fuel_fills", "todos", "lending_reminders"):
    check(f"new table '{t}' created", t in all_tables)

db.engine.dispose()
DB_PATH.unlink(missing_ok=True)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("Migration is safe: existing data survives, new columns/tables appear, re-running is a no-op.")
