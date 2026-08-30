"""Independent live behavior worker: source -> pose/ReID -> behavior model.

This intentionally owns no PPE, zone, sign, or WebSocket concerns.  It is the
streaming counterpart of PPE Labelling's canonical video pose pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import Any, Callable

from app.core.config import settings
from app.services.annotated_stream import AnnotatedStateStore
from app.services.behavior_inference import (
    BehaviorInferenceScheduler,
    get_behavior_scheduler,
)
from app.services.fall_detector import FallDetector, FallModelUnavailable
from app.services.frame_hub import FrameHubRegistry, frame_hubs
from app.services.zone_service import COORD_SCALE, ZoneViolationRecord, is_point_in_ignore_zone
from app.services.stream_health import (
    increment_stream_health,
    mark_stream_event,
    observe_stream_timing,
    update_stream_health,
)

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
        render_store: AnnotatedStateStore | None = None,
        ignore_zones_provider: Callable[[], list[ZoneViolationRecord]] | None = None,
    ) -> None:
        self.source = source
        self.source_name = source_name
        self.fps = max(float(fps), 1.0)
        self.detector = detector
        self.scheduler = scheduler or get_behavior_scheduler(detector)
        self.hubs = hubs or frame_hubs
        self.render_store = render_store
        self.ignore_zones_provider = ignore_zones_provider
        self.session = detector.create_live_session(fps=self.fps, frame_stride=1)
        self.latest_payload: dict[str, Any] | None = None
        # Reported to the UI through snapshot(). A source too slow for the
        # classifier's timeline used to produce nothing at all with no error
        # anywhere — the toggle was on, the worker ran, and behavior simply
        # never happened.
        self.unavailable: str | None = self.session.unsupported_source_reason()
        if self.unavailable:
            logger.warning("[BEHAVIOR] '%s' cannot be classified: %s", source, self.unavailable)
        self._incidents: list[dict[str, Any]] = []
        self._task: asyncio.Task[None] | None = None
        self._last_logged_status: str | None = None
        self._last_health_log = 0.0
        self.processed_frames = 0
        self.gap_events = 0
        self.dropped_frames = 0
        self.interpolated_frames = 0
        self.unrepaired_gap_events = 0
        self._stream_epoch: str | None = None

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

    def _is_detection_ignored(self, detection: dict[str, Any], frame_width: int, frame_height: int) -> bool:
        if self.ignore_zones_provider is None:
            return False
        bbox = detection.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            return False
        test_point = (
            ((float(bbox[0]) + float(bbox[2])) / 2 / frame_width) * COORD_SCALE,
            (float(bbox[3]) / frame_height) * COORD_SCALE,
        )
        return is_point_in_ignore_zone(self.ignore_zones_provider(), test_point)

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
                        epoch_changed = (
                            self._stream_epoch is not None
                            and packet.stream_epoch != self._stream_epoch
                        )
                        if (
                            epoch_changed
                            or packet.dropped_before > settings.BEHAVIOR_POSE_REPAIR_MAX_GAP
                        ):
                            self.unrepaired_gap_events += 1
                            self.session.mark_discontinuity(
                                frame_index=packet.frame_index,
                                timestamp_seconds=packet.media_timestamp,
                                dropped_frames=packet.dropped_before,
                            )
                    self._stream_epoch = packet.stream_epoch
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
                            ignore_detection=lambda detection: self._is_detection_ignored(
                                detection,
                                frame.shape[1],
                                frame.shape[0],
                            ),
                        )
                        timeline = {
                            "stream_epoch": packet.stream_epoch,
                            "source_frame_index": packet.frame_index,
                            "media_pts_ms": packet.media_pts_ms,
                            "source_time_ms": packet.source_time_ms,
                            "inference_completed_ms": time.time() * 1000.0,
                            "discontinuity_sequence": packet.discontinuity_sequence,
                        }
                        payload["timeline"] = timeline
                        for detection in payload.get("detections", []):
                            detection.update(timeline)
                        if self.render_store is not None:
                            self.render_store.update_pose(
                                packet,
                                payload.get("detections", []),
                            )
                        postprocess_ms = (
                            time.perf_counter() - postprocess_started
                        ) * 1000.0
                        self.latest_payload = payload
                        repaired_samples = self.session.last_pose_repaired_samples
                        self.interpolated_frames += repaired_samples
                        if repaired_samples:
                            increment_stream_health(
                                self.source,
                                behavior_interpolated_frames=repaired_samples,
                            )
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
                    mark_stream_event(self.source, "behavior_output")
                    scheduler_health = self.scheduler.health_snapshot().get(self.source)
                    update_stream_health(
                        self.source,
                        behavior_processed_frames=self.processed_frames,
                        behavior_queue_depth=subscription.queue.qsize(),
                        behavior_dropped_frames=self.dropped_frames,
                        behavior_gap_events=self.gap_events,
                        behavior_unrepaired_gap_events=self.unrepaired_gap_events,
                        behavior_batch_size=(scheduler_health.last_batch_size if scheduler_health else 0),
                    )
                    observe_stream_timing(self.source, "behavior_postprocess", postprocess_ms)
                    feature_ms = getattr(self.session, "last_feature_ms", 0.0)
                    classifier_ms = getattr(self.session, "last_classifier_ms", 0.0)
                    if feature_ms > 0:
                        observe_stream_timing(self.source, "behavior_feature_extraction", feature_ms)
                    if classifier_ms > 0:
                        observe_stream_timing(self.source, "behavior_classifier", classifier_ms)
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
