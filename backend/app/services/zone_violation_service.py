from datetime import datetime
from typing import Annotated
from urllib.parse import urlparse

from fastapi import Depends

from app.models.zone_violation import ZoneViolation as ZoneViolationModel
from app.repositories.zone_violation_repository import ZoneViolationRepository
from app.schemas.violation import ZoneViolation
from app.services import ServiceNotFoundError, ServiceValidationError


class ZoneViolationService:
    def __init__(
        self,
        repository: Annotated[
            ZoneViolationRepository,
            Depends(ZoneViolationRepository),
        ],
    ) -> None:
        self.repository = repository

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
        return _to_schema(violation)

    def get_zone_violation(self, violation_id: int) -> ZoneViolation:
        normalized_id = _require_positive_id(violation_id, "violation_id")
        violation = self.repository.get_by_id(normalized_id)
        if violation is None:
            raise ServiceNotFoundError(
                f"Zone violation {violation_id} was not found."
            )
        return _to_schema(violation)

    def get_recent_zone_violations(
        self,
        limit: int = 100,
    ) -> list[ZoneViolation]:
        normalized_limit = _validate_limit(limit)
        return [
            _to_schema(violation)
            for violation in self.repository.get_recent(normalized_limit)
        ]


def _to_schema(violation: ZoneViolationModel) -> ZoneViolation:
    if violation.zone_id is None:
        raise ServiceValidationError(
            "Current API responses require zone_id for zone violations."
        )
    if violation.tracker_id is None:
        raise ServiceValidationError(
            "Current API responses require track_id for zone violations."
        )
    return ZoneViolation(
        id=violation.id,
        zone_id=violation.zone_id,
        track_id=violation.tracker_id,
        timestamp=violation.occurred_at.isoformat(),
        video_name=violation.source_key,
        frame_index=violation.frame_index,
        snapshot_path=_to_snapshot_url(violation.snapshot_path),
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
