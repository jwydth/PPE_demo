import json
from typing import Annotated, Any

from fastapi import Depends

from app.models.camera import Camera
from app.models.zone import Zone as ZoneModel
from app.repositories.camera_repository import CameraRepository
from app.repositories.zone_repository import ZoneRepository
from app.schemas.zone import Zone
from app.services import ServiceNotFoundError, ServiceValidationError


class ZoneService:
    def __init__(
        self,
        repository: Annotated[ZoneRepository, Depends(ZoneRepository)],
        camera_repository: Annotated[
            CameraRepository,
            Depends(CameraRepository),
        ],
    ) -> None:
        self.repository = repository
        self.camera_repository = camera_repository

    def create_zone(self, zone: Zone) -> Zone:
        camera = self._get_camera_by_video_name(zone.video_name)
        if camera.id is None:
            raise ServiceValidationError("Persisted camera is missing an ID.")

        created = self.repository.create(
            ZoneModel(
                camera_id=camera.id,
                name=_require_text(zone.zone_name, "zone_name"),
                zone_type=zone.zone_type,
                dwell_threshold_seconds=_validate_dwell(
                    zone.dwell_threshold_seconds
                ),
                is_active=zone.is_active,
                ui_shape_data=_parse_json_object(
                    zone.ui_shape_data,
                    "ui_shape_data",
                ),
                normalized_coordinates=_parse_coordinate_list(
                    zone.flattened_coordinates
                ),
            )
        )
        return _to_schema(created, camera.source_key)

    def update_zone(self, zone_id: int, zone: Zone) -> Zone:
        persisted = self._get_zone_model(zone_id)
        camera = self._get_camera_by_video_name(zone.video_name)
        if camera.id is None:
            raise ServiceValidationError("Persisted camera is missing an ID.")

        persisted.camera_id = camera.id
        persisted.name = _require_text(zone.zone_name, "zone_name")
        persisted.zone_type = zone.zone_type
        persisted.dwell_threshold_seconds = _validate_dwell(
            zone.dwell_threshold_seconds
        )
        persisted.is_active = zone.is_active
        persisted.ui_shape_data = _parse_json_object(
            zone.ui_shape_data,
            "ui_shape_data",
        )
        persisted.normalized_coordinates = _parse_coordinate_list(
            zone.flattened_coordinates
        )
        updated = self.repository.update(persisted)
        return _to_schema(updated, camera.source_key)

    def delete_zone(self, zone_id: int) -> bool:
        normalized_id = _require_positive_id(zone_id, "zone_id")
        if not self.repository.delete(normalized_id):
            raise ServiceNotFoundError(f"Zone {zone_id} was not found.")
        return True

    def get_zone(self, zone_id: int) -> Zone:
        zone = self._get_zone_model(zone_id)
        camera = self.camera_repository.get_by_id(zone.camera_id)
        if camera is None:
            raise ServiceNotFoundError(
                f"Camera {zone.camera_id} for zone {zone_id} was not found."
            )
        return _to_schema(zone, camera.source_key)

    def get_zones_by_source_key(self, source_key: str) -> list[Zone]:
        normalized_source_key = _require_text(source_key, "video_name")
        zones = self.repository.get_by_source_key(normalized_source_key)
        return [_to_schema(zone, normalized_source_key) for zone in zones]

    def _get_zone_model(self, zone_id: int) -> ZoneModel:
        normalized_id = _require_positive_id(zone_id, "zone_id")
        zone = self.repository.get_by_id(normalized_id)
        if zone is None:
            raise ServiceNotFoundError(f"Zone {zone_id} was not found.")
        return zone

    def _get_camera_by_video_name(self, video_name: str) -> Camera:
        source_key = _require_text(video_name, "video_name")
        camera = self.camera_repository.get_by_source_key(source_key)
        if camera is None:
            raise ServiceNotFoundError(
                f"Camera for video_name '{source_key}' was not found."
            )
        return camera


def _to_schema(zone: ZoneModel, source_key: str) -> Zone:
    return Zone(
        id=zone.id,
        video_name=source_key,
        zone_name=zone.name,
        zone_type=zone.zone_type,
        dwell_threshold_seconds=zone.dwell_threshold_seconds,
        is_active=zone.is_active,
        ui_shape_data=json.dumps(zone.ui_shape_data, separators=(",", ":")),
        flattened_coordinates=json.dumps(
            zone.normalized_coordinates,
            separators=(",", ":"),
        ),
    )


def _parse_json_object(value: str, field_name: str) -> dict[str, Any]:
    parsed = _parse_json(value, field_name)
    if not isinstance(parsed, dict):
        raise ServiceValidationError(f"{field_name} must contain a JSON object.")
    return parsed


def _parse_coordinate_list(value: str) -> list[dict[str, float]]:
    parsed = _parse_json(value, "flattened_coordinates")
    if not isinstance(parsed, list) or not parsed:
        raise ServiceValidationError(
            "flattened_coordinates must contain a non-empty JSON array."
        )

    coordinates: list[dict[str, float]] = []
    for point in parsed:
        if not isinstance(point, dict) or "x" not in point or "y" not in point:
            raise ServiceValidationError(
                "Each flattened coordinate must contain x and y."
            )
        try:
            x = float(point["x"])
            y = float(point["y"])
        except (TypeError, ValueError) as exc:
            raise ServiceValidationError(
                "Coordinate x and y values must be numeric."
            ) from exc
        coordinates.append({"x": x, "y": y})
    return coordinates


def _parse_json(value: str, field_name: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ServiceValidationError(
            f"{field_name} must contain valid JSON."
        ) from exc


def _validate_dwell(value: int) -> int:
    if value < 0:
        raise ServiceValidationError(
            "dwell_threshold_seconds must not be negative."
        )
    return value


def _require_positive_id(value: int, field_name: str) -> int:
    if value <= 0:
        raise ServiceValidationError(f"{field_name} must be greater than zero.")
    return value


def _require_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ServiceValidationError(f"{field_name} must not be empty.")
    return normalized
