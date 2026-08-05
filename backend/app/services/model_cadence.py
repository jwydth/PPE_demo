"""Deterministic source-frame cadence for independent live models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelCadence:
    source_fps: float
    target_fps: float
    phase: int = 0

    @property
    def interval_frames(self) -> int:
        return max(1, round(max(self.source_fps, 1.0) / self.target_fps))

    def is_due(self, frame_index: int) -> bool:
        interval = self.interval_frames
        return frame_index % interval == self.phase % interval


@dataclass(slots=True)
class CadenceGate:
    """Select the first available frame at or after each model deadline."""

    cadence: ModelCadence
    next_frame_index: int | None = None

    def accept(self, frame_index: int) -> bool:
        if self.next_frame_index is None:
            self.next_frame_index = frame_index + self.cadence.phase
        if frame_index < self.next_frame_index:
            return False
        self.next_frame_index = frame_index + self.cadence.interval_frames
        return True
