from datetime import datetime, timedelta, timezone

from app.models.ppe_violation import PPEViolation, PPEViolationSubject
from app.repositories.ppe_violation_repository import PPEViolationRepository


def _violation(occurred_at: datetime, frame_index: int) -> PPEViolation:
    return PPEViolation(
        source_key="factory.mp4",
        occurred_at=occurred_at,
        violation_type="missing_helmet",
        details=f"Missing helmet at frame {frame_index}",
        frame_index=frame_index,
    )


def test_ppe_violation_repository_create_read_recent_and_subjects(session):
    repository = PPEViolationRepository(session)
    now = datetime.now(timezone.utc)

    oldest = repository.create(_violation(now - timedelta(minutes=2), 10))
    middle = repository.create(_violation(now - timedelta(minutes=1), 20))
    newest = repository.create(_violation(now, 30))

    assert oldest.id is not None
    assert repository.get_by_id(oldest.id) == oldest
    assert repository.get_by_id(999_999) is None
    assert repository.list_all() == [newest, middle, oldest]
    assert repository.get_recent(2) == [newest, middle]

    subject = repository.create_subject(
        PPEViolationSubject(
            ppe_violation_id=oldest.id,
            tracker_id=42,
            person_index=1,
            missing_equipment=["Helmet"],
            bounding_box={"x1": 10, "y1": 20, "x2": 50, "y2": 100},
            confidence=0.95,
        )
    )

    assert subject.id is not None
    assert repository.get_subjects(oldest.id) == [subject]
    assert repository.get_subjects(newest.id) == []


def test_ppe_violation_repository_list_between(session):
    repository = PPEViolationRepository(session)
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
    assert repository.list_between(
        date_from=None, date_to=now - timedelta(minutes=1), limit=10
    ) == [middle, oldest]
    assert repository.list_between(date_from=None, date_to=None, limit=1) == [newest]
