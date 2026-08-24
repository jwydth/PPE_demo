"""The severity rules exist twice — as Python in `incident_normalization` (used
to label a row for display) and as SQL in `incident_filters` (used to filter
before pagination). If they ever disagree, the panel filters to one set of rows
and then badges them with another, so these tests pin them to each other rather
than to hand-written expectations.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from app.models.behavior_incident import BehaviorIncident
from app.models.ppe_violation import PPEViolation
from app.models.zone_violation import ZoneViolation
from app.services.incident_filters import (
    behavior_severity_predicate,
    ppe_severity_predicate,
    severity_filter,
    zone_severity_predicate,
)
from app.services.incident_normalization import (
    VALID_SEVERITIES,
    normalize_behavior_severity,
    normalize_ppe_severity,
    normalize_zone_severity,
)

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)

# Deliberately includes the awkward cases each rule turns on: the "and" spellings
# PPE keys High off, a proximity type that must win over them, and the NULL /
# unrecognized / wrong-case values that fall through to a category's default.
PPE_TYPES = [
    "missing_helmet",
    "missing_helmet_and_vest",
    "missing helmet and vest",
    "proximity_violation",
    "proximity_and_missing_helmet",
    "MISSING_VEST",
    "",
]
ZONE_SEVERITIES = [None, "critical", "HIGH", "Medium", "low", "  high  ", "bogus", ""]
BEHAVIOR_SEVERITIES = [None, "CRITICAL", "high", "MEDIUM", "Low", " low ", "bogus", ""]


@pytest.fixture
def populated(session: Session) -> Session:
    for index, violation_type in enumerate(PPE_TYPES):
        session.add(
            PPEViolation(
                occurred_at=BASE + timedelta(minutes=index),
                violation_type=violation_type,
                details="d",
            )
        )
    for index, severity in enumerate(ZONE_SEVERITIES):
        session.add(
            ZoneViolation(
                occurred_at=BASE + timedelta(minutes=index),
                zone_type="RESTRICTED",
                zone_name="Z",
                source_key="rtsp://cam",
                severity=severity,
                status="OPEN",
                frame_index=0,
            )
        )
    for index, severity in enumerate(BEHAVIOR_SEVERITIES):
        session.add(
            BehaviorIncident(
                started_at=BASE + timedelta(minutes=index),
                behavior_type="FALL_DETECTED",
                severity=severity,
                status="OPEN",
                details="d",
            )
        )
    session.commit()
    return session


@pytest.mark.parametrize("severity", VALID_SEVERITIES)
def test_ppe_predicate_matches_normalizer(populated: Session, severity: str) -> None:
    selected = {
        row.violation_type
        for row in populated.exec(
            select(PPEViolation).where(ppe_severity_predicate(severity))
        ).all()
    }
    expected = {t for t in PPE_TYPES if normalize_ppe_severity(t) == severity}
    assert selected == expected


@pytest.mark.parametrize("severity", VALID_SEVERITIES)
def test_zone_predicate_matches_normalizer(populated: Session, severity: str) -> None:
    selected = {
        row.severity
        for row in populated.exec(
            select(ZoneViolation).where(zone_severity_predicate(severity))
        ).all()
    }
    expected = {s for s in ZONE_SEVERITIES if normalize_zone_severity(s) == severity}
    assert selected == expected


@pytest.mark.parametrize("severity", VALID_SEVERITIES)
def test_behavior_predicate_matches_normalizer(
    populated: Session, severity: str
) -> None:
    selected = {
        row.severity
        for row in populated.exec(
            select(BehaviorIncident).where(behavior_severity_predicate(severity))
        ).all()
    }
    expected = {
        s for s in BEHAVIOR_SEVERITIES if normalize_behavior_severity(s) == severity
    }
    assert selected == expected


def test_every_row_is_selected_by_exactly_one_severity(populated: Session) -> None:
    """Severity is a partition: every stored row belongs to exactly one bucket.
    A row matching none would be invisible under any filter; one matching two
    would appear on two different filtered pages."""
    for model, predicate in (
        (PPEViolation, ppe_severity_predicate),
        (ZoneViolation, zone_severity_predicate),
        (BehaviorIncident, behavior_severity_predicate),
    ):
        total = len(populated.exec(select(model)).all())
        matched = sum(
            len(populated.exec(select(model).where(predicate(severity))).all())
            for severity in VALID_SEVERITIES
        )
        assert matched == total, model.__name__


def test_severity_filter_ors_the_requested_severities(populated: Session) -> None:
    combined = severity_filter("ppe", ["High", "Low"])
    assert combined is not None
    selected = {
        row.violation_type
        for row in populated.exec(select(PPEViolation).where(combined)).all()
    }
    expected = {
        t for t in PPE_TYPES if normalize_ppe_severity(t) in {"High", "Low"}
    }
    assert selected == expected


def test_no_severities_means_unfiltered() -> None:
    """An empty request is "show everything", not "show nothing" — the caller
    passes None through to the query rather than an always-false predicate."""
    assert severity_filter("ppe", []) is None
    assert severity_filter("zone", ["not-a-severity"]) is None


def test_ppe_is_never_critical(populated: Session) -> None:
    """normalize_ppe_severity cannot return Critical, so the predicate must
    select nothing rather than falling through to everything."""
    rows = populated.exec(
        select(PPEViolation).where(ppe_severity_predicate("Critical"))
    ).all()
    assert rows == []
