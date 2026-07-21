from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.zone_violation import ZoneViolation as ZoneViolationModel
from app.repositories.zone_violation_repository import ZoneViolationRepository
from app.routers import zones
from app.services.zone_violation_service import (
    ZoneViolationService,
    get_zone_violation_service,
)


def _client(session) -> TestClient:
    app = FastAPI()
    app.include_router(zones.router)
    app.dependency_overrides[get_zone_violation_service] = (
        lambda: ZoneViolationService(ZoneViolationRepository(session))
    )
    return TestClient(app)


def test_get_zone_violation_detail_returns_zone_metadata(session):
    repository = ZoneViolationRepository(session)
    violation = repository.create(
        ZoneViolationModel(
            zone_name="Restricted Area",
            zone_type="RESTRICTED",
            source_key="factory.mp4",
            tracker_id=3,
            occurred_at=datetime.now(timezone.utc),
            frame_index=5,
            severity="critical",
        )
    )
    client = _client(session)

    res = client.get(f"/zone-violations/{violation.id}")

    assert res.status_code == 200
    body = res.json()
    assert body["id"] == violation.id
    assert body["zone_name"] == "Restricted Area"
    assert body["zone_type"] == "RESTRICTED"
    assert body["severity"] == "critical"


def test_get_zone_violation_detail_404_for_missing_id(session):
    client = _client(session)

    res = client.get("/zone-violations/999999")

    assert res.status_code == 404
