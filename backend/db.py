import os

from sqlalchemy import create_engine
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


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
