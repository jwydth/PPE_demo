from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Annotated, Any, Iterator

from fastapi import Depends
from sqlmodel import Session

from app.db.session import get_engine, get_session
from app.models.behavior_incident import (
    BehaviorEvidence,
    BehaviorEvidenceType,
    BehaviorIncident,
    BehaviorIncidentSeverity,
    BehaviorIncidentStatus,
    BehaviorIncidentSubject,
    BehaviorType,
)
from app.models.camera import Camera
from app.repositories.behavior_incident_repository import BehaviorIncidentRepository
from app.repositories.camera_repository import CameraRepository
from app.repositories.factory_repository import FactoryRepository
from app.schemas.fall_detection import (
    BehaviorEvidenceRead,
    BehaviorIncidentRead,
    BehaviorIncidentSubjectRead,
)
from app.services import ServiceNotFoundError, ServiceValidationError
from app.storage.evidence_storage import EvidenceStorage, get_evidence_storage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BehaviorIncidentBundle:
    incident: BehaviorIncidentRead
    object_key: str | None


class BehaviorIncidentService:
    def __init__(
        self,
        repository: Annotated[
            BehaviorIncidentRepository,
            Depends(BehaviorIncidentRepository),
        ],
        storage: EvidenceStorage | None = None,
        camera_repository: CameraRepository | None = None,
        factory_repository: FactoryRepository | None = None,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.camera_repository = camera_repository
        self.factory_repository = factory_repository

    def persist_fall_incident(
        self,
        *,
        timestamp: str | datetime,
        details: str,
        local_snapshot_path: str | Path,
        video_name: str | None,
        frame_start: int | None,
        frame_end: int | None,
        track_id: int | None,
        person_index: int | None,
        bounding_box: dict[str, float] | None,
        confidence: float | None,
        keypoints: list[Any] | None,
        features: dict[str, Any] | None,
        metadata: dict[str, Any] | None = None,
        camera_id: int | None = None,
        ended_at: str | datetime | None = None,
    ) -> BehaviorIncidentBundle:
        occurred_at = _parse_timestamp(timestamp)
        resolved_camera_id = self._resolve_camera_id(camera_id, video_name)
        storage = self._require_storage()
        stored_object = storage.upload_behavior_snapshot(local_snapshot_path)

        incident = self.repository.create(
            BehaviorIncident(
                camera_id=_optional_positive_id(resolved_camera_id, "camera_id"),
                source_key=_optional_text(video_name),
                behavior_type=BehaviorType.FALL_DETECTED.value,
                status=BehaviorIncidentStatus.NEW.value,
                severity=BehaviorIncidentSeverity.HIGH.value,
                confidence=_validate_confidence(confidence),
                track_id=track_id,
                frame_start=_optional_nonnegative(frame_start, "frame_start"),
                frame_end=_optional_nonnegative(frame_end, "frame_end"),
                started_at=occurred_at,
                ended_at=_parse_timestamp(ended_at) if ended_at is not None else None,
                details=_require_text(details, "details"),
                metadata_json=metadata or {},
            )
        )
        if incident.id is None:
            raise ServiceValidationError("Persisted behavior incident is missing an ID.")

        self.repository.create_subject(
            BehaviorIncidentSubject(
                behavior_incident_id=incident.id,
                tracker_id=track_id,
                person_index=_optional_nonnegative(person_index, "person_index"),
                bounding_box=bounding_box,
                confidence=_validate_confidence(confidence),
                keypoints=keypoints,
                features=features,
            )
        )
        self.repository.create_evidence(
            BehaviorEvidence(
                behavior_incident_id=incident.id,
                evidence_type=BehaviorEvidenceType.SNAPSHOT.value,
                object_key=stored_object.object_key,
                frame_index=_optional_nonnegative(frame_start, "frame_start"),
                occurred_at=occurred_at,
            )
        )
        _cleanup_local_snapshot(Path(local_snapshot_path))
        return BehaviorIncidentBundle(
            incident=self._to_read(incident),
            object_key=stored_object.object_key,
        )

    def get_incident(self, incident_id: int) -> BehaviorIncidentRead:
        normalized_id = _require_positive_id(incident_id, "incident_id")
        incident = self.repository.get_by_id(normalized_id)
        if incident is None:
            raise ServiceNotFoundError(f"Behavior incident {incident_id} was not found.")
        return self._to_read(incident)

    def list_recent(
        self,
        *,
        limit: int = 100,
        behavior_type: BehaviorType | str | None = None,
        status: BehaviorIncidentStatus | str | None = None,
        camera_id: int | None = None,
    ) -> list[BehaviorIncidentRead]:
        normalized_limit = _validate_limit(limit)
        behavior_value = _enum_value(behavior_type)
        status_value = _enum_value(status)
        normalized_camera_id = (
            _require_positive_id(camera_id, "camera_id")
            if camera_id is not None
            else None
        )
        incidents = self.repository.list_recent(
            limit=normalized_limit,
            behavior_type=behavior_value,
            status=status_value,
            camera_id=normalized_camera_id,
        )
        return [self._to_read(incident) for incident in incidents]

    def delete_incident(self, incident_id: int) -> bool:
        # Matches the PPE/zone single-delete precedent: no storage cleanup here
        # (delete_all_behavior_incidents does that, as a distinct bulk operation).
        normalized_id = _require_positive_id(incident_id, "incident_id")
        return self.repository.delete(normalized_id)

    def delete_all_behavior_incidents(self) -> int:
        storage = self._require_storage()
        evidence_rows = self.repository.list_all_evidence()
        for evidence in evidence_rows:
            try:
                storage.delete_object(evidence.object_key)
            except Exception:
                logger.warning(
                    "Could not delete behavior evidence object '%s'.",
                    evidence.object_key,
                    exc_info=True,
                )
        return self.repository.delete_all()

    def _to_read(self, incident: BehaviorIncident) -> BehaviorIncidentRead:
        if incident.id is None:
            raise ServiceValidationError("Persisted behavior incident is missing an ID.")

        subjects = [
            BehaviorIncidentSubjectRead(
                id=_require_positive_id(subject.id, "subject_id"),
                track_id=subject.tracker_id,
                person_index=subject.person_index,
                bounding_box=subject.bounding_box,
                confidence=subject.confidence,
                keypoints=subject.keypoints,
                features=subject.features,
            )
            for subject in self.repository.get_subjects(incident.id)
        ]
        evidence_rows = self.repository.get_evidence(incident.id)
        evidence = [
            BehaviorEvidenceRead(
                id=_require_positive_id(item.id, "evidence_id"),
                evidence_type=BehaviorEvidenceType(item.evidence_type),
                object_key=item.object_key,
                file_url=self._snapshot_url(item.object_key),
                frame_index=item.frame_index,
                timestamp=_timestamp_iso(item.occurred_at),
            )
            for item in evidence_rows
        ]
        return BehaviorIncidentRead(
            id=incident.id,
            camera_id=incident.camera_id,
            video_name=incident.source_key,
            behavior_type=BehaviorType(incident.behavior_type),
            status=BehaviorIncidentStatus(incident.status),
            severity=(
                BehaviorIncidentSeverity(incident.severity)
                if incident.severity
                else None
            ),
            confidence=incident.confidence,
            track_id=incident.track_id,
            frame_start=incident.frame_start,
            frame_end=incident.frame_end,
            timestamp=_timestamp_iso(incident.started_at),
            ended_at=_timestamp_iso(incident.ended_at) if incident.ended_at else None,
            details=incident.details,
            metadata=incident.metadata_json or {},
            snapshot_url=evidence[0].file_url if evidence else None,
            subjects=subjects,
            evidence=evidence,
        )

    def _snapshot_url(self, object_key: str | None) -> str | None:
        if object_key is None:
            return None
        if self.storage is None:
            return None
        return self.storage.get_object_url(object_key)

    def _resolve_camera_id(
        self,
        camera_id: int | None,
        video_name: str | None,
    ) -> int | None:
        if camera_id is not None:
            return camera_id

        source_key = _optional_text(video_name)
        if source_key is None or self.camera_repository is None:
            return None

        try:
            camera = self.camera_repository.get_by_source_key(source_key)
            if camera is not None:
                return camera.id

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
            logger.warning(
                "Could not resolve camera_id for source_key='%s'; storing NULL.",
                source_key,
                exc_info=True,
            )
            return None

    def _require_storage(self) -> EvidenceStorage:
        if self.storage is None:
            raise ServiceValidationError("Evidence storage is required for behavior persistence.")
        return self.storage


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return getattr(value, "value", str(value))


def _cleanup_local_snapshot(snapshot_path: Path) -> None:
    try:
        snapshot_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete uploaded local behavior snapshot: %s", snapshot_path)


def _parse_timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    normalized = _require_text(value, "timestamp").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ServiceValidationError("timestamp must be a valid ISO-8601 datetime.") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


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


def _require_positive_id(value: int | None, field_name: str) -> int:
    if value is None or value <= 0:
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


def get_behavior_incident_service(
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[EvidenceStorage, Depends(get_evidence_storage)],
) -> BehaviorIncidentService:
    return BehaviorIncidentService(
        BehaviorIncidentRepository(session),
        storage,
        CameraRepository(session),
        FactoryRepository(session),
    )


@contextmanager
def open_behavior_incident_service() -> Iterator[BehaviorIncidentService]:
    with Session(get_engine()) as session:
        yield BehaviorIncidentService(
            BehaviorIncidentRepository(session),
            get_evidence_storage(),
            CameraRepository(session),
            FactoryRepository(session),
        )
