"""SQL predicates for the incident-feed filters.

Severity is the awkward one. It is not a single stored column: PPE derives it
from `violation_type` text, zone reads a nullable free-text column, and behavior
reads an uppercase column with a different default. `incident_normalization`
holds those rules for the read layer; this module expresses the *same* rules as
SQL so the feed can filter before LIMIT/OFFSET instead of fetching rows and
discarding them (which would make page sizes and totals wrong).

Two encodings of one rule can drift, so tests/services/test_incident_filters.py
asserts that for every stored row the predicate here selects exactly the rows
the normalizer labels with that severity. Change one, change both.
"""

from sqlalchemy import ColumnElement, and_, false, func, not_, or_

from app.models.behavior_incident import BehaviorIncident
from app.models.ppe_violation import PPEViolation
from app.models.zone_violation import ZoneViolation
from app.services.incident_normalization import VALID_SEVERITIES

CATEGORIES = ("ppe", "zone", "behavior")

# Zone defaults to Medium for NULL/unrecognized values, behavior to High, so
# each needs to know which values are "explicitly something else".
_ZONE_EXPLICIT = ("CRITICAL", "HIGH", "LOW")
_BEHAVIOR_EXPLICIT = ("CRITICAL", "MEDIUM", "LOW")


def _upper(column: ColumnElement[str]) -> ColumnElement[str]:
    return func.upper(func.trim(column))


def _ppe_is_proximity() -> ColumnElement[bool]:
    return _upper(PPEViolation.violation_type).contains("PROXIMITY")


def _ppe_is_multiple() -> ColumnElement[bool]:
    # normalize_ppe_severity looks for "_and_" or " and " in the lowered type.
    upper = _upper(PPEViolation.violation_type)
    return or_(upper.contains("_AND_"), upper.contains(" AND "))


def ppe_severity_predicate(severity: str) -> ColumnElement[bool]:
    """Mirrors normalize_ppe_severity: proximity -> Low, several missing items
    -> High, otherwise Medium. PPE is never Critical, so that asks for nothing
    rather than everything."""
    if severity == "Low":
        return _ppe_is_proximity()
    if severity == "High":
        return and_(not_(_ppe_is_proximity()), _ppe_is_multiple())
    if severity == "Medium":
        return and_(not_(_ppe_is_proximity()), not_(_ppe_is_multiple()))
    return false()


def zone_severity_predicate(severity: str) -> ColumnElement[bool]:
    """Mirrors normalize_zone_severity: the stored value when it is one of the
    four known severities, Medium for NULL or anything unrecognized."""
    column = _upper(ZoneViolation.severity)
    if severity == "Medium":
        return or_(
            ZoneViolation.severity.is_(None),  # type: ignore[union-attr]
            column.notin_(_ZONE_EXPLICIT),
        )
    return column == severity.upper()


def behavior_severity_predicate(severity: str) -> ColumnElement[bool]:
    """Mirrors normalize_behavior_severity: the stored value when recognized,
    High for NULL or anything unrecognized."""
    column = _upper(BehaviorIncident.severity)
    if severity == "High":
        return or_(
            BehaviorIncident.severity.is_(None),  # type: ignore[union-attr]
            column.notin_(_BEHAVIOR_EXPLICIT),
        )
    return column == severity.upper()


_PREDICATES = {
    "ppe": ppe_severity_predicate,
    "zone": zone_severity_predicate,
    "behavior": behavior_severity_predicate,
}


def severity_filter(category: str, severities: list[str]) -> ColumnElement[bool] | None:
    """One OR-ed predicate for the requested severities, or None for "no
    filter". An empty list means nothing was asked for, which is not the same
    as asking for nothing — it leaves the category unfiltered."""
    wanted = [s for s in severities if s in VALID_SEVERITIES]
    if not wanted:
        return None
    build = _PREDICATES[category]
    return or_(*(build(severity) for severity in wanted))
