"""Thread-safe, secret-safe live stream performance snapshots."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(slots=True)
class StreamHealth:
    source_id: str
    source_label: str
    updated_monotonic: float = 0.0
    source_fps: float = 0.0
    captured_frames: int = 0
    ppe_processed_frames: int = 0
    behavior_processed_frames: int = 0
    behavior_queue_depth: int = 0
    behavior_dropped_frames: int = 0
    behavior_gap_events: int = 0
    behavior_batch_size: int = 0
    capture_age_ms: float = 0.0
    ppe_inference_ms: float = 0.0
    pose_inference_ms: float = 0.0
    reid_tracking_ms: float = 0.0
    behavior_queue_wait_ms: float = 0.0
    behavior_total_ms: float = 0.0
    behavior_postprocess_ms: float = 0.0
    feature_extraction_ms: float = 0.0
    xgboost_ms: float = 0.0
    jpeg_ms: float = 0.0
    websocket_ms: float = 0.0
    last_error: str | None = None


_records: dict[str, StreamHealth] = {}
_lock = threading.Lock()


def _identity(source: str) -> tuple[str, str]:
    source_id = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    parsed = urlsplit(source)
    if parsed.scheme and parsed.hostname:
        port = f":{parsed.port}" if parsed.port else ""
        label = f"{parsed.scheme}://{parsed.hostname}{port}{parsed.path}"
    else:
        label = Path(source).name or "local-source"
    return source_id, label


def update_stream_health(source: str, **values) -> None:
    source_id, label = _identity(source)
    with _lock:
        record = _records.setdefault(
            source,
            StreamHealth(source_id=source_id, source_label=label),
        )
        record.updated_monotonic = time.monotonic()
        for name, value in values.items():
            if hasattr(record, name):
                setattr(record, name, value)


def increment_stream_health(source: str, **values: int) -> None:
    source_id, label = _identity(source)
    with _lock:
        record = _records.setdefault(
            source,
            StreamHealth(source_id=source_id, source_label=label),
        )
        record.updated_monotonic = time.monotonic()
        for name, value in values.items():
            if hasattr(record, name):
                setattr(record, name, getattr(record, name) + value)


def stream_health_snapshot() -> list[dict]:
    now = time.monotonic()
    with _lock:
        snapshots = []
        for record in _records.values():
            item = asdict(record)
            item["updated_age_seconds"] = round(
                max(0.0, now - record.updated_monotonic), 3
            )
            item.pop("updated_monotonic", None)
            snapshots.append(item)
        return sorted(snapshots, key=lambda item: item["source_id"])


def clear_stream_health() -> None:
    with _lock:
        _records.clear()
