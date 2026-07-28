from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from app.models.camera import Camera
from app.models.factory import Factory
from app.models.physical_zone import PhysicalZone
from app.services import ServiceValidationError
from app.services.analytics_service import AnalyticsService
from app.services.incident_service import UnifiedIncident

# get_summary/get_trend/get_compare all compute their date range from the
# real clock (datetime.now(timezone.utc)), so tests use relative offsets from
# an equally-real "now" rather than a hardcoded date — otherwise these would
# only pass on the day they were written.
NOW = datetime.now(timezone.utc)


def _incident(
    *,
    id_: int,
    category: str,
    severity: str,
    zone_id: int | None,
    zone_name: str,
    timestamp: datetime,
    camera_id: int | None = 1,
    type_: str = "Missing Helmet",
) -> UnifiedIncident:
    return UnifiedIncident(
        id=id_,
        category=category,
        type=type_,
        severity=severity,
        timestamp=timestamp,
        camera_id=camera_id,
        zone_id=zone_id,
        zone_name=zone_name,
        camera_label=f"Camera {camera_id}",
        snapshot_url=None,
    )


def _zone_catalog() -> list[PhysicalZone]:
    return [
        PhysicalZone(id=1, factory_id=1, name="Zone A", zone_type="AREA"),
        PhysicalZone(id=2, factory_id=1, name="Zone B", zone_type="AREA"),
    ]


def _service(incidents: list[UnifiedIncident], cameras: list[Camera] | None = None) -> AnalyticsService:
    incident_service = Mock()
    incident_service.list_incidents.return_value = incidents
    physical_zone_repository = Mock()
    physical_zone_repository.get_by_factory.return_value = _zone_catalog()
    camera_repository = Mock()
    camera_repository.list_all.return_value = cameras or []
    factory_repository = Mock()
    factory_repository.get_or_create_default_factory.return_value = Factory(id=1, name="Default")

    return AnalyticsService(
        incident_service, physical_zone_repository, camera_repository, factory_repository
    )


def test_summary_zone_totals_includes_zero_count_zones():
    incidents = [
        _incident(id_=1, category="ppe", severity="High", zone_id=1, zone_name="Zone A", timestamp=NOW),
    ]
    service = _service(incidents)

    result = service.get_summary(range_="7D", zone_id=None)

    zone_ids = {z.zone_id: z.total for z in result.zone_totals}
    assert zone_ids[1] == 1
    assert zone_ids[2] == 0
    assert result.grand_total == 1


def test_summary_severity_and_type_counts_scoped_to_selected_zone_but_zone_totals_stay_global():
    incidents = [
        _incident(id_=1, category="ppe", severity="High", zone_id=1, zone_name="Zone A", timestamp=NOW),
        _incident(id_=2, category="zone", severity="Critical", zone_id=2, zone_name="Zone B", timestamp=NOW),
    ]
    service = _service(incidents)

    result = service.get_summary(range_="7D", zone_id=1)

    # grand_total/severity scoped to zone 1 only
    assert result.grand_total == 1
    assert result.severity_counts.High == 1
    assert result.severity_counts.Critical == 0
    # zone_totals always reflects the full picture, unfiltered
    zone_totals = {z.zone_id: z.total for z in result.zone_totals}
    assert zone_totals[1] == 1
    assert zone_totals[2] == 1


def test_summary_open_incidents_is_critical_plus_high():
    incidents = [
        _incident(id_=1, category="ppe", severity="Critical", zone_id=1, zone_name="Zone A", timestamp=NOW),
        _incident(id_=2, category="ppe", severity="High", zone_id=1, zone_name="Zone A", timestamp=NOW),
        _incident(id_=3, category="ppe", severity="Medium", zone_id=1, zone_name="Zone A", timestamp=NOW),
        _incident(id_=4, category="ppe", severity="Low", zone_id=1, zone_name="Zone A", timestamp=NOW),
    ]
    service = _service(incidents)

    result = service.get_summary(range_="7D", zone_id=None)

    assert result.open_incidents == 2


def test_summary_unassigned_zone_only_present_when_nonzero():
    service_with_unassigned = _service(
        [_incident(id_=1, category="ppe", severity="Low", zone_id=None, zone_name="Unassigned", timestamp=NOW)]
    )
    result = service_with_unassigned.get_summary(range_="7D", zone_id=None)
    assert any(z.zone_id is None for z in result.zone_totals)

    service_without = _service([])
    result_without = service_without.get_summary(range_="7D", zone_id=None)
    assert not any(z.zone_id is None for z in result_without.zone_totals)


def test_summary_active_cameras_counts_distinct_cameras_with_incidents_in_filtered_set():
    incidents = [
        _incident(id_=1, category="ppe", severity="Low", zone_id=1, zone_name="Zone A", timestamp=NOW, camera_id=1),
        _incident(id_=2, category="ppe", severity="Low", zone_id=1, zone_name="Zone A", timestamp=NOW, camera_id=1),
        _incident(id_=3, category="ppe", severity="Low", zone_id=1, zone_name="Zone A", timestamp=NOW, camera_id=2),
    ]
    cameras = [
        Camera(id=1, factory_id=1, name="Cam 1", source_key="c1", is_active=True),
        Camera(id=2, factory_id=1, name="Cam 2", source_key="c2", is_active=True),
        Camera(id=3, factory_id=1, name="Cam 3", source_key="c3", is_active=False),
    ]
    service = _service(incidents, cameras)

    result = service.get_summary(range_="7D", zone_id=None)

    assert result.active_cameras == 2
    assert result.total_cameras == 2  # only is_active cameras counted


def test_summary_zone_totals_include_per_zone_severity_breakdown():
    incidents = [
        _incident(id_=1, category="ppe", severity="Critical", zone_id=1, zone_name="Zone A", timestamp=NOW),
        _incident(id_=2, category="ppe", severity="Low", zone_id=1, zone_name="Zone A", timestamp=NOW),
        _incident(id_=3, category="zone", severity="High", zone_id=2, zone_name="Zone B", timestamp=NOW),
    ]
    service = _service(incidents)

    result = service.get_summary(range_="7D", zone_id=None)

    by_id = {z.zone_id: z for z in result.zone_totals}
    assert by_id[1].severity_counts.Critical == 1
    assert by_id[1].severity_counts.Low == 1
    assert by_id[1].severity_counts.High == 0
    assert by_id[2].severity_counts.High == 1
    assert by_id[2].severity_counts.Critical == 0


def test_summary_rejects_invalid_range():
    service = _service([])
    with pytest.raises(ServiceValidationError):
        service.get_summary(range_="1Y", zone_id=None)


def test_trend_24h_produces_24_hourly_buckets():
    incidents = [
        _incident(id_=1, category="ppe", severity="Low", zone_id=1, zone_name="Zone A", timestamp=NOW),
        _incident(
            id_=2,
            category="ppe",
            severity="Low",
            zone_id=1,
            zone_name="Zone A",
            timestamp=NOW - timedelta(hours=23, minutes=30),
        ),
    ]
    service = _service(incidents)

    result = service.get_trend(range_="24H", zone_id=None)

    assert result.bucket == "hour"
    assert len(result.points) == 24
    assert sum(p.zone_totals.get("1", 0) for p in result.points) == 2
    # newest incident (at "now") falls in the last bucket
    assert result.points[-1].zone_totals.get("1", 0) == 1
    # oldest incident (23.5h ago) falls in the first bucket
    assert result.points[0].zone_totals.get("1", 0) == 1


def test_trend_7d_produces_7_daily_buckets():
    service = _service([])

    result = service.get_trend(range_="7D", zone_id=None)

    assert result.bucket == "day"
    assert len(result.points) == 7


def test_trend_30d_produces_30_daily_buckets():
    service = _service([])

    result = service.get_trend(range_="30D", zone_id=None)

    assert len(result.points) == 30


def test_trend_zone_filter_restricts_points_but_not_zone_catalog():
    incidents = [
        _incident(id_=1, category="ppe", severity="Low", zone_id=1, zone_name="Zone A", timestamp=NOW),
        _incident(id_=2, category="zone", severity="Low", zone_id=2, zone_name="Zone B", timestamp=NOW),
    ]
    service = _service(incidents)

    result = service.get_trend(range_="7D", zone_id=1)

    assert sum(p.zone_totals.get("2", 0) for p in result.points) == 0
    assert sum(p.zone_totals.get("1", 0) for p in result.points) == 1


def test_compare_week_over_week_delta_and_severity_breakdown():
    incident_service = Mock()
    # get_compare calls _fetch(current_from, current_to) then
    # _fetch(prior_from, prior_to), in that exact order — drive the mock off
    # call order rather than inspecting the (real, non-frozen) timestamps.
    incident_service.list_incidents.side_effect = [
        [
            _incident(id_=1, category="ppe", severity="Critical", zone_id=1, zone_name="Zone A", timestamp=NOW),
            _incident(id_=2, category="ppe", severity="Low", zone_id=1, zone_name="Zone A", timestamp=NOW - timedelta(days=1)),
        ],
        [
            _incident(id_=3, category="ppe", severity="Low", zone_id=1, zone_name="Zone A", timestamp=NOW - timedelta(days=10)),
        ],
    ]
    physical_zone_repository = Mock()
    physical_zone_repository.get_by_factory.return_value = _zone_catalog()
    camera_repository = Mock()
    camera_repository.list_all.return_value = []
    factory_repository = Mock()
    factory_repository.get_or_create_default_factory.return_value = Factory(id=1, name="Default")
    service = AnalyticsService(
        incident_service, physical_zone_repository, camera_repository, factory_repository
    )

    result = service.get_compare(mode="week", zone_id=None)

    assert result.current_total == 2
    assert result.prior_total == 1
    assert result.delta_pct == 100.0
    assert len(result.points) == 7
    critical = next(s for s in result.severity_breakdown if s.severity == "Critical")
    assert critical.current == 1
    assert critical.prior == 0


def test_compare_rejects_invalid_mode():
    service = _service([])
    with pytest.raises(ServiceValidationError):
        service.get_compare(mode="quarter", zone_id=None)
