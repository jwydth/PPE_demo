"""Independent live behavior worker: source -> pose/ReID -> behavior model.

This intentionally owns no PPE, zone, sign, or WebSocket concerns.  It is the
streaming counterpart of PPE Labelling's canonical video pose pipeline.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from app.core.config import settings
from app.services.fall_detector import FallDetector, FallModelUnavailable

logger = logging.getLogger(__name__)


class BehaviorStreamWorker:
    def __init__(self, *, source: str, source_name: str, fps: float, detector: FallDetector) -> None:
        self.source = source
        self.source_name = source_name
        self.fps = max(float(fps), 1.0)
        self.detector = detector
        self.session = detector.create_live_session(fps=self.fps, frame_stride=1)
        self.latest_payload: dict[str, Any] | None = None
        self.unavailable: str | None = None
        self._incidents: list[dict[str, Any]] = []
        self._task: asyncio.Task[None] | None = None
        self._last_logged_status: str | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    def snapshot(self) -> tuple[dict[str, Any] | None, str | None, list[dict[str, Any]]]:
        incidents, self._incidents = self._incidents, []
        return self.latest_payload, self.unavailable, incidents

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        try:
            logger.info("[BEHAVIOR] worker starting source=%s fps=%.2f", self.source_name, self.fps)
            pose_model = await asyncio.to_thread(self.detector._ensure_model)
            results = pose_model.track(
                source=self.source, stream=True, persist=True,
                tracker=str(self.detector.tracker_path),
                conf=settings.FALL_PERSON_CONFIDENCE, device=self.detector.device,
                imgsz=640, half=str(self.detector.device).startswith("cuda"), verbose=False,
                # Behavioral features require source order; PPE may still use
                # drop-to-latest in its separate pipeline.
                stream_buffer=True,
            )
            iterator = iter(results)
            frame_index = 0
            while True:
                result = await asyncio.to_thread(next, iterator, None)
                if result is None:
                    return
                timestamp = frame_index / self.fps
                if self.session.accept_source_frame(timestamp):
                    frame = result.orig_img.copy()
                    payload = self.session.process_pose_result(
                        result, frame=frame, frame_index=frame_index,
                        source_name=self.source_name, timestamp_seconds=timestamp,
                    )
                    self.latest_payload = payload
                    self._incidents.extend(payload.get("incidents", []))
                    summary = payload.get("summary") or {}
                    status = str(summary.get("status", "no_detection"))
                    if status != self._last_logged_status and status != "no_detection":
                        logger.info(
                            "[BEHAVIOR] source=%s frame=%s prediction=%s confidence=%.3f people=%s",
                            self.source_name, frame_index, status,
                            float(summary.get("top_confidence", 0.0)),
                            int(summary.get("person_count", 0)),
                        )
                        self._last_logged_status = status
                if frame_index and frame_index % 60 == 0:
                    windows = self.session.windows
                    logger.info(
                        "[BEHAVIOR] source=%s frames=%s active_tracks=%s ready_windows=%s",
                        self.source_name, frame_index, len(windows),
                        sum(len(window) >= self.detector.window_size for window in windows.values()),
                    )
                frame_index += 1
        except asyncio.CancelledError:
            raise
        except FallModelUnavailable as exc:
            self.unavailable = str(exc)
            logger.warning("[BEHAVIOR] worker unavailable: %s", exc)
        except Exception:
            self.unavailable = "Behavior detection failed during live stream processing."
            logger.exception("[BEHAVIOR] worker failed")
