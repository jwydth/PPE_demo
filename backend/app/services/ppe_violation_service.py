from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Annotated, Iterator
from urllib.parse import urlparse

from fastapi import Depends
from sqlmodel import Session

from app.db.session import get_engine, get_session
from app.models.ppe_violation import PPEViolation, PPEViolationSubject
from app.repositories.ppe_violation_repository import PPEViolationRepository
from app.schemas.violation import ViolationReport
from app.services import ServiceNotFoundError, ServiceValidationError
from app.storage.evidence_storage import EvidenceStorage, get_evidence_storage


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
    ) -> None:
        self.repository = repository
        self.storage = storage

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
        storage = self._require_storage()
        stored_object = storage.upload_ppe_snapshot(local_snapshot_path)
        violation = self.repository.create(
            PPEViolation(
                camera_id=_optional_positive_id(camera_id, "camera_id"),
                source_key=_optional_text(video_name),
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
        return _to_report(
            violation,
            track_id=track_id,
            snapshot_url=stored_object.object_url,
        )

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
        violation = self.repository.create(
            PPEViolation(
                camera_id=_optional_positive_id(camera_id, "camera_id"),
                source_key=_optional_text(video_name),
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

    def _snapshot_url(self, snapshot_path: str | None) -> str | None:
        if snapshot_path is None:
            return None
        if self.storage is None:
            return _to_snapshot_url(snapshot_path)
        return self.storage.get_object_url(snapshot_path)

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


def get_ppe_violation_service(
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[EvidenceStorage, Depends(get_evidence_storage)],
) -> PPEViolationService:
    return PPEViolationService(
        PPEViolationRepository(session),
        storage,
    )


@contextmanager
def open_ppe_violation_service() -> Iterator[PPEViolationService]:
    with Session(get_engine()) as session:
        yield PPEViolationService(
            PPEViolationRepository(session),
            get_evidence_storage(),
        )
