"""Inference device selection and video tracker path resolution."""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import BACKEND_DIR

logger = logging.getLogger(__name__)


def _select_inference_device(preferred_device: str) -> str:
    requested = (preferred_device or "auto").strip().lower()
    if requested == "cpu":
        return "cpu"

    if requested not in {"auto", "cuda", "gpu"} and not (
        requested.startswith("cuda:") or requested.isdigit()
    ):
        return "cpu"

    try:
        import torch
    except Exception:
        return "cpu"

    torch_cuda = getattr(torch, "cuda", None)
    if torch_cuda is None or not torch_cuda.is_available():
        return "cpu"

    device_count = torch_cuda.device_count()
    if requested.isdigit():
        device_index = int(requested)
    elif requested.startswith("cuda:"):
        try:
            device_index = int(requested.split(":", 1)[1])
        except ValueError:
            device_index = 0
    else:
        device_index = 0

    if device_index >= device_count:
        return "cpu"

    device = f"cuda:{device_index}"
    return device


def _resolve_video_tracker(tracker: str) -> str:
    configured = (tracker or "").strip()
    if not configured:
        logger.warning("VIDEO_TRACKER is empty; falling back to Ultralytics default tracker.")
        return configured

    tracker_path = Path(configured).expanduser()
    if tracker_path.is_absolute():
        if tracker_path.exists():
            return str(tracker_path)
        logger.warning(
            "Configured VIDEO_TRACKER path '%s' does not exist; falling back to '%s'.",
            tracker_path,
            configured,
        )
        return configured

    backend_relative = BACKEND_DIR / tracker_path
    if backend_relative.exists():
        return str(backend_relative.resolve())

    if len(tracker_path.parts) == 1:
        repo_tracker = BACKEND_DIR / "trackers" / tracker_path
        if repo_tracker.exists():
            return str(repo_tracker.resolve())

    logger.warning(
        "Configured VIDEO_TRACKER '%s' was not found under backend at '%s'; "
        "falling back to the configured value.",
        configured,
        backend_relative,
    )
    return configured
