"""Small process-wide coordination primitives for shared CUDA execution."""

from __future__ import annotations

import threading


# Multiple independent Ultralytics predictors launching from Python threads can
# contend on the same CUDA context and produce long latency spikes even when
# average GPU utilization is low. Temporal behavior gets batching; PPE preview
# is latest-only, so serializing the short CUDA launch sections is preferable
# to unpredictable concurrent launches.
gpu_inference_lock = threading.RLock()
