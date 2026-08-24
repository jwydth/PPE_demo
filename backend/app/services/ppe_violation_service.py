from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Annotated, Iterator
from urllib.parse import urlparse

from fastapi import Depends
from sqlmodel import Session

from app.db.session import get_engine, get_session
from app.models.camera import Camera
from app.models.ppe_violation import PPEViolation, PPEViolationSubject
from app.repositories.camera_repository import CameraRepository
from app.repositories.factory_repository import FactoryRepository
from app.repositories.ppe_violation_repository import PPEViolationRepository
from app.schemas.violation import (
    PPEViolationSubjectRead,
    ViolationDetail,
    ViolationReport,
)
from app.services import ServiceNotFoundError, ServiceValidationError
from app.services.camera_identity import normalize_camera_source_key
from app.storage.evidence_storage import EvidenceStorage, get_evidence_storage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PPEViolationSubjectDTO:
    id: int
    violation_id: int
    track_id: int | None
    person_index: int | None
    missing_equipment: list[str]
    bounding_box: dict[str, float] | None
    confidence: float | None
    created_at: datetime


class PPEViolationService:
    def __init__(
        self,
        repository: Annotated[
            PPEViolationRepository,
            Depends(PPEViolationRepository),
        ],
        storage: EvidenceStorage | None = None,
        camera_repository: CameraRepository | None = None,
        factory_repository: FactoryRepository | None = None,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.camera_repository = camera_repository
        self.factory_repository = factory_repository

    def persist_violation(
        self,
        *,
        timestamp: str | datetime,
        violation_type: str,
        details: str,
        local_snapshot_path: str,
        video_name: str | None,
        frame_index: int | None,
        track_id: int | None,
        person_index: int | None,
        missing_equipment: list[str],
        bounding_box: dict[str, float] | None,
        confidence: float | None,
        camera_id: int | None = None,
    ) -> ViolationReport:
        source_key = _normalized_source_key(video_name)
        camera_id = self._resolve_camera_id(camera_id, source_key)
        storage = self._require_storage()
        stored_object = storage.upload_ppe_snapshot(local_snapshot_path)
        violation = self.repository.create(
            PPEViolation(
                camera_id=_optional_positive_id(camera_id, "camera_id"),
                source_key=source_key,
                occurred_at=_parse_timestamp(timestamp),
                violation_type=_require_text(
                    violation_type,
                    "violation_type",
                ),
                details=_require_text(details, "details"),
                snapshot_path=stored_object.object_key,
                frame_index=_optional_nonnegative(
                    frame_index,
                    "frame_index",
                ),
            )
        )
        if violation.id is None:
            raise ServiceValidationError(
                "Persisted PPE violation is missing an ID."
            )

        self.create_subject(
            violation_id=violation.id,
            track_id=track_id,
            person_index=person_index,
            missing_equipment=missing_equipment,
            bounding_box=bounding_box,
            confidence=confidence,
        )
        report = _to_report(
            violation,
            track_id=track_id,
            snapshot_url=stored_object.object_url,
        )
        _cleanup_local_snapshot(Path(local_snapshot_path))
        return report

    def create_violation(
        self,
        *,
        timestamp: str | datetime,
        violation_type: str,
        details: str,
        snapshot_url: str | None = None,
        video_name: str | None = None,
        frame_index: int | None = None,
        camera_id: int | None = None,
    ) -> ViolationReport:
        source_key = _normalized_source_key(video_name)
        camera_id = self._resolve_camera_id(camera_id, source_key)
        violation = self.repository.create(
            PPEViolation(
                camera_id=_optional_positive_id(camera_id, "camera_id"),
                source_key=source_key,
                occurred_at=_parse_timestamp(timestamp),
                violation_type=_require_text(
                    violation_type,
                    "violation_type",
                ),
                details=_require_text(details, "details"),
                snapshot_path=_to_snapshot_path(snapshot_url),
                frame_index=_optional_nonnegative(
                    frame_index,
                    "frame_index",
                ),
            )
        )
        return _to_report(violation)

    def get_violation(self, violation_id: int) -> ViolationReport:
        normalized_id = _require_positive_id(violation_id, "violation_id")
        violation = self.repository.get_by_id(normalized_id)
        if violation is None:
            raise ServiceNotFoundError(
                f"PPE violation {violation_id} was not found."
            )
        subjects = self.repository.get_subjects(normalized_id)
        track_id = subjects[0].tracker_id if subjects else None
        return _to_report(
            violation,
            track_id=track_id,
            snapshot_url=self._snapshot_url(violation.snapshot_path),
        )

    def get_violation_detail(self, violation_id: int) -> ViolationDetail:
        report = self.get_violation(violation_id)
        subjects = [
            PPEViolationSubjectRead(
                id=s.id,
                track_id=s.track_id,
                person_index=s.person_index,
                missing_equipment=s.missing_equipment,
                bounding_box=s.bounding_box,
                confidence=s.confidence,
            )
            for s in self.get_subjects(violation_id)
        ]
        return ViolationDetail(**report.model_dump(), subjects=subjects)

    def get_violations_by_ids(self, ids: list[int]) -> list[ViolationReport]:
        """Hydrate a specific set of PPE violations, for the paginated incident
        feed (which picks the ids for a page from a merged cross-table query).
        Returned in the repository's order — the caller re-orders to match the
        page's own ordering."""
        reports: list[ViolationReport] = []
        for violation in self.repository.get_by_ids(ids):
            if violation.id is None:
                raise ServiceValidationError(
                    "Persisted PPE violation is missing an ID."
                )
            subjects = self.repository.get_subjects(violation.id)
            track_id = subjects[0].tracker_id if subjects else None
            reports.append(
                _to_report(
                    violation,
                    track_id=track_id,
                    snapshot_url=self._snapshot_url(violation.snapshot_path),
                )
            )
        return reports

    def count_violations(self) -> int:
        return self.repository.count_all()

    def get_recent_violations(self, limit: int = 100) -> list[ViolationReport]:
        normalized_limit = _validate_limit(limit)
        violations = self.repository.get_recent(normalized_limit)
        reports: list[ViolationReport] = []
        for violation in violations:
            if violation.id is None:
                raise ServiceValidationError(
                    "Persisted PPE violation is missing an ID."
                )
            subjects = self.repository.get_subjects(violation.id)
            track_id = subjects[0].tracker_id if subjects else None
            reports.append(
                _to_report(
                    violation,
                    track_id=track_id,
                    snapshot_url=self._snapshot_url(
                        violation.snapshot_path
                    ),
                )
            )
        return reports

    def create_subject(
        self,
        *,
        violation_id: int,
        track_id: int | None = None,
        person_index: int | None = None,
        missing_equipment: list[str],
        bounding_box: dict[str, float] | None = None,
        confidence: float | None = None,
    ) -> PPEViolationSubjectDTO:
        normalized_id = _require_positive_id(violation_id, "violation_id")
        if self.repository.get_by_id(normalized_id) is None:
            raise ServiceNotFoundError(
                f"PPE violation {violation_id} was not found."
            )
        normalized_equipment = [
            _require_text(item, "missing_equipment item")
            for item in missing_equipment
        ]
        if not normalized_equipment:
            raise ServiceValidationError(
                "missing_equipment must contain at least one item."
            )
        normalized_confidence = _validate_confidence(confidence)

        subject = self.repository.create_subject(
            PPEViolationSubject(
                ppe_violation_id=normalized_id,
                tracker_id=track_id,
                person_index=_optional_nonnegative(
                    person_index,
                    "person_index",
                ),
                missing_equipment=normalized_equipment,
                bounding_box=bounding_box,
                confidence=normalized_confidence,
            )
        )
        return _to_subject_dto(subject)

    def get_subjects(self, violation_id: int) -> list[PPEViolationSubjectDTO]:
        normalized_id = _require_positive_id(violation_id, "violation_id")
        if self.repository.get_by_id(normalized_id) is None:
            raise ServiceNotFoundError(
                f"PPE violation {violation_id} was not found."
            )
        return [
            _to_subject_dto(subject)
            for subject in self.repository.get_subjects(normalized_id)
        ]

    def delete_violation(self, violation_id: int) -> bool:
        normalized_id = _require_positive_id(violation_id, "violation_id")
        return self.repository.delete(normalized_id)

    def delete_all_violations(self) -> int:
        return self.repository.delete_all()

    def _snapshot_url(self, snapshot_path: str | None) -> str | None:
        if snapshot_path is None:
            return None
        if self.storage is None:
            return _to_snapshot_url(snapshot_path)
        return self.storage.get_object_url(snapshot_path)

    def _resolve_camera_id(
        self,
        camera_id: int | None,
        video_name: str | None,
    ) -> int | None:
        """Resolve camera_id via lazy get-or-create from source_key.

        Priority:
        1. Explicit camera_id wins — return as-is (validation happens later).
        2. If video_name is provided, look up Camera by source_key.
           - Found   → return its id.
           - Missing → create a Camera row (using the default factory) and
             return the new id.
        3. Neither supplied → return None.
        """
        if camera_id is not None:
            return camera_id

        source_key = _optional_text(video_name)
        if source_key is None or self.camera_repository is None:
            return None

        try:
            camera = self.camera_repository.get_by_source_key(source_key)
            if camera is not None:
                return camera.id

            # Lazy-create: resolve the default factory id first.
            factory_id: int | None = None
            if self.factory_repository is not None:
                factory = self.factory_repository.get_or_create_default_factory()
                factory_id = factory.id

            if factory_id is None:
                logger.warning(
                    "Cannot create implicit camera for '%s': no factory available.",
                    source_key,
                )
                return None

            camera = self.camera_repository.create(
                Camera(
                    factory_id=factory_id,
                    name=source_key,
                    source_key=source_key,
                    source_uri=None,
                    is_active=True,
                )
            )
            logger.info(
                "Implicitly created Camera id=%s for source_key='%s'",
                camera.id,
                source_key,
            )
            return camera.id
        except Exception:
            # Never let camera resolution break violation persistence.
            logger.warning(
                "Could not resolve camera_id for source_key='%s'; storing NULL.",
                source_key,
                exc_info=True,
            )
            return None

    def _require_storage(self) -> EvidenceStorage:
        if self.storage is None:
            raise ServiceValidationError(
                "Evidence storage is required for PPE persistence."
            )
        return self.storage


def _to_report(
    violation: PPEViolation,
    *,
    track_id: int | None = None,
    snapshot_url: str | None = None,
) -> ViolationReport:
    if violation.id is None:
        raise ServiceValidationError("Persisted PPE violation is missing an ID.")
    return ViolationReport(
        id=violation.id,
        timestamp=_timestamp_iso(violation.occurred_at),
        violation_type=violation.violation_type,
        details=violation.details,
        snapshot_url=(
            snapshot_url
            if snapshot_url is not None
            else _to_snapshot_url(violation.snapshot_path)
        ),
        video_name=violation.source_key,
        frame_index=violation.frame_index,
        track_id=track_id,
    )


def _cleanup_local_snapshot(snapshot_path: Path) -> None:
    try:
        snapshot_path.unlink(missing_ok=True)
    except OSError:
        logger.warning(
            "Could not delete uploaded local PPE snapshot: %s",
            snapshot_path,
        )


def _to_subject_dto(subject: PPEViolationSubject) -> PPEViolationSubjectDTO:
    if subject.id is None:
        raise ServiceValidationError(
            "Persisted PPE violation subject is missing an ID."
        )
    return PPEViolationSubjectDTO(
        id=subject.id,
        violation_id=subject.ppe_violation_id,
        track_id=subject.tracker_id,
        person_index=subject.person_index,
        missing_equipment=list(subject.missing_equipment),
        bounding_box=subject.bounding_box,
        confidence=subject.confidence,
        created_at=subject.created_at,
    )


def _to_snapshot_path(snapshot_url: str | None) -> str | None:
    normalized = _optional_text(snapshot_url)
    if normalized is None:
        return None
    parsed_path = urlparse(normalized).path
    prefix = "/snapshots/"
    if parsed_path.startswith(prefix):
        return parsed_path[len(prefix) :]
    return normalized


def _to_snapshot_url(snapshot_path: str | None) -> str | None:
    normalized = _optional_text(snapshot_path)
    if normalized is None:
        return None
    if normalized.startswith(("http://", "https://", "/")):
        return normalized
    return f"/snapshots/{normalized}"


def _parse_timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    normalized = _require_text(value, "timestamp").replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ServiceValidationError(
            "timestamp must be a valid ISO-8601 datetime."
        ) from exc


def _timestamp_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _validate_limit(limit: int) -> int:
    if not 1 <= limit <= 500:
        raise ServiceValidationError("limit must be between 1 and 500.")
    return limit


def _validate_confidence(value: float | None) -> float | None:
    if value is not None and not 0 <= value <= 1:
        raise ServiceValidationError("confidence must be between 0 and 1.")
    return value


def _require_positive_id(value: int, field_name: str) -> int:
    if value <= 0:
        raise ServiceValidationError(f"{field_name} must be greater than zero.")
    return value


def _optional_positive_id(value: int | None, field_name: str) -> int | None:
    if value is None:
        return None
    return _require_positive_id(value, field_name)


def _optional_nonnegative(value: int | None, field_name: str) -> int | None:
    if value is not None and value < 0:
        raise ServiceValidationError(f"{field_name} must not be negative.")
    return value


def _require_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ServiceValidationError(f"{field_name} must not be empty.")
    return normalized


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalized_source_key(value: str | None) -> str | None:
    normalized = _optional_text(value)
    return normalize_camera_source_key(normalized) if normalized is not None else None


def get_ppe_violation_service(
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[EvidenceStorage, Depends(get_evidence_storage)],
) -> PPEViolationService:
    return PPEViolationService(
        PPEViolationRepository(session),
        storage,
        CameraRepository(session),
        FactoryRepository(session),
    )


@contextmanager
def open_ppe_violation_service() -> Iterator[PPEViolationService]:
    with Session(get_engine()) as session:
        yield PPEViolationService(
            PPEViolationRepository(session),
            get_evidence_storage(),
            CameraRepository(session),
            FactoryRepository(session),
        )
