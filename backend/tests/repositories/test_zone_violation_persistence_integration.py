from datetime import datetime, timezone
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

from app.models.camera import Camera
from app.models.camera_zone_view import CameraZoneView
from app.models.physical_zone import PhysicalZone
from app.models.zone_violation import ZoneViolation as ZoneViolationModel
from app.repositories.camera_repository import CameraRepository
from app.repositories.camera_zone_view_repository import CameraZoneViewRepository
from app.repositories.factory_repository import FactoryRepository
from app.repositories.physical_zone_repository import PhysicalZoneRepository
from app.repositories.zone_violation_repository import ZoneViolationRepository
from app.routers import detection, zones
from app.schemas.detection import (
    BoundingBox,
    TrackingOverlay,
    TrackingOverlayFrame,
    VideoProcessingResponse,
    VideoSummary,
)
from app.schemas.violation import ZoneViolation
from app.services.zone_violation_service import (
    ZoneViolationService,
    get_zone_violation_service,
)
from app.storage.evidence_storage import StorageObject


def _persisted_camera_zone_view(session) -> tuple[Camera, PhysicalZone, CameraZoneView]:
    factory = FactoryRepository(session).get_or_create_default_factory()
    assert factory.id is not None
    camera = CameraRepository(session).create(
        Camera(
            factory_id=factory.id,
            name="Factory",
            source_key="factory.mp4",
        )
    )
    assert camera.id is not None
    physical_zone = PhysicalZoneRepository(session).create(
        PhysicalZone(
            factory_id=factory.id,
            name="Restricted Area",
            zone_type="RESTRICTED",
            dwell_threshold_seconds=0,
        )
    )
    assert physical_zone.id is not None
    view = CameraZoneViewRepository(session).create(
        CameraZoneView(
            camera_id=camera.id,
            physical_zone_id=physical_zone.id,
            ui_shape_data={"type": "polygon"},
            normalized_coordinates=[
                {"x": 0.1, "y": 0.1},
                {"x": 0.9, "y": 0.1},
                {"x": 0.9, "y": 0.9},
            ],
        )
    )
    assert view.id is not None
    return camera, physical_zone, view


def _storage() -> Mock:
    storage = Mock()
    storage.upload_zone_snapshot.return_value = StorageObject(
        object_key="zone-violations/2026/06/05/evidence.jpg",
        object_url="http://minio/upload-url",
        bucket_name="safety-monitoring-evidence",
    )
    storage.get_object_url.return_value = "http://minio/read-url"
    return storage


def test_zone_violation_uploads_snapshot_and_creates_postgresql_record(
    session,
    tmp_path,
):
    camera, physical_zone, view = _persisted_camera_zone_view(session)
    snapshot = tmp_path / "zone.jpg"
    snapshot.write_bytes(b"image-data")
    storage = _storage()
    service = ZoneViolationService(
        ZoneViolationRepository(session),
        storage,
        CameraZoneViewRepository(session),
        CameraRepository(session),
    )

    result = service.persist_zone_violation(
        camera_zone_view_id=view.id,
        zone_name=physical_zone.name,
        zone_type=physical_zone.zone_type,
        video_name="factory.mp4",
        track_id=42,
        timestamp=datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc),
        frame_index=20,
        local_snapshot_path=str(snapshot),
    )

    violation = session.exec(select(ZoneViolationModel)).one()
    assert violation.camera_id == camera.id
    assert violation.physical_zone_id == physical_zone.id
    assert violation.camera_zone_view_id == view.id
    assert violation.zone_name == "Restricted Area"
    assert violation.zone_type == "RESTRICTED"
    assert violation.source_key == "factory.mp4"
    assert violation.tracker_id == 42
    assert violation.status == "OPEN"
    assert violation.severity is None
    assert violation.snapshot_path == (
        "zone-violations/2026/06/05/evidence.jpg"
    )
    assert result.zone_id == view.id
    assert result.camera_id == camera.id
    assert result.physical_zone_id == physical_zone.id
    assert result.camera_zone_view_id == view.id
    assert result.snapshot_path == "http://minio/upload-url"
    storage.upload_zone_snapshot.assert_called_once_with(str(snapshot))


def test_zone_violation_get_and_delete_endpoints_preserve_shape(session):
    camera, physical_zone, view = _persisted_camera_zone_view(session)
    storage = _storage()
    service = ZoneViolationService(
        ZoneViolationRepository(session),
        storage,
        CameraZoneViewRepository(session),
        CameraRepository(session),
    )
    created = service.persist_zone_violation(
        camera_zone_view_id=view.id,
        zone_name=physical_zone.name,
        zone_type=physical_zone.zone_type,
        video_name="factory.mp4",
        track_id=7,
        timestamp="2026-06-05T12:00:00+00:00",
        frame_index=30,
        local_snapshot_path="ignored-by-mock.jpg",
    )
    assert created.id is not None

    app = FastAPI()
    app.include_router(zones.router)
    app.include_router(detection.router)
    app.dependency_overrides[get_zone_violation_service] = lambda: service
    client = TestClient(app)

    response = client.get("/zone-violations?limit=10")
    assert response.status_code == 200
    assert response.json() == [
        {
            "id": created.id,
            "zone_id": view.id,
            "camera_id": camera.id,
            "physical_zone_id": physical_zone.id,
            "camera_zone_view_id": view.id,
            "zone_name": "Restricted Area",
            "zone_type": "RESTRICTED",
            "track_id": 7,
            "timestamp": "2026-06-05T12:00:00+00:00",
            "video_name": "factory.mp4",
            "frame_index": 30,
            "snapshot_path": "http://minio/read-url",
            "status": "OPEN",
            "severity": None,
        }
    ]

    deleted = client.delete(f"/zone-violations/{created.id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"success": True}
    assert client.delete(f"/zone-violations/{created.id}").status_code == 404


def test_zone_violation_endpoint_returns_null_after_camera_zone_view_deleted(session):
    camera, physical_zone, view = _persisted_camera_zone_view(session)
    storage = _storage()
    service = ZoneViolationService(
        ZoneViolationRepository(session),
        storage,
        CameraZoneViewRepository(session),
        CameraRepository(session),
    )
    created = service.persist_zone_violation(
        camera_zone_view_id=view.id,
        zone_name=physical_zone.name,
        zone_type=physical_zone.zone_type,
        video_name="factory.mp4",
        track_id=7,
        timestamp="2026-06-05T12:00:00+00:00",
        frame_index=30,
        local_snapshot_path="ignored-by-mock.jpg",
    )
    CameraZoneViewRepository(session).delete(view.id)

    app = FastAPI()
    app.include_router(zones.router)
    app.dependency_overrides[get_zone_violation_service] = lambda: service
    response = TestClient(app).get("/zone-violations")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": created.id,
            "zone_id": None,
            "camera_id": camera.id,
            "physical_zone_id": physical_zone.id,
            "camera_zone_view_id": None,
            "zone_name": "Restricted Area",
            "zone_type": "RESTRICTED",
            "track_id": 7,
            "timestamp": "2026-06-05T12:00:00+00:00",
            "video_name": "factory.mp4",
            "frame_index": 30,
            "snapshot_path": "http://minio/read-url",
            "status": "OPEN",
            "severity": None,
        }
    ]


def test_predict_video_returns_zone_violation_response(monkeypatch, tmp_path):
    response = VideoProcessingResponse(
        summary=VideoSummary(
            video_name="factory.mp4",
            total_frames=1,
            processed_frames=1,
            fps=25,
            duration_seconds=0.04,
            unique_violations=1,
            inference_ms=1,
        ),
        reports=[],
        zone_violations=[
            ZoneViolation(
                id=1,
                zone_id=3,
                camera_zone_view_id=3,
                physical_zone_id=2,
                camera_id=1,
                zone_name="Restricted Area",
                zone_type="RESTRICTED",
                track_id=7,
                timestamp="2026-06-05T12:00:00+00:00",
                video_name="factory.mp4",
                frame_index=20,
                snapshot_path="http://minio/zone-evidence",
                status="OPEN",
            )
        ],
        tracking_overlay=TrackingOverlay(
            fps=25,
            stride=1,
            frame_width=1920,
            frame_height=1080,
            frames=[
                TrackingOverlayFrame(
                    frame_index=20,
                    time_seconds=0.8,
                    track_id=7,
                    person_id=1,
                    bbox=BoundingBox(x1=10, y1=20, x2=110, y2=220),
                    confidence=0.95,
                    compliant=False,
                    missing_equipment=["Vest"],
                    status="violation",
                )
            ],
        ),
    )
    monkeypatch.setattr(
        detection._detector,
        "process_video",
        Mock(return_value=response),
    )
    video = tmp_path / "factory.mp4"
    video.write_bytes(b"video")

    app = FastAPI()
    app.include_router(detection.router)
    with video.open("rb") as file_handle:
        result = TestClient(app).post(
            "/predict-video",
            files={"file": ("factory.mp4", file_handle, "video/mp4")},
        )

    assert result.status_code == 200
    body = result.json()
    assert {"summary", "reports", "zone_violations", "tracking_overlay"} <= body.keys()
    assert body["zone_violations"][0]["zone_name"] == (
        "Restricted Area"
    )
    overlay_frame = body["tracking_overlay"]["frames"][0]
    assert overlay_frame["frame_index"] == 20
    assert overlay_frame["time_seconds"] == 0.8
    assert overlay_frame["track_id"] == 7
    assert overlay_frame["person_id"] == 1
    assert overlay_frame["bbox"] == {
        "x1": 10.0,
        "y1": 20.0,
        "x2": 110.0,
        "y2": 220.0,
    }
