import pytest

from app.services.incident_normalization import (
    behavior_type_label,
    default_zone_severity,
    normalize_behavior_severity,
    normalize_ppe_severity,
    normalize_zone_severity,
    ppe_type_label,
    zone_type_label,
)


@pytest.mark.parametrize(
    "violation_type,expected",
    [
        ("missing_helmet_and_vest", "High"),
        ("missing_helmet_and_cleaning_coverall", "High"),
        ("missing_helmet", "Medium"),
        ("missing_vest", "Medium"),
        ("missing_role_uniform", "Medium"),
        ("ppe_violation", "Medium"),
        ("proximity_violation", "Low"),
        ("Proximity Violation", "Low"),
    ],
)
def test_normalize_ppe_severity_never_critical(violation_type, expected):
    assert normalize_ppe_severity(violation_type) == expected
    assert normalize_ppe_severity(violation_type) != "Critical"


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "Medium"),
        ("", "Medium"),
        ("high", "High"),
        ("HIGH", "High"),
        ("critical", "Critical"),
        ("not-a-real-severity", "Medium"),
    ],
)
def test_normalize_zone_severity_defaults_to_medium(raw, expected):
    assert normalize_zone_severity(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "High"),
        ("", "High"),
        ("low", "Low"),
        ("CRITICAL", "Critical"),
        ("bogus", "High"),
    ],
)
def test_normalize_behavior_severity_defaults_to_high(raw, expected):
    assert normalize_behavior_severity(raw) == expected


def test_ppe_type_label_formats_snake_case():
    assert ppe_type_label("missing_helmet_and_vest") == "Missing Helmet and Vest"
    assert ppe_type_label("missing_helmet") == "Missing Helmet"


@pytest.mark.parametrize(
    "zone_type,expected",
    [
        ("RESTRICTED", "Restricted Zone Incursion"),
        ("WALKWAY", "Left Walkway"),
        ("SLIPPERY", "Slippery Area"),
        ("unknown_zone", "Unknown Zone"),
    ],
)
def test_zone_type_label(zone_type, expected):
    assert zone_type_label(zone_type) == expected


@pytest.mark.parametrize(
    "behavior_type,expected",
    [
        ("FALL_DETECTED", "Fall Detected"),
        ("RUNNING_DETECTED", "Running Detected"),
        ("FAINT_DETECTED", "Faint Detected"),
        ("COLLAPSE_DETECTED", "Collapse Detected"),
    ],
)
def test_behavior_type_label(behavior_type, expected):
    assert behavior_type_label(behavior_type) == expected


def test_default_zone_severity_puts_restricted_incursions_in_open_incidents():
    # Open Incidents on the dashboard is Critical + High. A stored "Medium"
    # (which is what NULL normalizes to) keeps a keep-out breach off that
    # count entirely.
    assert default_zone_severity("RESTRICTED") == "High"
    assert default_zone_severity("restricted") == "High"
    assert default_zone_severity("WALKWAY") == "Medium"
    assert default_zone_severity("SLIPPERY") == "Medium"
    assert default_zone_severity(None) == "Medium"
    assert default_zone_severity("SOMETHING_NEW") == "Medium"
