from datetime import datetime, timedelta, timezone

from app.models.behavior_incident import (
    BehaviorEvidence,
    BehaviorEvidenceType,
    BehaviorIncident,
    BehaviorIncidentSeverity,
    BehaviorIncidentStatus,
    BehaviorIncidentSubject,
    BehaviorType,
)
from app.repositories.behavior_incident_repository import BehaviorIncidentRepository


def _incident(started_at: datetime, frame_start: int) -> BehaviorIncident:
    return BehaviorIncident(
        source_key="factory.mp4",
        behavior_type=BehaviorType.FALL_DETECTED.value,
        status=BehaviorIncidentStatus.NEW.value,
        severity=BehaviorIncidentSeverity.HIGH.value,
        confidence=0.86,
        track_id=42,
        frame_start=frame_start,
        frame_end=frame_start,
        started_at=started_at,
        details=f"Fall detected at frame {frame_start}",
        metadata_json={"model_name": "yolo26m-pose"},
    )


def test_behavior_incident_repository_create_read_recent_subjects_and_evidence(session):
    repository = BehaviorIncidentRepository(session)
    now = datetime.now(timezone.utc)

    oldest = repository.create(_incident(now - timedelta(minutes=2), 10))
    middle = repository.create(_incident(now - timedelta(minutes=1), 20))
    newest = repository.create(_incident(now, 30))

    assert oldest.id is not None
    assert repository.get_by_id(oldest.id) == oldest
    assert repository.get_by_id(999_999) is None
    assert repository.list_recent(limit=2) == [newest, middle]
    assert repository.list_recent(
        limit=10,
        behavior_type=BehaviorType.FALL_DETECTED.value,
        status=BehaviorIncidentStatus.NEW.value,
    ) == [newest, middle, oldest]

    subject = repository.create_subject(
        BehaviorIncidentSubject(
            behavior_incident_id=oldest.id,
            tracker_id=42,
            person_index=1,
            bounding_box={"x1": 10, "y1": 20, "x2": 50, "y2": 100},
            confidence=0.86,
            features={"wide_box": 0.7},
        )
    )
    evidence = repository.create_evidence(
        BehaviorEvidence(
            behavior_incident_id=oldest.id,
            evidence_type=BehaviorEvidenceType.SNAPSHOT.value,
            object_key="behavior-incidents/2026/07/06/test.jpg",
            frame_index=10,
            occurred_at=now,
        )
    )

    assert subject.id is not None
    assert evidence.id is not None
    assert repository.get_subjects(oldest.id) == [subject]
    assert repository.get_evidence(oldest.id) == [evidence]

    assert repository.list_all_evidence() == [evidence]
    assert repository.delete_all() == 3
    assert repository.list_recent(limit=10) == []
    assert repository.list_all_evidence() == []


def test_behavior_incident_repository_delete_cascades_subjects_and_evidence(session):
    repository = BehaviorIncidentRepository(session)
    now = datetime.now(timezone.utc)
    incident = repository.create(_incident(now, 10))
    repository.create_subject(
        BehaviorIncidentSubject(
            behavior_incident_id=incident.id,
            tracker_id=42,
            person_index=1,
        )
    )
    repository.create_evidence(
        BehaviorEvidence(
            behavior_incident_id=incident.id,
            evidence_type=BehaviorEvidenceType.SNAPSHOT.value,
            object_key="behavior-incidents/2026/07/06/test.jpg",
            occurred_at=now,
        )
    )

    assert repository.delete(incident.id) is True
    assert repository.get_by_id(incident.id) is None
    assert repository.get_subjects(incident.id) == []
    assert repository.get_evidence(incident.id) == []
    assert repository.delete(incident.id) is False
    assert repository.delete(999_999) is False


def test_behavior_incident_repository_list_between(session):
    repository = BehaviorIncidentRepository(session)
    now = datetime.now(timezone.utc)

    oldest = repository.create(_incident(now - timedelta(minutes=2), 10))
    middle = repository.create(_incident(now - timedelta(minutes=1), 20))
    newest = repository.create(_incident(now, 30))

    assert repository.list_between(date_from=None, date_to=None, limit=10) == [
        newest,
        middle,
        oldest,
    ]
    assert repository.list_between(
        date_from=now - timedelta(minutes=1), date_to=None, limit=10
    ) == [newest, middle]
    assert repository.list_between(date_from=None, date_to=None, limit=1) == [newest]
