import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health/db")
def database_health(
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, str]:
    try:
        session.execute(text("SELECT 1")).scalar_one()
    except SQLAlchemyError as exc:
        logger.exception("PostgreSQL health check failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL connection failed.",
        ) from exc

    return {"database": "connected"}
