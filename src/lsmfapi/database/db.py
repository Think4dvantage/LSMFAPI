import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

logger = logging.getLogger(__name__)

_engine = None
SessionLocal = None


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    global _engine, SessionLocal
    import lsmfapi.database.models  # noqa: F401 — registers ORM models with Base metadata

    _engine = create_engine("sqlite:///lsmfapi.db", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=_engine)
    Base.metadata.create_all(_engine)
    _run_column_migrations()
    logger.info("Database initialised")


def check_db() -> bool:
    """Cheap connectivity check for /health — SELECT 1, no table dependency."""
    if _engine is None:
        return False
    try:
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.error("Health check: SQLite unreachable", exc_info=True)
        return False


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _run_column_migrations() -> None:
    """Add new columns to existing tables. Always idempotent — check PRAGMA table_info first."""
    with _engine.connect():
        pass  # No column migrations yet — tables are created fresh by create_all
