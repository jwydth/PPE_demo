from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from app.models.behavior_incident import (
    BehaviorEvidence,
    BehaviorEvidenceType,
    BehaviorIncident,
    BehaviorType,
)
from app.models.camera import Camera
from app.models.physical_zone import PhysicalZone
from app.models.ppe_violation import PPEViolation
from app.models.zone_violation import ZoneViolation
from app.core.config import settings
from app.services import ServiceValidationError
from app.services.incident_service import UnifiedIncidentService

NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _camera(camera_id: int, home_zone_id: int | None) -> Camera:
    return Camera(
        id=camera_id,
        factory_id=1,
        name=f"Camera {camera_id}",
        source_key=f"cam-{camera_id}.mp4",
        home_zone_id=home_zone_id,
    )


def _zone(zone_id: int, name: str) -> PhysicalZone:
    return PhysicalZone(id=zone_id, factory_id=1, name=name, zone_type="WALKWAY")


def _ppe_violation(
    violation_id: int, occurred_at: datetime, camera_id: int | None
) -> PPEViolation:
    return PPEViolation(
        id=violation_id,
        camera_id=camera_id,
        source_key="factory.mp4",
        occurred_at=occurred_at,
        violation_type="missing_helmet_and_vest",
        details="two people missing PPE",
        snapshot_path="ppe/snap.jpg",
    )


def _zone_violation(
    violation_id: int, occurred_at: datetime, camera_id: int | None
) -> ZoneViolation:
    return ZoneViolation(
        id=violation_id,
        camera_id=camera_id,
        zone_name="Restricted Area",
        zone_type="RESTRICTED",
        source_key="factory.mp4",
        occurred_at=occurred_at,
        frame_index=1,
        severity="high",
        snapshot_path="zone/snap.jpg",
    )


def _behavior_incident(
    incident_id: int, started_at: datetime, camera_id: int | None
) -> BehaviorIncident:
    return BehaviorIncident(
        id=incident_id,
        camera_id=camera_id,
        source_key="factory.mp4",
        behavior_type=BehaviorType.FALL_DETECTED.value,
        severity="CRITICAL",
        started_at=started_at,
        details="fall detected",
    )


def _service(
    *,
    ppe=None,
    zone=None,
    behavior=None,
    cameras: dict[int, Camera] | None = None,
    zones: dict[int, PhysicalZone] | None = None,
    behavior_evidence: list[BehaviorEvidence] | None = None,
    storage=None,
) -> UnifiedIncidentService:
    ppe_repository = Mock()
    ppe_repository.list_between.return_value = ppe or []
    zone_repository = Mock()
    zone_repository.list_between.return_value = zone or []
    behavior_repository = Mock()
    behavior_repository.list_between.return_value = behavior or []
    behavior_repository.get_evidence.return_value = behavior_evidence or []
    evidence_by_incident: dict[int, list[BehaviorEvidence]] = {}
    for evidence in behavior_evidence or []:
        evidence_by_incident.setdefault(evidence.behavior_incident_id, []).append(evidence)
    behavior_repository.get_evidence_for_incidents.return_value = evidence_by_incident
    camera_repository = Mock()
    camera_repository.get_by_id.side_effect = lambda cid: (cameras or {}).get(cid)
    zone_repository_physical = Mock()
    zone_repository_physical.get_by_id.side_effect = lambda zid: (zones or {}).get(zid)

    return UnifiedIncidentService(
        ppe_repository,
        zone_repository,
        behavior_repository,
        camera_repository,
        zone_repository_physical,
        storage,
    )


def test_list_incidents_merges_and_sorts_all_three_categories():
    camera = _camera(1, home_zone_id=5)
    zone = _zone(5, "Warehouse Intake")
    service = _service(
        ppe=[_ppe_violation(1, NOW - timedelta(minutes=5), camera_id=1)],
        zone=[_zone_violation(2, NOW, camera_id=1)],
        behavior=[_behavior_incident(3, NOW - timedelta(minutes=2), camera_id=1)],
        cameras={1: camera},
        zones={5: zone},
    )

    result = service.list_incidents()

    assert [i.category for i in result] == ["zone", "behavior", "ppe"]
    assert all(i.zone_id == 5 and i.zone_name == "Warehouse Intake" for i in result)
    assert all(i.camera_label == "Camera 1" for i in result)


def test_list_incidents_unassigned_when_no_home_zone():
    service = _service(
        ppe=[_ppe_violation(1, NOW, camera_id=None)],
    )

    result = service.list_incidents()

    assert result[0].zone_id is None
    assert result[0].zone_name == "Unassigned"
    assert result[0].camera_label == "factory.mp4"


def test_list_incidents_filters_by_category_zone_and_severity():
    camera_a = _camera(1, home_zone_id=5)
    camera_b = _camera(2, home_zone_id=6)
    service = _service(
        ppe=[
            _ppe_violation(1, NOW, camera_id=1),  # zone 5, severity High
            _ppe_violation(2, NOW, camera_id=2),  # zone 6, severity High
        ],
        zone=[_zone_violation(3, NOW, camera_id=1)],  # zone 5, severity High
        cameras={1: camera_a, 2: camera_b},
        zones={5: _zone(5, "Zone A"), 6: _zone(6, "Zone B")},
    )

    only_ppe = service.list_incidents(category="ppe")
    assert {i.id for i in only_ppe} == {1, 2}

    only_zone_5 = service.list_incidents(zone_id=5)
    assert {i.id for i in only_zone_5} == {1, 3}

    only_high = service.list_incidents(severity="High")
    assert {i.id for i in only_high} == {1, 2, 3}
    assert service.list_incidents(severity="Low") == []


def test_ppe_severity_derivation_feeds_through_unified_incident():
    service = _service(ppe=[_ppe_violation(1, NOW, camera_id=None)])

    result = service.list_incidents(category="ppe")

    assert result[0].severity == "High"  # "missing_helmet_and_vest"
    assert result[0].type == "Missing Helmet and Vest"


def test_behavior_snapshot_uses_newest_evidence_via_storage():
    storage = Mock()
    storage.get_object_url.return_value = "https://minio.local/behavior/newest.jpg"
    newest = BehaviorEvidence(
        id=2,
        behavior_incident_id=1,
        evidence_type=BehaviorEvidenceType.SNAPSHOT.value,
        object_key="behavior/newest.jpg",
        occurred_at=NOW,
    )
    service = _service(
        behavior=[_behavior_incident(1, NOW, camera_id=None)],
        behavior_evidence=[newest],
        storage=storage,
    )

    result = service.list_incidents(category="behavior")

    assert result[0].snapshot_url == "https://minio.local/behavior/newest.jpg"
    storage.get_object_url.assert_called_once_with("behavior/newest.jpg")


def test_behavior_snapshot_none_without_storage():
    service = _service(
        behavior=[_behavior_incident(1, NOW, camera_id=None)],
        behavior_evidence=[],
        storage=None,
    )

    result = service.list_incidents(category="behavior")

    assert result[0].snapshot_url is None


def test_ppe_snapshot_falls_back_to_local_path_without_storage():
    service = _service(ppe=[_ppe_violation(1, NOW, camera_id=None)], storage=None)

    result = service.list_incidents(category="ppe")

    assert result[0].snapshot_url == "/snapshots/ppe/snap.jpg"


def test_list_incidents_rejects_invalid_limit():
    service = _service()
    with pytest.raises(ServiceValidationError):
        service.list_incidents(limit=0)
    with pytest.raises(ServiceValidationError):
        service.list_incidents(limit=settings.ANALYTICS_LIMIT + 1)
