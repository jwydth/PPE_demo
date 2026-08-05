"""One decoded frame source shared by all consumers of a camera.

The hub owns the only ``cv2.VideoCapture`` for a source. PPE subscribers get a
latest-value queue while behavior subscribers get an ordered bounded queue.
If ordered delivery overflows, the queue is reset and the next packet is marked
as discontinuous so a temporal classifier cannot consume a false sequence.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, Callable, Literal

import numpy as np

from app.core.config import settings
from app.services.stream_health import mark_stream_event, update_stream_health

logger = logging.getLogger(__name__)

DeliveryPolicy = Literal["latest", "ordered"]
_END = object()


@dataclass(frozen=True, slots=True)
class FramePacket:
    source: str
    frame_index: int
    media_timestamp: float
    captured_monotonic: float
    image: np.ndarray
    stream_epoch: str
    media_pts_ms: float
    source_time_ms: float
    discontinuity_sequence: int = 0
    gap_before: bool = False
    dropped_before: int = 0


@dataclass(slots=True)
class FrameHubHealth:
    source: str
    fps: float = 0.0
    captured_frames: int = 0
    started_monotonic: float = 0.0
    last_frame_monotonic: float = 0.0
    reader_error: str | None = None
    subscribers: int = 0


class FrameSubscription:
    def __init__(
        self,
        *,
        hub: "CameraFrameHub",
        policy: DeliveryPolicy,
        queue_size: int,
    ) -> None:
        self.hub = hub
        self.policy = policy
        self.queue: asyncio.Queue[FramePacket | object] = asyncio.Queue(
            maxsize=1 if policy == "latest" else queue_size
        )
        self.dropped_frames = 0
        self.delivered_frames = 0
        self._closed = False

    async def get(self) -> FramePacket | None:
        item = await self.queue.get()
        if item is _END:
            return None
        self.delivered_frames += 1
        return item  # type: ignore[return-value]

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.hub.registry.release(self)

    def _offer(self, packet: FramePacket) -> None:
        if self._closed:
            return
        if not self.queue.full():
            self.queue.put_nowait(packet)
            return

        if self.policy == "latest":
            try:
                previous = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                previous = None
            dropped = 1 if previous is not None and previous is not _END else 0
        else:
            dropped = 0
            while True:
                try:
                    previous = self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if previous is not _END:
                    dropped += 1

        self.dropped_frames += dropped
        self.queue.put_nowait(
            replace(
                packet,
                gap_before=True,
                dropped_before=packet.dropped_before + dropped,
            )
        )

    def _finish(self) -> None:
        if self._closed:
            return
        while self.queue.full():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.queue.put_nowait(_END)


class CameraFrameHub:
    def __init__(
        self,
        source: str,
        *,
        registry: "FrameHubRegistry",
        loop: asyncio.AbstractEventLoop,
        capture_factory: Callable[[str], Any],
    ) -> None:
        self.source = source
        self.registry = registry
        self.loop = loop
        self.capture_factory = capture_factory
        self.stream_epoch = uuid.uuid4().hex
        self.health = FrameHubHealth(source=source)
        self._subscribers: set[FrameSubscription] = set()
        self._ready = asyncio.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name=f"frame-hub:{source[-48:]}",
            daemon=True,
        )

    def start(self) -> None:
        self.health.started_monotonic = time.monotonic()
        self._thread.start()

    async def wait_ready(self) -> None:
        await self._ready.wait()
        if self.health.reader_error:
            raise RuntimeError(self.health.reader_error)

    def subscribe(self, policy: DeliveryPolicy, queue_size: int) -> FrameSubscription:
        subscription = FrameSubscription(
            hub=self,
            policy=policy,
            queue_size=queue_size,
        )
        self._subscribers.add(subscription)
        self.health.subscribers = len(self._subscribers)
        return subscription

    async def remove(self, subscription: FrameSubscription) -> bool:
        self._subscribers.discard(subscription)
        self.health.subscribers = len(self._subscribers)
        if self._subscribers:
            return False
        self._stop.set()
        await asyncio.to_thread(self._thread.join, 6.0)
        if self._thread.is_alive():
            logger.error("Frame reader did not stop within timeout: %s", self.source)
            return False
        return True

    def _capture_loop(self) -> None:
        capture = None
        try:
            if self.source.startswith("rtsp://"):
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                    "rtsp_transport;tcp|timeout;3000000"
                )
            capture = self.capture_factory(self.source)
            if not capture.isOpened():
                raise RuntimeError(f"Could not open video source: {self.source}")

            import cv2

            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            self.health.fps = fps if fps > 0 else 30.0
            self.loop.call_soon_threadsafe(self._ready.set)
            frame_index = 0
            first_media_pts_ms: float | None = None
            last_media_pts_ms = -1.0
            discontinuity_sequence = 0
            while not self._stop.is_set():
                ok, image = capture.read()
                if not ok or image is None:
                    break
                captured_monotonic = time.monotonic()
                source_time_ms = time.time() * 1000.0
                raw_media_pts_ms = float(capture.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
                if raw_media_pts_ms > 0.0:
                    if first_media_pts_ms is None:
                        first_media_pts_ms = raw_media_pts_ms
                    media_pts_ms = raw_media_pts_ms - first_media_pts_ms
                else:
                    media_pts_ms = frame_index * 1000.0 / self.health.fps

                expected_step_ms = 1000.0 / self.health.fps
                source_discontinuity = (
                    last_media_pts_ms >= 0.0
                    and (
                        media_pts_ms <= last_media_pts_ms - expected_step_ms
                        or media_pts_ms - last_media_pts_ms > expected_step_ms * 12
                    )
                )
                if source_discontinuity:
                    discontinuity_sequence += 1
                    self.stream_epoch = uuid.uuid4().hex
                    first_media_pts_ms = raw_media_pts_ms if raw_media_pts_ms > 0.0 else None
                    media_pts_ms = 0.0
                elif last_media_pts_ms >= 0.0 and media_pts_ms <= last_media_pts_ms:
                    media_pts_ms = last_media_pts_ms + expected_step_ms

                packet = FramePacket(
                    source=self.source,
                    frame_index=frame_index,
                    media_timestamp=media_pts_ms / 1000.0,
                    captured_monotonic=captured_monotonic,
                    image=image,
                    stream_epoch=self.stream_epoch,
                    media_pts_ms=media_pts_ms,
                    source_time_ms=source_time_ms,
                    discontinuity_sequence=discontinuity_sequence,
                    gap_before=source_discontinuity,
                )
                last_media_pts_ms = media_pts_ms
                self.health.captured_frames += 1
                self.health.last_frame_monotonic = packet.captured_monotonic
                mark_stream_event(self.source, "capture")
                if frame_index % max(1, int(self.health.fps)) == 0:
                    update_stream_health(
                        self.source,
                        source_fps=self.health.fps,
                        captured_frames=self.health.captured_frames,
                        capture_age_ms=0.0,
                    )
                self.loop.call_soon_threadsafe(self._dispatch, packet)
                frame_index += 1
        except Exception as exc:
            self.health.reader_error = f"Frame reader failed for {self.source}: {exc}"
            update_stream_health(self.source, last_error=str(exc))
            logger.exception(self.health.reader_error)
            self.loop.call_soon_threadsafe(self._ready.set)
        finally:
            if capture is not None:
                capture.release()
            self.loop.call_soon_threadsafe(self._finish)

    def _dispatch(self, packet: FramePacket) -> None:
        for subscription in tuple(self._subscribers):
            subscription._offer(packet)

    def _finish(self) -> None:
        self._ready.set()
        for subscription in tuple(self._subscribers):
            subscription._finish()


class FrameHubRegistry:
    def __init__(self, capture_factory: Callable[[str], Any] | None = None) -> None:
        self._capture_factory = capture_factory
        self._hubs: dict[str, CameraFrameHub] = {}
        self._lock = asyncio.Lock()

    @property
    def hubs(self) -> dict[str, CameraFrameHub]:
        return dict(self._hubs)

    async def subscribe(
        self,
        source: str,
        *,
        policy: DeliveryPolicy,
        queue_size: int | None = None,
    ) -> FrameSubscription:
        async with self._lock:
            hub = self._hubs.get(source)
            if hub is None:
                if self._capture_factory is None:
                    import cv2

                    capture_factory = cv2.VideoCapture
                else:
                    capture_factory = self._capture_factory
                hub = CameraFrameHub(
                    source,
                    registry=self,
                    loop=asyncio.get_running_loop(),
                    capture_factory=capture_factory,
                )
                self._hubs[source] = hub
                hub.start()
            subscription = hub.subscribe(
                policy,
                queue_size or settings.BEHAVIOR_ORDERED_QUEUE_SIZE,
            )
        try:
            await hub.wait_ready()
        except Exception:
            await self.release(subscription)
            raise
        return subscription

    async def release(self, subscription: FrameSubscription) -> None:
        async with self._lock:
            hub = subscription.hub
            stopped = await hub.remove(subscription)
            if stopped and self._hubs.get(hub.source) is hub:
                self._hubs.pop(hub.source, None)

    async def close_all(self) -> None:
        subscriptions = [
            sub
            for hub in self._hubs.values()
            for sub in tuple(hub._subscribers)
        ]
        for subscription in subscriptions:
            await subscription.close()


frame_hubs = FrameHubRegistry()
