import logging
from collections.abc import Generator
from functools import lru_cache

from fastapi import HTTPException, status
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, create_engine

from app.core.config import settings

logger = logging.getLogger(__name__)


def _normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    return database_url


@lru_cache
def get_engine() -> Engine:
    database_url = (settings.DATABASE_URL or "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")

    url = _normalize_database_url(database_url)
    connect_args: dict[str, object] = {}
    if url.startswith("postgresql"):
        # libpq blocks in connect() indefinitely by default. Every DB-backed
        # endpoint is `async def` calling sync SQLAlchemy, so that block lands
        # on the asyncio event loop thread and freezes the whole server —
        # including already-running camera WebSocket streams. Failing fast
        # turns an unrecoverable hang into a 503.
        connect_args["connect_timeout"] = 5

    return create_engine(url, pool_pre_ping=True, connect_args=connect_args)


def get_session() -> Generator[Session, None, None]:
    try:
        engine = get_engine()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except (ImportError, SQLAlchemyError) as exc:
        logger.exception("PostgreSQL engine initialization failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL configuration is invalid.",
        ) from exc

    with Session(engine) as session:
        yield session
