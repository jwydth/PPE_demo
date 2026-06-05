from datetime import datetime, timedelta, timezone

from app.models.zone_violation import ZoneViolation
from app.repositories.zone_violation_repository import ZoneViolationRepository


def _violation(occurred_at: datetime, frame_index: int) -> ZoneViolation:
    return ZoneViolation(
        zone_name="Restricted Area",
        source_key="factory.mp4",
        tracker_id=7,
        occurred_at=occurred_at,
        frame_index=frame_index,
    )


def test_zone_violation_repository_create_read_and_recent(session):
    repository = ZoneViolationRepository(session)
    now = datetime.now(timezone.utc)

    oldest = repository.create(_violation(now - timedelta(minutes=2), 10))
    middle = repository.create(_violation(now - timedelta(minutes=1), 20))
    newest = repository.create(_violation(now, 30))

    assert oldest.id is not None
    assert repository.get_by_id(oldest.id) == oldest
    assert repository.get_by_id(999_999) is None
    assert repository.list_all() == [newest, middle, oldest]
    assert repository.get_recent(2) == [newest, middle]
