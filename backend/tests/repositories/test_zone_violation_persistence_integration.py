from datetime import datetime, timezone
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

from app.models.camera import Camera
from app.models.zone import Zone
from app.models.zone_violation import ZoneViolation as ZoneViolationModel
from app.repositories.camera_repository import CameraRepository
from app.repositories.zone_repository import ZoneRepository
from app.repositories.zone_violation_repository import ZoneViolationRepository
from app.routers import detection, zones
from app.schemas.detection import VideoProcessingResponse, VideoSummary
from app.schemas.violation import ZoneViolation
from app.services.zone_violation_service import (
    ZoneViolationService,
    get_zone_violation_service,
)
from app.storage.evidence_storage import StorageObject


def _persisted_zone(session) -> Zone:
    camera = CameraRepository(session).create(
        Camera(name="Factory", source_key="factory.mp4")
    )
    assert camera.id is not None
    return ZoneRepository(session).create(
        Zone(
            camera_id=camera.id,
            name="Restricted Area",
            zone_type="RESTRICTED",
            dwell_threshold_seconds=0,
            ui_shape_data={"type": "polygon"},
            normalized_coordinates=[
                {"x": 0.1, "y": 0.1},
                {"x": 0.9, "y": 0.1},
                {"x": 0.9, "y": 0.9},
            ],
        )
    )


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
    zone = _persisted_zone(session)
    assert zone.id is not None
    snapshot = tmp_path / "zone.jpg"
    snapshot.write_bytes(b"image-data")
    storage = _storage()
    service = ZoneViolationService(
        ZoneViolationRepository(session),
        storage,
        ZoneRepository(session),
    )

    result = service.persist_zone_violation(
        zone_id=zone.id,
        zone_name=zone.name,
        zone_type=zone.zone_type,
        video_name="factory.mp4",
        track_id=42,
        timestamp=datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc),
        frame_index=20,
        local_snapshot_path=str(snapshot),
    )

    violation = session.exec(select(ZoneViolationModel)).one()
    assert violation.zone_id == zone.id
    assert violation.zone_name == "Restricted Area"
    assert violation.source_key == "factory.mp4"
    assert violation.tracker_id == 42
    assert violation.snapshot_path == (
        "zone-violations/2026/06/05/evidence.jpg"
    )
    assert result.snapshot_path == "http://minio/upload-url"
    storage.upload_zone_snapshot.assert_called_once_with(str(snapshot))


def test_zone_violation_get_and_delete_endpoints_preserve_shape(session):
    zone = _persisted_zone(session)
    assert zone.id is not None
    storage = _storage()
    service = ZoneViolationService(
        ZoneViolationRepository(session),
        storage,
        ZoneRepository(session),
    )
    created = service.persist_zone_violation(
        zone_id=zone.id,
        zone_name=zone.name,
        zone_type=zone.zone_type,
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
            "zone_id": zone.id,
            "zone_name": "Restricted Area",
            "zone_type": "RESTRICTED",
            "track_id": 7,
            "timestamp": "2026-06-05T12:00:00+00:00",
            "video_name": "factory.mp4",
            "frame_index": 30,
            "snapshot_path": "http://minio/read-url",
        }
    ]

    deleted = client.delete(f"/zone-violations/{created.id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"success": True}
    assert client.delete(f"/zone-violations/{created.id}").status_code == 404


def test_zone_violation_endpoint_returns_null_after_zone_deleted(session):
    zone = _persisted_zone(session)
    assert zone.id is not None
    storage = _storage()
    service = ZoneViolationService(
        ZoneViolationRepository(session),
        storage,
        ZoneRepository(session),
    )
    created = service.persist_zone_violation(
        zone_id=zone.id,
        zone_name=zone.name,
        zone_type=zone.zone_type,
        video_name="factory.mp4",
        track_id=7,
        timestamp="2026-06-05T12:00:00+00:00",
        frame_index=30,
        local_snapshot_path="ignored-by-mock.jpg",
    )
    ZoneRepository(session).delete(zone.id)

    app = FastAPI()
    app.include_router(zones.router)
    app.dependency_overrides[get_zone_violation_service] = lambda: service
    response = TestClient(app).get("/zone-violations")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": created.id,
            "zone_id": None,
            "zone_name": "Restricted Area",
            "zone_type": None,
            "track_id": 7,
            "timestamp": "2026-06-05T12:00:00+00:00",
            "video_name": "factory.mp4",
            "frame_index": 30,
            "snapshot_path": "http://minio/read-url",
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
                zone_name="Restricted Area",
                zone_type="RESTRICTED",
                track_id=7,
                timestamp="2026-06-05T12:00:00+00:00",
                video_name="factory.mp4",
                frame_index=20,
                snapshot_path="http://minio/zone-evidence",
            )
        ],
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
    assert result.json()["zone_violations"][0]["zone_name"] == (
        "Restricted Area"
    )
