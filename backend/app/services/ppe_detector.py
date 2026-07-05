"""Backward-compatible re-export shim.

`PPEDetector` now lives in `app.services.ppe.detector`; the video/zone
orchestration it used to contain inline now lives in
`app.services.video_pipeline`. This module re-exports everything that used
to be defined here so existing imports (`from app.services.ppe_detector
import PPEDetector`, `from app.services import ppe_detector as ppe`) keep
working without touching every call site — see
`backend/docs/ppe_detector_refactor_map.md` and the Step 3/4 refactor
summary for what moved where.
"""

from __future__ import annotations

from app.core.config import settings
from app.services.ppe.detector import PPEDetector
from app.services.ppe.device import _resolve_video_tracker, _select_inference_device
from app.services.ppe.response_builder import (
    _append_tracking_overlay_frame,
    _build_response,
    _extract_result_boxes,
    _video_metadata,
)
from app.services.ppe.violation_matching import ViolationCase, _violation_type
from app.services.ppe.worker_tracking import WorkerState, _update_worker_status
from app.services.video_pipeline import (
    _record_violation_case,
    _save_violation_snapshot,
    save_violation,
)

__all__ = [
    "PPEDetector",
    "ViolationCase",
    "WorkerState",
    "settings",
    "_append_tracking_overlay_frame",
    "_build_response",
    "_extract_result_boxes",
    "_record_violation_case",
    "_resolve_video_tracker",
    "_save_violation_snapshot",
    "save_violation",
    "_select_inference_device",
    "_update_worker_status",
    "_video_metadata",
    "_violation_type",
]
