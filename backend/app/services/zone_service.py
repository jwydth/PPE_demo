# This module must not import from `app.services.ppe` (or `app.services.ppe_detector`)
# — cross-feature logic belongs in `app.services.video_pipeline`, the only module
# allowed to import from both `app.services.ppe` and `app.services.zone_service`.

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import numpy as np
from fastapi import Depends
from sqlmodel import Session

from app.db.session import get_engine, get_session
from app.models.camera import Camera
from app.models.camera_zone_view import CameraZoneView
from app.models.physical_zone import PhysicalZone
from app.repositories.camera_repository import CameraRepository
from app.repositories.camera_zone_view_repository import CameraZoneViewRepository
from app.repositories.factory_repository import FactoryRepository
from app.repositories.physical_zone_repository import PhysicalZoneRepository
from app.schemas.detection import PersonResult
from app.schemas.violation import ZoneViolation
from app.schemas.zone import Zone
from app.services import ServiceNotFoundError, ServiceValidationError
from app.services.camera_identity import normalize_camera_source_key
from app.services.spatial import is_point_in_polygon
from app.services.zone_violation_service import open_zone_violation_service
from app.storage.local_paths import SNAPSHOT_DIR

COORD_SCALE = 1000


class ZoneService:
    def __init__(
        self,
        physical_zone_repository: Annotated[
            PhysicalZoneRepository,
            Depends(PhysicalZoneRepository),
        ],
        camera_zone_view_repository: Annotated[
            CameraZoneViewRepository,
            Depends(CameraZoneViewRepository),
        ],
        camera_repository: Annotated[
            CameraRepository,
            Depends(CameraRepository),
        ],
        factory_repository: Annotated[
            FactoryRepository,
            Depends(FactoryRepository),
        ],
    ) -> None:
        self.physical_zone_repository = physical_zone_repository
        self.camera_zone_view_repository = camera_zone_view_repository
        self.camera_repository = camera_repository
        self.factory_repository = factory_repository

    def create_zone(self, zone: Zone) -> Zone:
        factory_id = self._get_default_factory_id()
        camera = self._get_or_create_camera(zone.video_name, factory_id=factory_id)
        if camera.id is None:
            raise ServiceValidationError("Persisted camera is missing an ID.")

        zone_name = _require_text(zone.zone_name, "zone_name")
        dwell_threshold_seconds = _validate_dwell(zone.dwell_threshold_seconds)
        ui_shape_data = _parse_json_object(
            zone.ui_shape_data,
            "ui_shape_data",
        )
        normalized_coordinates = _parse_coordinate_list(zone.flattened_coordinates)
        physical_zone = self.physical_zone_repository.create(
            PhysicalZone(
                factory_id=factory_id,
                name=zone_name,
                zone_type=zone.zone_type,
                dwell_threshold_seconds=dwell_threshold_seconds,
                is_active=zone.is_active,
            )
        )
        if physical_zone.id is None:
            raise ServiceValidationError("Persisted physical zone is missing an ID.")

        view = self.camera_zone_view_repository.create(
            CameraZoneView(
                camera_id=camera.id,
                physical_zone_id=physical_zone.id,
                ui_shape_data=ui_shape_data,
                normalized_coordinates=normalized_coordinates,
                is_active=zone.is_active,
            )
        )
        return _to_schema(view, physical_zone, camera.source_key)

    def update_zone(self, zone_id: int, zone: Zone) -> Zone:
        view = self._get_camera_zone_view_model(zone_id)
        physical_zone = self._get_physical_zone_model(view.physical_zone_id)
        factory_id = self._get_default_factory_id()
        camera = self._get_camera_by_video_name(zone.video_name)
        if camera.id is None:
            raise ServiceValidationError("Persisted camera is missing an ID.")

        physical_zone.factory_id = factory_id
        physical_zone.name = _require_text(zone.zone_name, "zone_name")
        physical_zone.zone_type = zone.zone_type
        physical_zone.dwell_threshold_seconds = _validate_dwell(
            zone.dwell_threshold_seconds
        )
        physical_zone.is_active = zone.is_active
        view.camera_id = camera.id
        view.ui_shape_data = _parse_json_object(
            zone.ui_shape_data,
            "ui_shape_data",
        )
        view.normalized_coordinates = _parse_coordinate_list(
            zone.flattened_coordinates
        )
        view.is_active = zone.is_active
        updated_zone = self.physical_zone_repository.update(physical_zone)
        updated_view = self.camera_zone_view_repository.update(view)
        return _to_schema(updated_view, updated_zone, camera.source_key)

    def delete_zone(self, zone_id: int) -> bool:
        normalized_id = _require_positive_id(zone_id, "zone_id")
        view = self.camera_zone_view_repository.get_by_id(normalized_id)
        if view is None:
            raise ServiceNotFoundError(f"Zone {zone_id} was not found.")
        physical_zone_id = view.physical_zone_id
        if not self.camera_zone_view_repository.delete(normalized_id):
            raise ServiceNotFoundError(f"Zone {zone_id} was not found.")
        self.physical_zone_repository.delete(physical_zone_id)
        return True

    def delete_zones_by_source_key(self, source_key: str) -> int:
        normalized_source_key = _require_text(source_key, "video_name")
        camera = self.camera_repository.get_by_source_key(normalized_source_key)
        if camera is None or camera.id is None:
            return 0
        views = self.camera_zone_view_repository.get_by_camera(camera.id)
        deleted = 0
        for view in views:
            if view.id is None:
                continue
            physical_zone_id = view.physical_zone_id
            if self.camera_zone_view_repository.delete(view.id):
                deleted += 1
                self.physical_zone_repository.delete(physical_zone_id)
        return deleted

    def get_zone(self, zone_id: int) -> Zone:
        view = self._get_camera_zone_view_model(zone_id)
        physical_zone = self._get_physical_zone_model(view.physical_zone_id)
        camera = self.camera_repository.get_by_id(view.camera_id)
        if camera is None:
            raise ServiceNotFoundError(
                f"Camera {view.camera_id} for zone {zone_id} was not found."
            )
        return _to_schema(view, physical_zone, camera.source_key)

    def list_physical_zones(self) -> list[PhysicalZone]:
        factory_id = self._get_default_factory_id()
        zones = self.physical_zone_repository.get_by_factory(factory_id)
        return [z for z in zones if z.zone_type == "AREA"]

    def delete_physical_zone(self, zone_id: int) -> None:
        """Delete a named zone. Cameras pointing at it fall back to
        home_zone_id=NULL (ON DELETE SET NULL); any drawn CameraZoneViews for
        it are cascade-deleted."""
        normalized_id = _require_positive_id(zone_id, "zone_id")
        zone = self.physical_zone_repository.get_by_id(normalized_id)
        if zone is None:
            raise ServiceNotFoundError(f"Zone {zone_id} was not found.")
        self.physical_zone_repository.delete(normalized_id)

    def create_physical_zone(self, name: str) -> PhysicalZone:
        """Create a named zone for grouping cameras/analytics — no drawn shape
        required. Distinct from `create_zone`, which creates a physical zone
        together with a drawn CameraZoneView for zone-monitoring."""
        factory_id = self._get_default_factory_id()
        normalized_name = _require_text(name, "name")
        existing = self.physical_zone_repository.get_by_factory_and_name(
            factory_id, normalized_name
        )
        if existing is not None:
            raise ServiceValidationError(
                f"A zone named '{normalized_name}' already exists."
            )
        return self.physical_zone_repository.create(
            PhysicalZone(
                factory_id=factory_id,
                name=normalized_name,
                zone_type="AREA",
                is_active=True,
            )
        )

    def update_physical_zone_name(self, zone_id: int, name: str) -> PhysicalZone:
        normalized_id = _require_positive_id(zone_id, "zone_id")
        normalized_name = _require_text(name, "name")
        factory_id = self._get_default_factory_id()
        
        zone = self.physical_zone_repository.get_by_id(normalized_id)
        if zone is None:
            raise ServiceNotFoundError(f"Physical zone {zone_id} was not found.")
            
        existing = self.physical_zone_repository.get_by_factory_and_name(
            factory_id, normalized_name
        )
        if existing is not None and existing.id != normalized_id:
            raise ServiceValidationError(
                f"A zone named '{normalized_name}' already exists."
            )
            
        zone.name = normalized_name
        return self.physical_zone_repository.update(zone)

    def get_zones_by_source_key(self, source_key: str) -> list[Zone]:
        normalized_source_key = _require_text(source_key, "video_name")
        camera = self.camera_repository.get_by_source_key(normalized_source_key)
        if camera is None or camera.id is None:
            return []
        views = self.camera_zone_view_repository.get_by_camera(camera.id)
        return [
            _to_schema(
                view,
                self._get_physical_zone_model(view.physical_zone_id),
                normalized_source_key,
            )
            for view in views
        ]

    def _get_camera_zone_view_model(self, zone_id: int) -> CameraZoneView:
        normalized_id = _require_positive_id(zone_id, "zone_id")
        view = self.camera_zone_view_repository.get_by_id(normalized_id)
        if view is None:
            raise ServiceNotFoundError(f"Zone {zone_id} was not found.")
        return view

    def _get_physical_zone_model(self, physical_zone_id: int) -> PhysicalZone:
        zone = self.physical_zone_repository.get_by_id(physical_zone_id)
        if zone is None:
            raise ServiceNotFoundError(
                f"Physical zone {physical_zone_id} was not found."
            )
        return zone

    def _get_camera_by_video_name(self, video_name: str) -> Camera:
        source_key = normalize_camera_source_key(
            _require_text(video_name, "video_name")
        )
        camera = self.camera_repository.get_by_source_key(source_key)
        if camera is None:
            raise ServiceNotFoundError(
                f"Camera for video_name '{source_key}' was not found."
            )
        return camera

    def _get_or_create_camera(
        self,
        video_name: str,
        *,
        factory_id: int | None = None,
    ) -> Camera:
        # Normalized to the same identity the violation services write
        # (zone_violation_service / ppe_violation_service /
        # behavior_incident_service all call this). Matching on the raw string
        # meant saving a zone for rtsp://localhost:8554/stream2 did not find the
        # existing camera at rtsp://127.0.0.1:8554/stream2, so this quietly
        # minted a second camera row for the same physical stream — inflating
        # the camera count and splitting that stream's incidents across two ids.
        source_key = normalize_camera_source_key(
            _require_text(video_name, "video_name")
        )
        camera = self.camera_repository.get_by_source_key(source_key)
        if camera is not None:
            return camera
        if factory_id is None:
            factory_id = self._get_default_factory_id()
        return self.camera_repository.create(
            Camera(
                factory_id=factory_id,
                name=source_key,
                source_key=source_key,
                source_uri=None,
                is_active=True,
            )
        )

    def _get_default_factory_id(self) -> int:
        factory = self.factory_repository.get_or_create_default_factory()
        if factory.id is None:
            raise ServiceValidationError("Default factory is missing an ID.")
        return factory.id


def get_zone_service(
    session: Annotated[Session, Depends(get_session)],
) -> ZoneService:
    return ZoneService(
        PhysicalZoneRepository(session),
        CameraZoneViewRepository(session),
        CameraRepository(session),
        FactoryRepository(session),
    )


@dataclass
class ZoneViolationRecord:
    """Camera-specific zone metadata used during detection."""

    camera_zone_view_id: int
    physical_zone_id: int
    zone_name: str
    zone_type: str
    poly: list[tuple[float, float]]
    threshold: float

    def point_in_zone(self, test_point: tuple) -> bool:
        """Check if a point is inside this zone's polygon"""
        return is_point_in_polygon(test_point, self.poly)


def load_zones(video_name: str) -> list[ZoneViolationRecord]:
    """Load active camera-zone views for a video with normalized coordinates."""
    zones: list[ZoneViolationRecord] = []
    with Session(get_engine()) as session:
        view_repository = CameraZoneViewRepository(session)
        physical_zone_repository = PhysicalZoneRepository(session)
        active_views = view_repository.get_active_by_camera_source_key(video_name)

        for view in active_views:
            if view.id is None:
                continue
            physical_zone = physical_zone_repository.get_by_id(
                view.physical_zone_id
            )
            if physical_zone is None or physical_zone.id is None:
                continue

            try:
                coords = [
                    (
                        float(point["x"]) * COORD_SCALE,
                        float(point["y"]) * COORD_SCALE,
                    )
                    for point in view.normalized_coordinates
                ]
            except (KeyError, TypeError, ValueError):
                continue

            if not coords:
                continue

            zones.append(
                ZoneViolationRecord(
                    camera_zone_view_id=view.id,
                    physical_zone_id=physical_zone.id,
                    zone_name=physical_zone.name,
                    zone_type=physical_zone.zone_type,
                    poly=coords,
                    threshold=physical_zone.dwell_threshold_seconds,
                )
            )

    return zones


def get_person_foot_point(
    person: PersonResult, frame_width: int, frame_height: int
) -> tuple:
    """Calculate normalized foot point for a person's bounding box"""
    foot_x = (person.bbox.x1 + person.bbox.x2) / 2 / frame_width
    foot_y = person.bbox.y2 / frame_height
    return (foot_x * COORD_SCALE, foot_y * COORD_SCALE)


def check_zone_incursion(
    zones: list[ZoneViolationRecord],
    test_point: tuple,
) -> list[ZoneViolationRecord]:
    """Check which zones a point is inside"""
    incursion_zones = []
    for zone in zones:
        if zone.point_in_zone(test_point):
            incursion_zones.append(zone)
    return incursion_zones


def is_point_in_ignore_zone(
    zones: list[ZoneViolationRecord],
    test_point: tuple,
) -> bool:
    """Return whether a point belongs to a camera-specific ignore zone."""
    return any(
        zone.zone_type == "IGNORE" and zone.point_in_zone(test_point)
        for zone in zones
    )


def record_zone_violation(
    worker_state,
    zone: ZoneViolationRecord,
    frame: np.ndarray,
    person: PersonResult,
    video_name: str,
    frame_index: int,
    save_snapshot_fn,
) -> ZoneViolation | None:
    """Record a zone violation for a person"""
    import logging
    _log = logging.getLogger(__name__)

    timestamp = datetime.now(timezone.utc).isoformat()
    if zone.zone_type == "WALKWAY":
        label = f"Left Walkway: {zone.zone_name}"
    elif zone.zone_type == "SLIPPERY":
        label = f"Entered Slippery Area: {zone.zone_name}"
    else:
        label = f"Entered Zone: {zone.zone_name}"

    try:
        snapshot_filename = save_snapshot_fn(
            frame=frame,
            person=person,
            missing=[label],
            video_stem=Path(video_name).stem,
            frame_index=frame_index,
            polygon=zone.poly,
            zone_type=zone.zone_type,
        )
    except Exception as exc:
        _log.error(f"[ZONE] Snapshot save failed for zone '{zone.zone_name}': {exc}", exc_info=True)
        return None

    local_snapshot_path = SNAPSHOT_DIR / snapshot_filename
    try:
        with open_zone_violation_service() as service:
            # Sentinel IDs (negative) represent virtual zones (e.g. "No Walkway
            # Defined") that have no database row.  Pass None so the DB layer
            # stores a NULL foreign key instead of rejecting a negative ID.
            db_view_id = zone.camera_zone_view_id if zone.camera_zone_view_id > 0 else None
            db_phys_id = zone.physical_zone_id if zone.physical_zone_id > 0 else None
            saved = service.persist_zone_violation(
                camera_zone_view_id=db_view_id,
                physical_zone_id=db_phys_id,
                zone_name=zone.zone_name,
                zone_type=zone.zone_type,
                track_id=person.track_id or 0,
                timestamp=timestamp,
                video_name=video_name,
                frame_index=frame_index,
                local_snapshot_path=str(local_snapshot_path),
            )
    except Exception as exc:
        _log.error(f"[ZONE] DB persist failed for zone '{zone.zone_name}': {exc}", exc_info=True)
        return None
    finally:
        local_snapshot_path.unlink(missing_ok=True)

    worker_state.reported_zones.add(zone.camera_zone_view_id)
    _log.info(f"[ZONE] Violation persisted: zone='{zone.zone_name}' type={zone.zone_type} track={person.track_id} frame={frame_index}")
    return saved


def _to_schema(
    view: CameraZoneView,
    physical_zone: PhysicalZone,
    source_key: str,
) -> Zone:
    return Zone(
        id=view.id,
        video_name=source_key,
        zone_name=physical_zone.name,
        zone_type=physical_zone.zone_type,
        dwell_threshold_seconds=physical_zone.dwell_threshold_seconds,
        is_active=view.is_active and physical_zone.is_active,
        ui_shape_data=json.dumps(view.ui_shape_data, separators=(",", ":")),
        flattened_coordinates=json.dumps(
            view.normalized_coordinates,
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


def _validate_dwell(value: float) -> float:
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
