from datetime import datetime, timedelta, timezone

from app.models.zone_violation import ZoneViolation
from app.repositories.zone_violation_repository import ZoneViolationRepository


def _violation(occurred_at: datetime, frame_index: int) -> ZoneViolation:
    return ZoneViolation(
        camera_id=None,
        physical_zone_id=None,
        camera_zone_view_id=None,
        zone_name="Restricted Area",
        zone_type="RESTRICTED",
        source_key="factory.mp4",
        tracker_id=7,
        occurred_at=occurred_at,
        frame_index=frame_index,
        status="OPEN",
    )


def test_zone_violation_repository_create_read_and_recent(session):
    repository = ZoneViolationRepository(session)
    now = datetime.now(timezone.utc)

    oldest = repository.create(_violation(now - timedelta(minutes=2), 10))
    middle = repository.create(_violation(now - timedelta(minutes=1), 20))
    newest = repository.create(_violation(now, 30))

    assert oldest.id is not None
    assert oldest.zone_type == "RESTRICTED"
    assert oldest.status == "OPEN"
    assert oldest.severity is None
    assert repository.get_by_id(oldest.id) == oldest
    assert repository.get_by_id(999_999) is None
    assert repository.list_all() == [newest, middle, oldest]
    assert repository.get_recent(2) == [newest, middle]

    assert repository.delete(middle.id) is True
    assert repository.get_by_id(middle.id) is None
    assert repository.delete(middle.id) is False
    assert repository.delete_all() == 2
    assert repository.list_all() == []


def test_zone_violation_repository_list_between(session):
    repository = ZoneViolationRepository(session)
    now = datetime.now(timezone.utc)

    oldest = repository.create(_violation(now - timedelta(minutes=2), 10))
    middle = repository.create(_violation(now - timedelta(minutes=1), 20))
    newest = repository.create(_violation(now, 30))

    assert repository.list_between(date_from=None, date_to=None, limit=10) == [
        newest,
        middle,
        oldest,
    ]
    assert repository.list_between(
        date_from=now - timedelta(minutes=1), date_to=None, limit=10
    ) == [newest, middle]
    assert repository.list_between(date_from=None, date_to=None, limit=1) == [newest]
