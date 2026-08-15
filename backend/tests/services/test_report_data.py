import re
from datetime import datetime, timezone

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
from app.services.analytics_service import AnalyticsService
from app.services.incident_service import UnifiedIncidentService
from app.services.reporting.report_data import ReportDataBuilder


def _builder(session) -> ReportDataBuilder:
    incident_service = UnifiedIncidentService(
        PPEViolationRepository(session),
        ZoneViolationRepository(session),
        BehaviorIncidentRepository(session),
        CameraRepository(session),
        PhysicalZoneRepository(session),
    )
    analytics_service = AnalyticsService(
        incident_service,
        PhysicalZoneRepository(session),
        CameraRepository(session),
        FactoryRepository(session),
    )
    return ReportDataBuilder(
        analytics_service,
        incident_service,
        FactoryRepository(session),
        PhysicalZoneRepository(session),
    )


def _seed(session) -> tuple[int, int]:
    factory = FactoryRepository(session).get_or_create_default_factory()
    zone = PhysicalZoneRepository(session).create(
        PhysicalZone(factory_id=factory.id, name="Khu vực sản xuất", zone_type="AREA")
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
            zone_name=zone.name,
            zone_type="RESTRICTED",
            source_key="cam-a.mp4",
            occurred_at=now,
            frame_index=1,
            severity="critical",
        )
    )
    return zone.id, camera.id


def test_build_sorts_top_incidents_by_severity_then_recency(session):
    _seed(session)
    builder = _builder(session)

    data = builder.build(range_="7D", zone_id=None)

    assert len(data.top_incidents) == 2
    # zone violation was stored with severity="critical" -> normalized "Critical",
    # PPE violation "missing_helmet_and_vest" normalizes to "High" — Critical must
    # rank first regardless of insertion order.
    assert data.top_incidents[0].severity == "Critical"
    assert data.top_incidents[1].severity == "High"


def test_build_formats_timestamps_in_local_time_not_isoformat(session):
    _seed(session)
    builder = _builder(session)

    data = builder.build(range_="7D", zone_id=None)

    for row in data.top_incidents:
        assert "T" not in row.timestamp_local
        assert "+00:00" not in row.timestamp_local


def test_build_zone_scope_label_defaults_to_all_zones(session):
    _seed(session)
    builder = _builder(session)

    data = builder.build(range_="7D", zone_id=None)

    assert data.zone_scope_label == "All zones"
    assert data.data_caveats == []


def test_build_zone_scope_label_and_caveat_when_zone_filtered(session):
    zone_id, _camera_id = _seed(session)
    builder = _builder(session)

    data = builder.build(range_="7D", zone_id=zone_id)

    assert data.zone_scope_label == "Khu vực sản xuất"
    assert any("site-wide" in c for c in data.data_caveats)


def test_build_renders_vietnamese_zone_name_in_range_and_scope(session):
    _seed(session)
    builder = _builder(session)

    data = builder.build(range_="7D", zone_id=None)

    assert data.factory_name
    assert data.range_param == "7D"
    assert data.timezone_label.startswith("UTC+07:00")


def test_build_empty_database_produces_zero_totals(session):
    builder = _builder(session)

    data = builder.build(range_="7D", zone_id=None)

    assert data.summary.grand_total == 0
    assert data.top_incidents == []
    assert any("No incidents recorded" in i for i in data.insights)


def test_build_vietnamese_zone_scope_label(session):
    builder = _builder(session)

    data = builder.build(range_="7D", zone_id=None, language="vi")

    assert data.zone_scope_label == "Tất cả khu vực"
    assert re.match(r"\d{2}/\d{2}/\d{4}, \d{2}:\d{2}", data.generated_at_local)
