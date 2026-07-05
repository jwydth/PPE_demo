"""Feature-scoped PPE detection package.

`PPEDetector` itself is not re-exported here yet — it still lives in
`app/services/ppe_detector.py` because several of its methods
(`_real_video_pipeline`, `_mock_process_video`, `_mock_stream_video`) import
`app.services.zone_service` directly, which this package's modules must not
do (see Step 4 of the refactor plan, which introduces `video_pipeline/` as
the only allowed PPE/zone bridge). Re-exporting it from here before then
would also create a circular import with `ppe_detector.py`, which imports
these submodules.
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
]
