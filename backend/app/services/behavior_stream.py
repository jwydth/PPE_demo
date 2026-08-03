"""Independent live behavior worker: source -> pose/ReID -> behavior model.

This intentionally owns no PPE, zone, sign, or WebSocket concerns.  It is the
streaming counterpart of PPE Labelling's canonical video pose pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import Any

from app.core.config import settings
from app.services.behavior_inference import (
    BehaviorInferenceScheduler,
    get_behavior_scheduler,
)
from app.services.fall_detector import FallDetector, FallModelUnavailable
from app.services.frame_hub import FrameHubRegistry, frame_hubs
from app.services.stream_health import update_stream_health

logger = logging.getLogger(__name__)


class BehaviorStreamWorker:
    def __init__(
        self,
        *,
        source: str,
        source_name: str,
        fps: float,
        detector: FallDetector,
        scheduler: BehaviorInferenceScheduler | None = None,
        hubs: FrameHubRegistry | None = None,
    ) -> None:
        self.source = source
        self.source_name = source_name
        self.fps = max(float(fps), 1.0)
        self.detector = detector
        self.scheduler = scheduler or get_behavior_scheduler(detector)
        self.hubs = hubs or frame_hubs
        self.session = detector.create_live_session(fps=self.fps, frame_stride=1)
        self.latest_payload: dict[str, Any] | None = None
        self.unavailable: str | None = None
        self._incidents: list[dict[str, Any]] = []
        self._task: asyncio.Task[None] | None = None
        self._last_logged_status: str | None = None
        self._last_health_log = 0.0
        self.processed_frames = 0
        self.gap_events = 0
        self.dropped_frames = 0

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
        subscription = None
        warm_subscription = None
        try:
            logger.info("[BEHAVIOR] worker starting source=%s fps=%.2f", self.source_name, self.fps)
            prepare_camera = getattr(self.scheduler, "prepare_camera", None)
            if callable(prepare_camera):
                await prepare_camera(self.source)
                # Run the first real-source pose/ReID/tracker pass through a
                # latest-only subscription. CUDA libraries can have a sizeable
                # first pass for the real tensor/crop shapes even after dummy
                # warm-up; ordered capture must not begin until that cost is
                # paid or its queue starts life already behind.
                warm_subscription = await self.hubs.subscribe(
                    self.source,
                    policy="latest",
                    queue_size=1,
                )
                for _ in range(settings.BEHAVIOR_LIVE_WARMUP_FRAMES):
                    warm_packet = await warm_subscription.get()
                    if warm_packet is None:
                        return
                    await self.scheduler.infer(self.source, warm_packet.image)
                await_start_cohort = getattr(self.scheduler, "await_start_cohort", None)
                if callable(await_start_cohort):
                    await await_start_cohort()
            subscription = await self.hubs.subscribe(
                self.source,
                policy="ordered",
                queue_size=settings.BEHAVIOR_ORDERED_QUEUE_SIZE,
            )
            if warm_subscription is not None:
                await warm_subscription.close()
                warm_subscription = None
            source_ended = False
            while True:
                packet = await subscription.get()
                if packet is None:
                    return
                packets = [packet]
                while (
                    len(packets) < settings.BEHAVIOR_CAMERA_BURST_SIZE
                    and not subscription.queue.empty()
                ):
                    next_packet = await subscription.get()
                    if next_packet is None:
                        source_ended = True
                        break
                    packets.append(next_packet)
                results = await asyncio.gather(
                    *(self.scheduler.infer(self.source, item.image) for item in packets)
                )
                for packet, result in zip(packets, results, strict=True):
                    if packet.gap_before:
                        self.gap_events += 1
                        self.dropped_frames += packet.dropped_before
                        self.session.mark_discontinuity(
                            frame_index=packet.frame_index,
                            timestamp_seconds=packet.media_timestamp,
                            dropped_frames=packet.dropped_before,
                        )
                    frame_index = packet.frame_index
                    timestamp = packet.media_timestamp
                    postprocess_ms = 0.0
                    if self.session.accept_source_frame(timestamp):
                        frame = result.orig_img.copy()
                        postprocess_started = time.perf_counter()
                        payload = await asyncio.to_thread(
                            self.session.process_pose_result,
                            result,
                            frame=frame,
                            frame_index=frame_index,
                            source_name=self.source_name,
                            timestamp_seconds=timestamp,
                        )
                        postprocess_ms = (
                            time.perf_counter() - postprocess_started
                        ) * 1000.0
                        self.latest_payload = payload
                        self._incidents.extend(payload.get("incidents", []))
                        summary = payload.get("summary") or {}
                        status = str(summary.get("status", "no_detection"))
                        if status != self._last_logged_status and status != "no_detection":
                            logger.debug(
                                "[BEHAVIOR] source=%s frame=%s prediction=%s confidence=%.3f people=%s",
                                self.source_name, frame_index, status,
                                float(summary.get("top_confidence", 0.0)),
                                int(summary.get("person_count", 0)),
                            )
                            self._last_logged_status = status
                    self.processed_frames += 1
                    scheduler_health = self.scheduler.health_snapshot().get(self.source)
                    update_stream_health(
                        self.source,
                        behavior_processed_frames=self.processed_frames,
                        behavior_queue_depth=subscription.queue.qsize(),
                        behavior_dropped_frames=self.dropped_frames,
                        behavior_gap_events=self.gap_events,
                        behavior_batch_size=(scheduler_health.last_batch_size if scheduler_health else 0),
                        pose_inference_ms=(scheduler_health.last_pose_ms if scheduler_health else 0.0),
                        reid_tracking_ms=(scheduler_health.last_tracking_ms if scheduler_health else 0.0),
                        behavior_queue_wait_ms=(scheduler_health.last_queue_wait_ms if scheduler_health else 0.0),
                        behavior_total_ms=(scheduler_health.last_total_ms if scheduler_health else 0.0),
                        behavior_postprocess_ms=postprocess_ms,
                        feature_extraction_ms=getattr(self.session, "last_feature_ms", 0.0),
                        xgboost_ms=getattr(self.session, "last_classifier_ms", 0.0),
                    )
                    now = time.monotonic()
                    if now - self._last_health_log >= settings.BEHAVIOR_HEALTH_LOG_INTERVAL_SECONDS:
                        windows = self.session.windows
                        logger.debug(
                            "[BEHAVIOR_HEALTH] source=%s source_frame=%s processed=%s "
                            "active_tracks=%s ready_windows=%s queue=%s dropped=%s gaps=%s "
                            "total_ms=%.1f",
                            self.source_name,
                            frame_index,
                            self.processed_frames,
                            len(windows),
                            sum(len(window) >= self.detector.window_size for window in windows.values()),
                            subscription.queue.qsize(),
                            self.dropped_frames,
                            self.gap_events,
                            scheduler_health.last_total_ms if scheduler_health else 0.0,
                        )
                        self._last_health_log = now
                if source_ended:
                    return
        except asyncio.CancelledError:
            raise
        except FallModelUnavailable as exc:
            self.unavailable = str(exc)
            update_stream_health(self.source, last_error=self.unavailable)
            logger.warning("[BEHAVIOR] worker unavailable: %s", exc)
        except Exception:
            self.unavailable = "Behavior detection failed during live stream processing."
            update_stream_health(self.source, last_error=self.unavailable)
            logger.exception("[BEHAVIOR] worker failed")
        finally:
            if warm_subscription is not None:
                await warm_subscription.close()
            if subscription is not None:
                await subscription.close()
            await self.scheduler.unregister_camera(self.source)
