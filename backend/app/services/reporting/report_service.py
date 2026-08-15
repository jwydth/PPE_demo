"""Report evidence fetching + delivery orchestration.

fetch_snapshots() is the only I/O the PDF renderer needs but must not do
itself (see pdf_renderer.py's module docstring) — it lives here, separate
from rendering, so render_incident_report() stays pure and unit-testable.

ReportService is the orchestrator the router calls: it wires ReportDataBuilder
+ pdf_renderer + SmtpEmailSender + the report_deliveries audit trail together.
"""

import logging
import os
import re
import tempfile
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Annotated

import httpx
from fastapi import Depends
from PIL import Image as PILImage
from sqlmodel import Session

from app.core.config import settings
from app.db.session import get_session
from app.models.report_delivery import ReportDelivery
from app.repositories import RepositoryError
from app.repositories.report_delivery_repository import ReportDeliveryRepository
from app.services import ServiceValidationError
from app.services.reporting import ReportEmailError
from app.services.reporting.email_sender import (
    EmailAttachment,
    SmtpEmailSender,
    build_html_body,
    build_subject,
    build_text_body,
)
from app.services.reporting.pdf_renderer import render_incident_report
from app.services.reporting.report_data import (
    ReportData,
    ReportDataBuilder,
    ReportIncidentRow,
    get_report_data_builder,
)
from app.storage import StorageError
from app.storage.evidence_storage import EvidenceStorage, get_evidence_storage
from app.storage.local_paths import SNAPSHOT_DIR

logger = logging.getLogger(__name__)

_SNAPSHOT_URL_PREFIX = "/snapshots/"
_EVIDENCE_SEVERITIES = {"Critical", "High"}
_MAX_WIDTH_PX = 800
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def fetch_snapshots(rows: list[ReportIncidentRow], limit: int) -> dict[str, bytes]:
    """Fetches + downscales evidence images for the report appendix.

    Only Critical/High rows with a snapshot are considered, up to `limit`.
    Every failure (missing file, unreachable MinIO, corrupt image) is
    swallowed and logged — a missing thumbnail must never fail the report.
    Fetches run concurrently: each is an independent network/disk read with
    its own multi-second timeout, and doing them one at a time was the
    dominant cost of sending a report.
    """
    urls: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if len(urls) >= limit:
            break
        if row.snapshot_url is None or row.severity not in _EVIDENCE_SEVERITIES:
            continue
        if row.snapshot_url in seen:
            continue
        seen.add(row.snapshot_url)
        urls.append(row.snapshot_url)

    if not urls:
        return {}

    snapshots: dict[str, bytes] = {}
    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        for url, downscaled in zip(urls, pool.map(_fetch_and_downscale, urls)):
            if downscaled is not None:
                snapshots[url] = downscaled
    return snapshots


def _fetch_and_downscale(url: str) -> bytes | None:
    raw = _fetch_one(url)
    if raw is None:
        return None
    return _downscale(raw)


def _fetch_one(url: str) -> bytes | None:
    try:
        if url.startswith(_SNAPSHOT_URL_PREFIX):
            return _read_local(url)
        if url.startswith(("http://", "https://")):
            return _fetch_remote(url)
        logger.warning("Unrecognized snapshot URL scheme, skipping: %s", url)
        return None
    except Exception:
        logger.warning("Could not fetch report evidence snapshot %s", url, exc_info=True)
        return None


def _read_local(url: str) -> bytes | None:
    relative = url[len(_SNAPSHOT_URL_PREFIX):]
    path = (SNAPSHOT_DIR / relative).resolve()
    if not path.is_relative_to(SNAPSHOT_DIR):
        logger.warning("Snapshot path escapes SNAPSHOT_DIR, skipping: %s", url)
        return None
    if not path.is_file():
        return None
    return path.read_bytes()


def _fetch_remote(url: str) -> bytes | None:
    response = httpx.get(url, timeout=settings.REPORT_SNAPSHOT_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.content


def _downscale(raw: bytes) -> bytes | None:
    try:
        with PILImage.open(BytesIO(raw)) as im:
            im = im.convert("RGB")
            if im.width > _MAX_WIDTH_PX:
                ratio = _MAX_WIDTH_PX / im.width
                im = im.resize((_MAX_WIDTH_PX, max(1, int(im.height * ratio))))
            buf = BytesIO()
            im.save(buf, format="JPEG", quality=80)
            return buf.getvalue()
    except Exception:
        logger.warning("Could not downscale report evidence snapshot.", exc_info=True)
        return None


def validate_recipients(raw: list[str]) -> list[str]:
    """Strips/lowercases/dedupes, then enforces the policy that keeps an
    unauthenticated email endpoint from becoming an open spam relay: a
    recipient cap, address-shape validation, a header-injection guard
    (\\r/\\n reject — see plan §0.2 item 5), and the allowlist."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw:
        candidate = item.strip().lower()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        cleaned.append(candidate)

    if not cleaned:
        raise ServiceValidationError("At least one recipient is required.")
    if len(cleaned) > settings.REPORT_MAX_RECIPIENTS:
        raise ServiceValidationError(
            f"Too many recipients: {len(cleaned)} exceeds the limit of "
            f"{settings.REPORT_MAX_RECIPIENTS}."
        )
    for candidate in cleaned:
        if "\r" in candidate or "\n" in candidate:
            raise ServiceValidationError("Recipient contains invalid characters.")
        if not _EMAIL_RE.match(candidate):
            raise ServiceValidationError(f"Recipient '{candidate}' is not a valid email address.")
    _check_allowlist(cleaned)
    return cleaned


def _check_allowlist(recipients: list[str]) -> None:
    allowlist = settings.REPORT_RECIPIENT_ALLOWLIST
    if not allowlist:
        return
    for recipient in recipients:
        if not _is_allowed(recipient, allowlist):
            raise ServiceValidationError(f"Recipient {recipient} is not on the allowed list.")


def _is_allowed(recipient: str, allowlist: list[str]) -> bool:
    for entry in allowlist:
        normalized = entry.strip().lower()
        if not normalized:
            continue
        if normalized.startswith("@"):
            if recipient.endswith(normalized):
                return True
        elif recipient == normalized:
            return True
    return False


@dataclass(frozen=True)
class ReportDeliveryResult:
    recipients: list[str]
    filename: str
    size_bytes: int
    object_key: str | None
    sent_at: str


class ReportService:
    def __init__(
        self,
        report_data_builder: ReportDataBuilder,
        storage: EvidenceStorage,
        sender: SmtpEmailSender,
        delivery_repository: ReportDeliveryRepository,
    ) -> None:
        self.report_data_builder = report_data_builder
        self.storage = storage
        self.sender = sender
        self.delivery_repository = delivery_repository

    def build_pdf(
        self, *, range_: str, zone_id: int | None, include_snapshots: bool = True,
        language: str = "en",
    ) -> tuple[bytes, str]:
        _data, pdf_bytes, filename = self._assemble(
            range_=range_, zone_id=zone_id, include_snapshots=include_snapshots, language=language
        )
        return pdf_bytes, filename

    def email_report(
        self,
        *,
        range_: str,
        zone_id: int | None,
        recipients: list[str],
        subject: str | None,
        message: str | None,
        include_snapshots: bool = True,
        language: str = "en",
    ) -> ReportDeliveryResult:
        # Order matters: fail fast before generating a PDF nobody can send,
        # and before touching storage — see plan §4.4.
        self.sender.validate_configuration()
        clean_recipients = validate_recipients(recipients)

        data, pdf_bytes, filename = self._assemble(
            range_=range_, zone_id=zone_id, include_snapshots=include_snapshots, language=language
        )

        object_key = self._archive(pdf_bytes) if settings.REPORT_ARCHIVE_TO_MINIO else None
        final_subject = subject or build_subject(data)
        attachment = EmailAttachment(filename=filename, content=pdf_bytes)

        try:
            self.sender.send(
                recipients=clean_recipients,
                subject=final_subject,
                html_body=build_html_body(data, message),
                text_body=build_text_body(data, message),
                attachments=[attachment],
            )
        except ReportEmailError as exc:
            self._write_audit_row(
                range_=range_,
                zone_id=zone_id,
                recipients=clean_recipients,
                subject=final_subject,
                filename=filename,
                object_key=object_key,
                size_bytes=len(pdf_bytes),
                status="FAILED",
                error_message=str(exc),
            )
            raise

        sent_at = datetime.now(timezone.utc)
        self._write_audit_row(
            range_=range_,
            zone_id=zone_id,
            recipients=clean_recipients,
            subject=final_subject,
            filename=filename,
            object_key=object_key,
            size_bytes=len(pdf_bytes),
            status="SENT",
            error_message=None,
        )
        return ReportDeliveryResult(
            recipients=clean_recipients,
            filename=filename,
            size_bytes=len(pdf_bytes),
            object_key=object_key,
            sent_at=sent_at.isoformat(),
        )

    def _assemble(
        self, *, range_: str, zone_id: int | None, include_snapshots: bool, language: str = "en"
    ) -> tuple[ReportData, bytes, str]:
        data = self.report_data_builder.build(range_=range_, zone_id=zone_id, language=language)
        snapshots = (
            fetch_snapshots(data.top_incidents, settings.REPORT_MAX_SNAPSHOTS)
            if include_snapshots
            else None
        )
        pdf_bytes = render_incident_report(data, snapshots)
        return data, pdf_bytes, _build_filename(data)

    def _archive(self, pdf_bytes: bytes) -> str | None:
        # Archiving must never block delivery — swallow StorageError and
        # continue without an object_key (plan §4.4 step 4).
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(pdf_bytes)
                tmp_path = tmp.name
            storage_object = self.storage.upload_report(tmp_path)
            return storage_object.object_key
        except StorageError:
            logger.warning(
                "Could not archive report PDF to MinIO; continuing without archive.",
                exc_info=True,
            )
            return None
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _write_audit_row(
        self,
        *,
        range_: str,
        zone_id: int | None,
        recipients: list[str],
        subject: str,
        filename: str,
        object_key: str | None,
        size_bytes: int,
        status: str,
        error_message: str | None,
    ) -> None:
        try:
            self.delivery_repository.create(
                ReportDelivery(
                    report_type="incident_analytics",
                    range_param=range_,
                    zone_id=zone_id,
                    recipients=", ".join(recipients),
                    subject=subject,
                    filename=filename,
                    object_key=object_key,
                    size_bytes=size_bytes,
                    status=status,
                    error_message=error_message,
                )
            )
        except RepositoryError:
            logger.warning("Could not write report_deliveries audit row.", exc_info=True)


def _build_filename(data: ReportData) -> str:
    now = datetime.now(timezone.utc)
    return f"safety-report_{_slugify(data.factory_name)}_{data.range_param}_{now:%Y%m%d-%H%M}.pdf"


def _slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return slug or "factory"


def get_report_service(
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[EvidenceStorage, Depends(get_evidence_storage)],
) -> ReportService:
    report_data_builder = get_report_data_builder(session, storage)
    return ReportService(
        report_data_builder,
        storage,
        SmtpEmailSender(),
        ReportDeliveryRepository(session),
    )
