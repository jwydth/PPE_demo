"""Feature-scoped PPE detection package.

This module must not import from `app.services.zone_service` — cross-feature
logic belongs in `app.services.video_pipeline`, the only module allowed to
import from both `app.services.ppe` and `app.services.zone_service`.

`PPEDetector` (in `app.services.ppe.detector`) is exposed lazily via
`__getattr__` below rather than a top-level import: `ppe.detector` imports
`app.services.video_pipeline`, which imports several sibling modules in this
package — a top-level `from app.services.ppe.detector import PPEDetector`
here would try to fully import `ppe.detector` (and transitively
`video_pipeline`) *while this package's own `__init__` is still running*,
which is a circular import. Deferring the import to attribute-access time
(PEP 562) avoids that without losing `from app.services.ppe import
PPEDetector` as a working spelling.
"""

from __future__ import annotations

from app.services.ppe.constants import (
    CLEANING_COVERALL_LABEL,
    COMPLIANT_COLOR,
    HELMET_LABEL,
    MISSING_LABEL_ORDER,
    PERSON_COLOR,
    ROLE_UNIFORM_LABEL,
    VEST_LABEL,
    VIOLATION_COLOR,
)
from app.services.ppe.violation_matching import ViolationCase
from app.services.ppe.worker_tracking import WorkerState

__all__ = [
    "CLEANING_COVERALL_LABEL",
    "COMPLIANT_COLOR",
    "HELMET_LABEL",
    "MISSING_LABEL_ORDER",
    "PERSON_COLOR",
    "ROLE_UNIFORM_LABEL",
    "VEST_LABEL",
    "VIOLATION_COLOR",
    "ViolationCase",
    "WorkerState",
    "PPEDetector",
]


def __getattr__(name: str):
    if name == "PPEDetector":
        from app.services.ppe.detector import PPEDetector

        return PPEDetector
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
