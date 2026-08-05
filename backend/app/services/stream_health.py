"""Thread-safe, secret-safe rolling performance metrics for live streams."""

from __future__ import annotations

import hashlib
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field, fields
from pathlib import Path
from statistics import fmean
from urllib.parse import urlsplit


ROLLING_WINDOW_SECONDS = 60.0


@dataclass(slots=True)
class RollingSamples:
    values: deque[tuple[float, float]] = field(default_factory=deque)

    def add(self, value: float, now: float) -> None:
        self.values.append((now, float(value)))
        self.prune(now)

    def prune(self, now: float) -> None:
        cutoff = now - ROLLING_WINDOW_SECONDS
        while self.values and self.values[0][0] < cutoff:
            self.values.popleft()

    def snapshot(self, now: float) -> dict[str, float | int]:
        self.prune(now)
        samples = [value for _, value in self.values]
        if not samples:
            return {
                "last_ms": 0.0,
                "mean_ms": 0.0,
                "p50_ms": 0.0,
                "p95_ms": 0.0,
                "p99_ms": 0.0,
                "max_ms": 0.0,
                "samples": 0,
            }
        ordered = sorted(samples)
        return {
            "last_ms": round(samples[-1], 3),
            "mean_ms": round(fmean(samples), 3),
            "p50_ms": round(_percentile(ordered, 0.50), 3),
            "p95_ms": round(_percentile(ordered, 0.95), 3),
            "p99_ms": round(_percentile(ordered, 0.99), 3),
            "max_ms": round(max(samples), 3),
            "samples": len(samples),
        }


@dataclass(slots=True)
class EventSeries:
    timestamps: deque[float] = field(default_factory=deque)

    def add(self, now: float) -> float | None:
        interval_ms = (now - self.timestamps[-1]) * 1000.0 if self.timestamps else None
        self.timestamps.append(now)
        self.prune(now)
        return interval_ms

    def prune(self, now: float) -> None:
        cutoff = now - ROLLING_WINDOW_SECONDS
        while self.timestamps and self.timestamps[0] < cutoff:
            self.timestamps.popleft()

    def rate(self, now: float) -> float:
        self.prune(now)
        if len(self.timestamps) < 2:
            return 0.0
        elapsed = self.timestamps[-1] - self.timestamps[0]
        return round((len(self.timestamps) - 1) / elapsed, 3) if elapsed > 0 else 0.0


@dataclass(slots=True)
class StreamHealth:
    source_id: str
    source_label: str
    updated_monotonic: float = 0.0
    source_fps: float = 0.0
    captured_frames: int = 0
    ppe_processed_frames: int = 0
    ppe_dropped_frames: int = 0
    behavior_processed_frames: int = 0
    behavior_queue_depth: int = 0
    behavior_dropped_frames: int = 0
    behavior_gap_events: int = 0
    behavior_batch_size: int = 0
    sign_calls: int = 0
    sign_last_frame_index: int = -1
    preview_generated_frames: int = 0
    preview_sent_frames: int = 0
    preview_coalesced_frames: int = 0
    preview_gap_count: int = 0
    capture_age_ms: float = 0.0
    last_error: str | None = None
    timings: dict[str, RollingSamples] = field(default_factory=dict)
    events: dict[str, EventSeries] = field(default_factory=dict)


_records: dict[str, StreamHealth] = {}
_lock = threading.Lock()


def _percentile(ordered: list[float], fraction: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _identity(source: str) -> tuple[str, str]:
    source_id = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    parsed = urlsplit(source)
    if parsed.scheme and parsed.hostname:
        port = f":{parsed.port}" if parsed.port else ""
        label = f"{parsed.scheme}://{parsed.hostname}{port}{parsed.path}"
    else:
        label = Path(source).name or "local-source"
    return source_id, label


def _record(source: str) -> StreamHealth:
    source_id, label = _identity(source)
    return _records.setdefault(
        source,
        StreamHealth(source_id=source_id, source_label=label),
    )


def update_stream_health(source: str, **values) -> None:
    with _lock:
        record = _record(source)
        record.updated_monotonic = time.monotonic()
        for name, value in values.items():
            if hasattr(record, name) and name not in {"timings", "events"}:
                setattr(record, name, value)


def increment_stream_health(source: str, **values: int) -> None:
    with _lock:
        record = _record(source)
        record.updated_monotonic = time.monotonic()
        for name, value in values.items():
            if hasattr(record, name) and name not in {"timings", "events"}:
                setattr(record, name, getattr(record, name) + value)


def observe_stream_timing(source: str, name: str, milliseconds: float) -> None:
    now = time.monotonic()
    with _lock:
        record = _record(source)
        record.updated_monotonic = now
        record.timings.setdefault(name, RollingSamples()).add(milliseconds, now)


def mark_stream_event(source: str, name: str) -> None:
    now = time.monotonic()
    with _lock:
        record = _record(source)
        record.updated_monotonic = now
        interval_ms = record.events.setdefault(name, EventSeries()).add(now)
        if interval_ms is not None:
            record.timings.setdefault(f"{name}_interval", RollingSamples()).add(
                interval_ms,
                now,
            )
            if name == "preview_sent" and interval_ms > 250.0:
                record.preview_gap_count += 1


def stream_health_snapshot() -> list[dict]:
    now = time.monotonic()
    with _lock:
        snapshots = []
        for record in _records.values():
            item = {
                definition.name: getattr(record, definition.name)
                for definition in fields(record)
                if definition.name not in {"updated_monotonic", "timings", "events"}
            }
            item["updated_age_seconds"] = round(
                max(0.0, now - record.updated_monotonic), 3
            )
            item["timings"] = {
                name: samples.snapshot(now)
                for name, samples in sorted(record.timings.items())
            }
            item["rates_fps"] = {
                name: series.rate(now)
                for name, series in sorted(record.events.items())
            }
            snapshots.append(item)
        return sorted(snapshots, key=lambda item: item["source_id"])


def clear_stream_health() -> None:
    with _lock:
        _records.clear()
