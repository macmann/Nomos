from collections.abc import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from .config import get_settings

class Base(DeclarativeBase):
    pass

def _url() -> str:
    url = get_settings().database_url or "sqlite:///./nomos.db"
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://") and "+" not in url.split("://",1)[0]:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url

engine = create_engine(_url(), pool_pre_ping=True, connect_args={"check_same_thread": False} if _url().startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

def init_db() -> None:
    from . import models  # noqa
    Base.metadata.create_all(bind=engine)
    # Lightweight additive migrations for deployments without Alembic.
    with engine.begin() as conn:
        if engine.url.get_backend_name() == "sqlite":
            existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(call_transcripts)")}
            if "source" not in existing:
                conn.exec_driver_sql("ALTER TABLE call_transcripts ADD COLUMN source VARCHAR(30) DEFAULT 'stt'")
            call_state_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(call_state)")}
            if "registration_status" not in call_state_cols:
                conn.exec_driver_sql("ALTER TABLE call_state ADD COLUMN registration_status VARCHAR(60)")
            if "hold_mode" not in call_state_cols:
                conn.exec_driver_sql("ALTER TABLE call_state ADD COLUMN hold_mode BOOLEAN DEFAULT 0")
            if "last_operator_intents" not in call_state_cols:
                conn.exec_driver_sql("ALTER TABLE call_state ADD COLUMN last_operator_intents VARCHAR(255)")
        elif engine.url.get_backend_name().startswith("postgresql"):
            conn.exec_driver_sql("ALTER TABLE call_transcripts ADD COLUMN IF NOT EXISTS source VARCHAR(30) DEFAULT 'stt'")
            conn.exec_driver_sql("ALTER TABLE call_state ADD COLUMN IF NOT EXISTS registration_status VARCHAR(60)")
            conn.exec_driver_sql("ALTER TABLE call_state ADD COLUMN IF NOT EXISTS hold_mode BOOLEAN DEFAULT FALSE")
            conn.exec_driver_sql("ALTER TABLE call_state ADD COLUMN IF NOT EXISTS last_operator_intents VARCHAR(255)")

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
