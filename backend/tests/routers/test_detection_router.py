from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.ppe_violation import PPEViolation, PPEViolationSubject
from app.repositories.ppe_violation_repository import PPEViolationRepository
from app.routers import detection
from app.services.ppe_violation_service import (
    PPEViolationService,
    get_ppe_violation_service,
)


def _client(session) -> TestClient:
    app = FastAPI()
    app.include_router(detection.router)
    app.dependency_overrides[get_ppe_violation_service] = lambda: PPEViolationService(
        PPEViolationRepository(session)
    )
    return TestClient(app)


def test_get_violation_detail_includes_subjects(session):
    repository = PPEViolationRepository(session)
    violation = repository.create(
        PPEViolation(
            source_key="factory.mp4",
            occurred_at=datetime.now(timezone.utc),
            violation_type="missing_helmet_and_vest",
            details="two people missing PPE",
            snapshot_path="ppe/snap.jpg",
        )
    )
    repository.create_subject(
        PPEViolationSubject(
            ppe_violation_id=violation.id,
            tracker_id=7,
            person_index=0,
            missing_equipment=["Helmet", "Vest"],
            bounding_box={"x1": 1, "y1": 2, "x2": 3, "y2": 4},
            confidence=0.91,
        )
    )
    client = _client(session)

    res = client.get(f"/violations/{violation.id}")

    assert res.status_code == 200
    body = res.json()
    assert body["id"] == violation.id
    assert body["violation_type"] == "missing_helmet_and_vest"
    assert len(body["subjects"]) == 1
    assert body["subjects"][0]["missing_equipment"] == ["Helmet", "Vest"]
    assert body["subjects"][0]["confidence"] == 0.91


def test_get_violation_detail_404_for_missing_id(session):
    client = _client(session)

    res = client.get("/violations/999999")

    assert res.status_code == 404
