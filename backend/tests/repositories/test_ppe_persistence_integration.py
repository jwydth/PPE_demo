from datetime import datetime, timezone
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

from app.models.ppe_violation import PPEViolation, PPEViolationSubject
from app.repositories.ppe_violation_repository import PPEViolationRepository
from app.routers import detection
from app.services.ppe_violation_service import (
    PPEViolationService,
    get_ppe_violation_service,
)
from app.storage.evidence_storage import StorageObject


def test_ppe_persistence_uploads_snapshot_and_creates_records(
    session,
    tmp_path,
):
    snapshot = tmp_path / "incident.jpg"
    snapshot.write_bytes(b"image-data")
    storage = Mock()
    storage.upload_ppe_snapshot.return_value = StorageObject(
        object_key="ppe-violations/2026/06/05/evidence.jpg",
        object_url=(
            "http://localhost:9000/safety-monitoring-evidence/"
            "ppe-violations/2026/06/05/evidence.jpg?signature=test"
        ),
        bucket_name="safety-monitoring-evidence",
    )
    service = PPEViolationService(
        PPEViolationRepository(session),
        storage,
    )

    report = service.persist_violation(
        timestamp=datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc),
        violation_type="missing_helmet",
        details="track 42 missing Helmet at frame 20",
        local_snapshot_path=str(snapshot),
        video_name="factory.mp4",
        frame_index=20,
        track_id=42,
        person_index=1,
        missing_equipment=["Helmet"],
        bounding_box={"x1": 10, "y1": 20, "x2": 50, "y2": 120},
        confidence=0.95,
    )

    violation = session.exec(select(PPEViolation)).one()
    subject = session.exec(select(PPEViolationSubject)).one()
    assert violation.snapshot_path == (
        "ppe-violations/2026/06/05/evidence.jpg"
    )
    assert violation.source_key == "factory.mp4"
    assert subject.ppe_violation_id == violation.id
    assert subject.tracker_id == 42
    assert subject.missing_equipment == ["Helmet"]
    assert report.model_dump() == {
        "id": violation.id,
        "timestamp": "2026-06-05T12:00:00+00:00",
        "violation_type": "missing_helmet",
        "details": "track 42 missing Helmet at frame 20",
        "snapshot_url": (
            "http://localhost:9000/safety-monitoring-evidence/"
            "ppe-violations/2026/06/05/evidence.jpg?signature=test"
        ),
        "video_name": "factory.mp4",
        "frame_index": 20,
        "track_id": 42,
    }
    storage.upload_ppe_snapshot.assert_called_once_with(str(snapshot))


def test_violations_endpoint_preserves_response_shape(session):
    repository = PPEViolationRepository(session)
    storage = Mock()
    storage.upload_ppe_snapshot.return_value = StorageObject(
        object_key="ppe-violations/2026/06/05/evidence.jpg",
        object_url="http://minio/upload-url",
        bucket_name="safety-monitoring-evidence",
    )
    storage.get_object_url.return_value = "http://minio/read-url"
    service = PPEViolationService(repository, storage)
    snapshot_path = "ignored-by-mock.jpg"
    service.persist_violation(
        timestamp="2026-06-05T12:00:00+00:00",
        violation_type="missing_vest",
        details="track 7 missing Vest at frame 30",
        local_snapshot_path=snapshot_path,
        video_name="warehouse.mp4",
        frame_index=30,
        track_id=7,
        person_index=2,
        missing_equipment=["Vest"],
        bounding_box=None,
        confidence=0.9,
    )

    app = FastAPI()
    app.include_router(detection.router)
    app.dependency_overrides[get_ppe_violation_service] = lambda: service
    response = TestClient(app).get("/violations?limit=10")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 1,
            "timestamp": "2026-06-05T12:00:00+00:00",
            "violation_type": "missing_vest",
            "details": "track 7 missing Vest at frame 30",
            "snapshot_url": "http://minio/read-url",
            "video_name": "warehouse.mp4",
            "frame_index": 30,
            "track_id": 7,
        }
    ]
