from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Annotated, Iterator
from urllib.parse import urlparse

from fastapi import Depends
from sqlmodel import Session

from app.db.session import get_engine, get_session
from app.models.zone_violation import ZoneViolation as ZoneViolationModel
from app.repositories.zone_repository import ZoneRepository
from app.repositories.zone_violation_repository import ZoneViolationRepository
from app.schemas.violation import ZoneViolation
from app.services import ServiceNotFoundError, ServiceValidationError
from app.storage.evidence_storage import EvidenceStorage, get_evidence_storage


class ZoneViolationService:
    def __init__(
        self,
        repository: Annotated[
            ZoneViolationRepository,
            Depends(ZoneViolationRepository),
        ],
        storage: EvidenceStorage | None = None,
        zone_repository: ZoneRepository | None = None,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.zone_repository = zone_repository

    def persist_zone_violation(
        self,
        *,
        zone_id: int,
        zone_name: str,
        zone_type: str | None,
        video_name: str,
        track_id: int,
        timestamp: str | datetime,
        frame_index: int,
        local_snapshot_path: str,
    ) -> ZoneViolation:
        storage = self._require_storage()
        stored_object = storage.upload_zone_snapshot(local_snapshot_path)
        violation = self.repository.create(
            ZoneViolationModel(
                zone_id=_require_positive_id(zone_id, "zone_id"),
                zone_name=_require_text(zone_name, "zone_name"),
                source_key=_require_text(video_name, "video_name"),
                tracker_id=track_id,
                occurred_at=_parse_timestamp(timestamp),
                frame_index=_require_nonnegative(frame_index, "frame_index"),
                snapshot_path=stored_object.object_key,
            )
        )
        return _to_schema(
            violation,
            snapshot_url=stored_object.object_url,
            zone_type=zone_type,
        )

    def create_zone_violation(
        self,
        *,
        zone_id: int,
        zone_name: str,
        video_name: str,
        track_id: int,
        timestamp: str | datetime,
        frame_index: int,
        snapshot_url: str | None = None,
        zone_type: str | None = None,
    ) -> ZoneViolation:
        violation = self.repository.create(
            ZoneViolationModel(
                zone_id=_require_positive_id(zone_id, "zone_id"),
                zone_name=_require_text(zone_name, "zone_name"),
                source_key=_require_text(video_name, "video_name"),
                tracker_id=track_id,
                occurred_at=_parse_timestamp(timestamp),
                frame_index=_require_nonnegative(frame_index, "frame_index"),
                snapshot_path=_to_snapshot_path(snapshot_url),
            )
        )
        return _to_schema(violation, zone_type=zone_type)

    def get_zone_violation(self, violation_id: int) -> ZoneViolation:
        normalized_id = _require_positive_id(violation_id, "violation_id")
        violation = self.repository.get_by_id(normalized_id)
        if violation is None:
            raise ServiceNotFoundError(
                f"Zone violation {violation_id} was not found."
            )
        return self._to_response(violation)

    def get_recent_zone_violations(
        self,
        limit: int = 100,
    ) -> list[ZoneViolation]:
        normalized_limit = _validate_limit(limit)
        return [
            self._to_response(violation)
            for violation in self.repository.get_recent(normalized_limit)
        ]

    def delete_zone_violation(self, violation_id: int) -> bool:
        normalized_id = _require_positive_id(violation_id, "violation_id")
        return self.repository.delete(normalized_id)

    def delete_all_zone_violations(self) -> int:
        return self.repository.delete_all()

    def _to_response(self, violation: ZoneViolationModel) -> ZoneViolation:
        return _to_schema(
            violation,
            snapshot_url=self._snapshot_url(violation.snapshot_path),
            zone_type=self._zone_type(violation.zone_id),
        )

    def _snapshot_url(self, snapshot_path: str | None) -> str | None:
        if snapshot_path is None:
            return None
        if self.storage is None:
            return _to_snapshot_url(snapshot_path)
        return self.storage.get_object_url(snapshot_path)

    def _zone_type(self, zone_id: int | None) -> str | None:
        if zone_id is None or self.zone_repository is None:
            return None
        zone = self.zone_repository.get_by_id(zone_id)
        return zone.zone_type if zone is not None else None

    def _require_storage(self) -> EvidenceStorage:
        if self.storage is None:
            raise ServiceValidationError(
                "Evidence storage is required for zone violation persistence."
            )
        return self.storage


def _to_schema(
    violation: ZoneViolationModel,
    *,
    snapshot_url: str | None = None,
    zone_type: str | None = None,
) -> ZoneViolation:
    if violation.tracker_id is None:
        raise ServiceValidationError(
            "Current API responses require track_id for zone violations."
        )
    return ZoneViolation(
        id=violation.id,
        zone_id=violation.zone_id,
        zone_name=violation.zone_name,
        zone_type=zone_type,
        track_id=violation.tracker_id,
        timestamp=_timestamp_iso(violation.occurred_at),
        video_name=violation.source_key,
        frame_index=violation.frame_index,
        snapshot_path=(
            snapshot_url
            if snapshot_url is not None
            else _to_snapshot_url(violation.snapshot_path)
        ),
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


def _require_positive_id(value: int, field_name: str) -> int:
    if value <= 0:
        raise ServiceValidationError(f"{field_name} must be greater than zero.")
    return value


def _require_nonnegative(value: int, field_name: str) -> int:
    if value < 0:
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


def get_zone_violation_service(
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[EvidenceStorage, Depends(get_evidence_storage)],
) -> ZoneViolationService:
    return ZoneViolationService(
        ZoneViolationRepository(session),
        storage,
        ZoneRepository(session),
    )


@contextmanager
def open_zone_violation_service() -> Iterator[ZoneViolationService]:
    with Session(get_engine()) as session:
        yield ZoneViolationService(
            ZoneViolationRepository(session),
            get_evidence_storage(),
            ZoneRepository(session),
        )
