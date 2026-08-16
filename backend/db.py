import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base

# On Railway: the Postgres plugin sets DATABASE_URL for you.
# Falls back to local SQLite so `uvicorn main:app --reload` just works.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./budget.db")

# Railway/Heroku hand out "postgres://", which SQLAlchemy 2.x no longer accepts.
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
