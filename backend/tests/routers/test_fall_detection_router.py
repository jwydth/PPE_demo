from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.behavior_incident import (
    BehaviorIncident,
    BehaviorIncidentSeverity,
    BehaviorIncidentStatus,
    BehaviorType,
)
from app.repositories.behavior_incident_repository import BehaviorIncidentRepository
from app.routers import fall_detection
from app.services.behavior_incident_service import (
    BehaviorIncidentService,
    get_behavior_incident_service,
)


def _client(session) -> TestClient:
    app = FastAPI()
    app.include_router(fall_detection.router)
    app.dependency_overrides[get_behavior_incident_service] = (
        lambda: BehaviorIncidentService(BehaviorIncidentRepository(session))
    )
    return TestClient(app)


def test_delete_behavior_incident_removes_row(session):
    repository = BehaviorIncidentRepository(session)
    incident = repository.create(
        BehaviorIncident(
            source_key="factory.mp4",
            behavior_type=BehaviorType.FALL_DETECTED.value,
            status=BehaviorIncidentStatus.NEW.value,
            severity=BehaviorIncidentSeverity.HIGH.value,
            started_at=datetime.now(timezone.utc),
            details="fall detected",
        )
    )
    client = _client(session)

    res = client.delete(f"/behavior-incidents/{incident.id}")

    assert res.status_code == 200
    assert res.json() == {"success": True}
    assert repository.get_by_id(incident.id) is None


def test_delete_behavior_incident_404_for_missing_id(session):
    client = _client(session)

    res = client.delete("/behavior-incidents/999999")

    assert res.status_code == 404
