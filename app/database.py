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

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
