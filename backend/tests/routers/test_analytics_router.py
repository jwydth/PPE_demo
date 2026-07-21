from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.camera import Camera
from app.models.physical_zone import PhysicalZone
from app.models.ppe_violation import PPEViolation
from app.models.zone_violation import ZoneViolation
from app.repositories.behavior_incident_repository import BehaviorIncidentRepository
from app.repositories.camera_repository import CameraRepository
from app.repositories.factory_repository import FactoryRepository
from app.repositories.physical_zone_repository import PhysicalZoneRepository
from app.repositories.ppe_violation_repository import PPEViolationRepository
from app.repositories.zone_violation_repository import ZoneViolationRepository
from app.routers import analytics
from app.services.analytics_service import AnalyticsService, get_analytics_service
from app.services.incident_service import (
    UnifiedIncidentService,
    get_unified_incident_service,
)


def _make_incident_service(session) -> UnifiedIncidentService:
    return UnifiedIncidentService(
        PPEViolationRepository(session),
        ZoneViolationRepository(session),
        BehaviorIncidentRepository(session),
        CameraRepository(session),
        PhysicalZoneRepository(session),
    )


def _client(session) -> TestClient:
    app = FastAPI()
    app.include_router(analytics.router)

    def _override() -> AnalyticsService:
        return AnalyticsService(
            _make_incident_service(session),
            PhysicalZoneRepository(session),
            CameraRepository(session),
            FactoryRepository(session),
        )

    app.dependency_overrides[get_analytics_service] = _override
    app.dependency_overrides[get_unified_incident_service] = lambda: _make_incident_service(
        session
    )
    return TestClient(app)


def _seed(session) -> tuple[int, int]:
    factory = FactoryRepository(session).get_or_create_default_factory()
    zone = PhysicalZoneRepository(session).create(
        PhysicalZone(factory_id=factory.id, name="Zone A", zone_type="WALKWAY")
    )
    camera = CameraRepository(session).create(
        Camera(
            factory_id=factory.id,
            name="Cam A",
            source_key="cam-a.mp4",
            home_zone_id=zone.id,
            is_active=True,
        )
    )
    now = datetime.now(timezone.utc)
    PPEViolationRepository(session).create(
        PPEViolation(
            camera_id=camera.id,
            source_key="cam-a.mp4",
            occurred_at=now,
            violation_type="missing_helmet_and_vest",
            details="two people missing PPE",
        )
    )
    ZoneViolationRepository(session).create(
        ZoneViolation(
            camera_id=camera.id,
            zone_name="Zone A",
            zone_type="RESTRICTED",
            source_key="cam-a.mp4",
            occurred_at=now,
            frame_index=1,
            severity="critical",
        )
    )
    return zone.id, camera.id


def test_analytics_summary_reconciles_with_seeded_rows(session):
    zone_id, _camera_id = _seed(session)
    client = _client(session)

    res = client.get("/analytics/summary", params={"range": "7D"})

    assert res.status_code == 200
    body = res.json()
    assert body["grand_total"] == 2
    assert body["severity_counts"]["High"] == 1  # derived PPE severity
    assert body["severity_counts"]["Critical"] == 1  # stored zone severity
    zone_totals = {z["zone_id"]: z["total"] for z in body["zone_totals"]}
    assert zone_totals[zone_id] == 2


def test_analytics_summary_zone_filter_scopes_counts(session):
    zone_id, _camera_id = _seed(session)
    client = _client(session)

    res = client.get("/analytics/summary", params={"range": "7D", "zone_id": zone_id})

    assert res.status_code == 200
    assert res.json()["grand_total"] == 2


def test_analytics_trend_has_expected_bucket_count(session):
    _seed(session)
    client = _client(session)

    res = client.get("/analytics/trend", params={"range": "24H"})

    assert res.status_code == 200
    body = res.json()
    assert body["bucket"] == "hour"
    assert len(body["points"]) == 24
    assert sum(sum(p["zone_totals"].values()) for p in body["points"]) == 2


def test_analytics_compare_week_over_week(session):
    _seed(session)
    client = _client(session)

    res = client.get("/analytics/compare", params={"mode": "week"})

    assert res.status_code == 200
    body = res.json()
    assert body["current_total"] == 2
    assert body["prior_total"] == 0
    assert len(body["points"]) == 7


def test_analytics_summary_rejects_invalid_range(session):
    client = _client(session)

    res = client.get("/analytics/summary", params={"range": "1Y"})

    assert res.status_code == 422


def test_analytics_incidents_returns_normalized_feed(session):
    zone_id, _camera_id = _seed(session)
    client = _client(session)

    res = client.get("/analytics/incidents")

    assert res.status_code == 200
    body = res.json()
    assert len(body) == 2
    categories = {row["category"] for row in body}
    assert categories == {"ppe", "zone"}
    assert all(row["zone_id"] == zone_id for row in body)
    assert all(row["zone_name"] == "Zone A" for row in body)


def test_analytics_incidents_zone_filter(session):
    zone_id, _camera_id = _seed(session)
    client = _client(session)

    res = client.get("/analytics/incidents", params={"zone_id": zone_id})

    assert res.status_code == 200
    assert len(res.json()) == 2
