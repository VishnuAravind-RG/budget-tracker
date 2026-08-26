import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from models import Base

# In production: DATABASE_URL is the Supabase pooler connection string (set on
# Render as a secret env var). Falls back to local SQLite so
# `uvicorn main:app --reload` just works with nothing configured.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./budget.db")

# Some providers (Heroku, older Railway configs) hand out "postgres://", which
# SQLAlchemy 2.x no longer accepts. Harmless no-op on Supabase's own "postgresql://".
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # pool_pre_ping survives Postgres connections dropped by an idle proxy.
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=1800)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Columns added to `transactions` after it already existed live in production.
# create_all() below only creates missing *tables* — it never alters a table
# that's already there, so a plain create_all() would leave these missing on
# the real Postgres database and every query touching them would 500.
#
# Each entry is (column name, SQL type, backfill SQL expression | None).
# `backfill` runs as `UPDATE transactions SET <col> = <backfill> WHERE <col> IS NULL`
# — for `kind` this must be direction-aware (CASE ... END), NOT a flat
# 'expense' default, or every pre-existing *credit* row (salary, refunds,
# repayments) would get silently relabelled as spending and inflate every
# historical month's total. Caught by test_migration.py before this ever
# touched real data. Kept additive-only — never remove/retype a column here.
_NEW_COLUMNS_BY_TABLE = {
    "transactions": [
        ("kind", "VARCHAR", "CASE WHEN direction = 'credit' THEN 'income' ELSE 'expense' END"),
        ("payee_key", "VARCHAR", None),
        ("counterparty", "VARCHAR", None),
        ("note", "VARCHAR", None),
        # Backfilled from created_at: for every row written before alerts were
        # date-aware, the two were the same value anyway.
        ("ingested_at", "TIMESTAMP", "created_at"),
        ("bank_ref", "VARCHAR", None),
        # Groups the rows written by one screenshot import, so the whole
        # upload can be undone as a unit. No backfill: rows written before
        # this existed genuinely belong to no batch.
        ("import_batch", "VARCHAR", None),
    ],
    # trip_km: distance since the previous fill, straight off a trip meter
    # that gets reset at every fill. No backfill — an existing row genuinely
    # has no trip reading, and inventing one would fabricate mileage. Rows
    # without it simply fall back to the odometer difference.
    "fuel_fills": [
        ("trip_km", "FLOAT", None),
    ],
}


def _ensure_columns():
    """Idempotent, additive-only ALTER TABLE for columns create_all() can't add.

    Safe to run on every startup: checks what's already there via reflection
    before issuing anything, so a column added on a previous boot is a no-op,
    not an error.
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table, columns in _NEW_COLUMNS_BY_TABLE.items():
            if table not in tables:
                continue  # brand-new database — create_all() made the final shape
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, sql_type, backfill in columns:
                if name in existing:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
                if backfill is not None:
                    # A column-level DEFAULT only applies to *future* inserts in
                    # most engines, not rows already there — backfill explicitly.
                    conn.execute(text(f"UPDATE {table} SET {name} = {backfill} WHERE {name} IS NULL"))


def _ensure_indexes():
    """A UNIQUE index on bank_ref, so the DATABASE rejects a second copy of a
    payment rather than the application trying to.

    An application-level "select, then insert if absent" cannot hold under
    concurrency, and this was not theoretical: six simultaneous copies of one
    alert all passed the existence check before any of them committed, and
    Rs 333 was booked six times. MacroDroid genuinely does fire repeatedly on
    flaky mobile data, which is the case the check existed for.

    NULL is exempt from uniqueness in both SQLite and Postgres, so alerts that
    carry no reference are unaffected and still fall back to the text-and-time
    window in _ingest().
    """
    inspector = inspect(engine)
    if "transactions" not in inspector.get_table_names():
        return
    if any(ix["name"] == "uq_transactions_bank_ref" for ix in inspector.get_indexes("transactions")):
        return

    with engine.begin() as conn:
        # Rows already stored may share a reference (that is how this was
        # found). Keep the earliest of each group and blank the others' refs
        # so the index can be built — deliberately NOT deleting anything,
        # because dropping a user's transaction rows on a startup path is
        # never worth it. The duplicates stay visible for review.
        conn.execute(text("""
            UPDATE transactions SET bank_ref = NULL
            WHERE bank_ref IS NOT NULL
              AND id NOT IN (SELECT MIN(id) FROM transactions
                             WHERE bank_ref IS NOT NULL GROUP BY bank_ref)
        """))
        conn.execute(text(
            "CREATE UNIQUE INDEX uq_transactions_bank_ref ON transactions (bank_ref)"
        ))


def _ensure_row_level_security():
    """Enable Postgres Row-Level Security on every table this app has, with
    no policies defined.

    Supabase's own advisor flagged this as CRITICAL: with RLS off, anyone who
    has this project's URL and its public anon key can read, edit, or delete
    every row in a table through Supabase's REST API — completely bypassing
    this backend's own bearer-token auth, which only guards the FastAPI
    routes, not the database underneath them.

    It went unnoticed specifically on `gmail_auth`, which holds a Google
    OAuth refresh token — a live credential, not data, and exactly what the
    advisor's "sensitive data publicly accessible" finding meant. That table
    was added after RLS was first enabled by hand on the others and simply
    never got the same treatment; a table-by-table checklist is exactly the
    kind of thing a new table quietly falls through.

    No policies are created, and none are needed: with RLS on and zero
    policies, every role is denied by default EXCEPT the table owner (and any
    role with BYPASSRLS), which is what this app's own DATABASE_URL connects
    as — confirmed by the fact that this file's own ALTER TABLE / CREATE
    INDEX migrations already succeed against it. So this only closes the
    public REST API's access, the one path the app itself never uses, while
    changing nothing about how the app talks to its own database.

    Enabling RLS on a table twice is a no-op in Postgres, not an error — so
    unlike `_ensure_columns`, this needs no reflection to check what is
    already set; it simply runs for every table, every boot. Table names come
    from the database's own catalogue, not a hand-maintained list, precisely
    so a future table can never be silently skipped the way gmail_auth was.

    Meaningless for SQLite (no RLS concept), so a no-op there — this only
    ever does anything against the real Postgres database.
    """
    if engine.dialect.name != "postgresql":
        return
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    with engine.begin() as conn:
        for table in tables:
            conn.execute(text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))


def init_db():
    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    _ensure_indexes()
    _ensure_row_level_security()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
