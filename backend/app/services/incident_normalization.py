"""Pure normalization helpers for the unified incident read layer (see §2 of
INCIDENT_ANALYTICS_IMPLEMENTATION_PLAN.md). No DB access here on purpose —
these are simple string-in/string-out rules, kept in one place and unit
tested directly so severity/type casing never drifts between call sites.
"""

VALID_SEVERITIES = ("Critical", "High", "Medium", "Low")

_ZONE_TYPE_LABELS = {
    "RESTRICTED": "Restricted Zone Incursion",
    "WALKWAY": "Left Walkway",
    "SLIPPERY": "Slippery Area",
}

_BEHAVIOR_TYPE_LABELS = {
    "FALL_DETECTED": "Fall Detected",
    "RUNNING_DETECTED": "Running Detected",
    "FAINT_DETECTED": "Faint Detected",
    "COLLAPSE_DETECTED": "Collapse Detected",
}


def normalize_ppe_severity(violation_type: str) -> str:
    """PPE has no stored severity, so derive one from violation_type.

    Rule (confirmed default, see plan §1): multiple missing items (e.g.
    "missing_helmet_and_vest") -> High; a single missing item -> Medium;
    a proximity violation -> Low. PPE is never Critical by default.
    """
    normalized = violation_type.strip().lower()
    if "proximity" in normalized:
        return "Low"
    if "_and_" in normalized or " and " in normalized:
        return "High"
    return "Medium"


# What to *store* on a new zone violation. The detection pipeline never set a
# severity, so every incursion landed NULL and normalize_zone_severity below
# turned it into "Medium" on read. The dashboard's Open Incidents figure is
# Critical + High, so walking into a RESTRICTED area — the one thing a
# keep-out zone exists to catch — never registered there.
_ZONE_TYPE_SEVERITY = {
    "RESTRICTED": "High",
    "SLIPPERY": "Medium",
    "WALKWAY": "Medium",
}


def default_zone_severity(zone_type: str | None) -> str:
    """Severity to persist for an incursion of this zone type."""
    if not zone_type:
        return "Medium"
    return _ZONE_TYPE_SEVERITY.get(zone_type.strip().upper(), "Medium")


def normalize_zone_severity(raw: str | None) -> str:
    """zone_violations.severity is nullable free text; Title-case it and
    default to Medium when missing or unrecognized."""
    if not raw:
        return "Medium"
    titled = raw.strip().title()
    return titled if titled in VALID_SEVERITIES else "Medium"


def normalize_behavior_severity(raw: str | None) -> str:
    """behavior_incidents.severity is stored uppercase (e.g. "HIGH"); map to
    Title case. Defaults to High, matching the model's own column default."""
    if not raw:
        return "High"
    titled = raw.strip().title()
    return titled if titled in VALID_SEVERITIES else "High"


def ppe_type_label(violation_type: str) -> str:
    """e.g. "missing_helmet_and_vest" -> "Missing Helmet and Vest"."""
    words = violation_type.strip().replace("_", " ").split(" ")
    return " ".join(word if word == "and" else word.capitalize() for word in words if word)


def zone_type_label(zone_type: str) -> str:
    return _ZONE_TYPE_LABELS.get(
        zone_type.strip().upper(),
        zone_type.strip().replace("_", " ").title(),
    )


def behavior_type_label(behavior_type: str) -> str:
    return _BEHAVIOR_TYPE_LABELS.get(
        behavior_type.strip().upper(),
        behavior_type.strip().replace("_", " ").title(),
    )
