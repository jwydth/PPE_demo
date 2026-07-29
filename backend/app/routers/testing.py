import logging
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.db.session import get_session
from app.services.reporting import ReportEmailError
from app.services.reporting.email_sender import SmtpEmailSender
from app.storage import StorageError
from app.storage.evidence_storage import EvidenceStorage, get_evidence_storage

router = APIRouter(tags=["testing"])
logger = logging.getLogger(__name__)


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


def _get_health_storage() -> EvidenceStorage:
    try:
        return get_evidence_storage()
    except StorageError as exc:
        logger.exception("MinIO storage configuration failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.get("/health/storage")
def storage_health(
    storage: Annotated[EvidenceStorage, Depends(_get_health_storage)],
) -> dict[str, str]:
    try:
        bucket_name = storage.ensure_ready()
    except StorageError as exc:
        logger.exception("MinIO health check failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return {
        "storage": "connected",
        "bucket": bucket_name,
    }


@router.get("/health/smtp")
def smtp_health() -> dict[str, str]:
    sender = SmtpEmailSender()
    try:
        info = sender.verify_connection()
    except ReportEmailError as exc:
        logger.warning("SMTP health check failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return {"smtp": "connected", **info}
