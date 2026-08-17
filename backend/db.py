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
_NEW_TRANSACTION_COLUMNS = [
    ("kind", "VARCHAR", "CASE WHEN direction = 'credit' THEN 'income' ELSE 'expense' END"),
    ("payee_key", "VARCHAR", None),
    ("counterparty", "VARCHAR", None),
]


def _ensure_columns():
    """Idempotent, additive-only ALTER TABLE for columns create_all() can't add.

    Safe to run on every startup: checks what's already there via reflection
    before issuing anything, so a column added on a previous boot is a no-op,
    not an error.
    """
    inspector = inspect(engine)
    if "transactions" not in inspector.get_table_names():
        return  # brand-new database — create_all() already made the final shape

    existing = {c["name"] for c in inspector.get_columns("transactions")}
    with engine.begin() as conn:
        for name, sql_type, backfill in _NEW_TRANSACTION_COLUMNS:
            if name in existing:
                continue
            conn.execute(text(f"ALTER TABLE transactions ADD COLUMN {name} {sql_type}"))
            if backfill is not None:
                # A column-level DEFAULT only applies to *future* inserts in
                # most engines, not rows already there — backfill explicitly.
                conn.execute(text(f"UPDATE transactions SET {name} = {backfill} WHERE {name} IS NULL"))


def init_db():
    Base.metadata.create_all(bind=engine)
    _ensure_columns()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
