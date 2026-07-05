"""Labels, colors, and ordering constants shared across the PPE detection package."""

from __future__ import annotations

COMPLIANT_COLOR = "#22c55e"
VIOLATION_COLOR = "#ef4444"
PERSON_COLOR = "#f97316"
HELMET_LABEL = "Helmet"
VEST_LABEL = "Vest"
CLEANING_COVERALL_LABEL = "Cleaning Coverall"
ROLE_UNIFORM_LABEL = "Role Uniform"
MISSING_LABEL_ORDER = (
    HELMET_LABEL,
    VEST_LABEL,
    CLEANING_COVERALL_LABEL,
    ROLE_UNIFORM_LABEL,
)
