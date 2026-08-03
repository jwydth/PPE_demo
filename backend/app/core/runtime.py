"""Process-level CPU/GPU runtime configuration.

This module is intentionally called before router/model imports in ``main`` so
native libraries do not each create a full-machine worker pool.
"""

from __future__ import annotations

import logging
import os

from app.core.config import settings
from app.services.ppe.device import _select_inference_device

logger = logging.getLogger(__name__)

_configured = False


def configure_inference_runtime() -> None:
    global _configured
    if _configured:
        return

    os.environ.setdefault("OMP_NUM_THREADS", str(settings.BEHAVIOR_TORCH_THREADS))
    os.environ.setdefault("MKL_NUM_THREADS", str(settings.BEHAVIOR_TORCH_THREADS))
    os.environ.setdefault("OPENBLAS_NUM_THREADS", str(settings.BEHAVIOR_TORCH_THREADS))
    os.environ.setdefault("NUMEXPR_NUM_THREADS", str(settings.BEHAVIOR_TORCH_THREADS))

    import cv2
    import torch

    torch.set_num_threads(settings.BEHAVIOR_TORCH_THREADS)
    try:
        torch.set_num_interop_threads(settings.BEHAVIOR_TORCH_INTEROP_THREADS)
    except RuntimeError:
        # PyTorch only permits this before inter-op work starts. This can occur
        # in tests that import a model module before importing app.main.
        logger.warning("PyTorch inter-op thread pool was already initialized.")
    cv2.setNumThreads(settings.BEHAVIOR_OPENCV_THREADS)

    resolved_device = _select_inference_device(settings.INFERENCE_DEVICE)
    logger.info(
        "Inference runtime configured: pose=%s reid=%s classifier=cpu "
        "half=%s behavior_window=%s@%sfps stride=%s cpu_threads="
        "torch:%s/inter-op:%s opencv:%s xgboost:%s",
        resolved_device,
        resolved_device,
        settings.BEHAVIOR_REID_HALF and resolved_device.startswith("cuda"),
        settings.FALL_BEHAVIOR_WINDOW_FRAMES,
        settings.FALL_BEHAVIOR_CANONICAL_FPS,
        settings.FALL_LIVE_FRAME_STRIDE,
        settings.BEHAVIOR_TORCH_THREADS,
        settings.BEHAVIOR_TORCH_INTEROP_THREADS,
        settings.BEHAVIOR_OPENCV_THREADS,
        settings.BEHAVIOR_XGBOOST_THREADS,
    )
    _configured = True
