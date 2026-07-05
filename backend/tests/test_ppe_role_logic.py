from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import ppe_detector as ppe


PERSON = {"x1": 0, "y1": 0, "x2": 100, "y2": 200, "conf": 0.95}
HELMET = {"x1": 20, "y1": 0, "x2": 80, "y2": 40, "conf": 0.9}
VEST = {"x1": 15, "y1": 60, "x2": 85, "y2": 140, "conf": 0.88}
CLEANING_COVERALL = {"x1": 10, "y1": 50, "x2": 90, "y2": 190, "conf": 0.87}


def _person_result(
    *,
    helmets: list[dict] | None = None,
    vests: list[dict] | None = None,
    cleaning_coveralls: list[dict] | None = None,
):
    response = ppe._build_response(
        [PERSON],
        helmets or [],
        vests or [],
        cleaning_coveralls or [],
        0.0,
    )
    return response.persons[0]


def _equipment(person) -> list[tuple[str, str]]:
    return [(item.label, item.status) for item in person.equipment]


def test_worker_with_helmet_and_vest_is_compliant():
    person = _person_result(helmets=[HELMET], vests=[VEST])

    assert person.role == "worker"
    assert person.uniform_type == "vest"
    assert person.compliant is True
    assert _equipment(person) == [("Helmet", "compliant"), ("Vest", "compliant")]


def test_worker_with_vest_only_is_missing_helmet():
    person = _person_result(vests=[VEST])

    assert person.role == "worker"
    assert person.uniform_type == "vest"
    assert person.compliant is False
    assert _equipment(person) == [("Helmet", "violation"), ("Vest", "compliant")]
    assert ppe._violation_type(["Helmet"]) == "missing_helmet"


def test_janitor_with_helmet_and_cleaning_coverall_is_compliant():
    person = _person_result(helmets=[HELMET], cleaning_coveralls=[CLEANING_COVERALL])

    assert person.role == "janitor"
    assert person.uniform_type == "cleaning_coverall"
    assert person.compliant is True
    assert _equipment(person) == [
        ("Helmet", "compliant"),
        ("Cleaning Coverall", "compliant"),
    ]


def test_janitor_with_cleaning_coverall_only_is_missing_helmet():
    person = _person_result(cleaning_coveralls=[CLEANING_COVERALL])

    assert person.role == "janitor"
    assert person.uniform_type == "cleaning_coverall"
    assert person.compliant is False
    assert _equipment(person) == [
        ("Helmet", "violation"),
        ("Cleaning Coverall", "compliant"),
    ]
    assert ppe._violation_type(["Helmet"]) == "missing_helmet"


def test_unknown_with_helmet_only_is_missing_role_uniform():
    person = _person_result(helmets=[HELMET])

    assert person.role is None
    assert person.uniform_type is None
    assert person.compliant is False
    assert _equipment(person) == [
        ("Helmet", "compliant"),
        ("Role Uniform", "violation"),
    ]
    assert ppe._violation_type(["Role Uniform"]) == "missing_role_uniform"


def test_unknown_with_person_only_is_missing_helmet_and_role_uniform():
    person = _person_result()

    assert person.role is None
    assert person.uniform_type is None
    assert person.compliant is False
    assert _equipment(person) == [
        ("Helmet", "violation"),
        ("Role Uniform", "violation"),
    ]
    assert (
        ppe._violation_type(["Helmet", "Role Uniform"])
        == "missing_helmet_and_role_uniform"
    )


def test_cleaning_coverall_wins_when_both_uniforms_overlap():
    person = _person_result(
        helmets=[HELMET],
        vests=[VEST],
        cleaning_coveralls=[CLEANING_COVERALL],
    )

    assert person.role == "janitor"
    assert person.uniform_type == "cleaning_coverall"
    assert _equipment(person) == [
        ("Helmet", "compliant"),
        ("Cleaning Coverall", "compliant"),
    ]


def test_violation_type_mapping_supports_role_uniform_and_cleaning_coverall():
    cases = {
        ("Helmet",): "missing_helmet",
        ("Vest",): "missing_vest",
        ("Helmet", "Vest"): "missing_helmet_and_vest",
        ("Cleaning Coverall",): "missing_cleaning_coverall",
        ("Helmet", "Cleaning Coverall"): "missing_helmet_and_cleaning_coverall",
        ("Role Uniform",): "missing_role_uniform",
        ("Helmet", "Role Uniform"): "missing_helmet_and_role_uniform",
    }

    for missing, violation_type in cases.items():
        assert ppe._violation_type(list(missing)) == violation_type
