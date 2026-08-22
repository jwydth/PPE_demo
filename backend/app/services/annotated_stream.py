"""Server-side composition contracts for one annotated live media stream."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any, Callable, Literal
from urllib.parse import urlsplit, urlunsplit

import cv2
import numpy as np

from app.core.config import settings
from app.services.frame_hub import FrameHubRegistry, FramePacket, frame_hubs
from app.services.stream_health import (
    increment_stream_health,
    mark_stream_event,
    observe_stream_timing,
    update_stream_health,
)

logger = logging.getLogger(__name__)

_publisher_registry_lock = threading.Lock()
_active_publishers: dict[str, "AnnotatedStreamPublisher"] = {}

# Most recently composed frame per source, kept so a freshly-loaded page can
# paint a real still image immediately instead of a black box while its HLS
# player negotiates the stream (see stream_snapshot in routers/streaming.py).
# This is the *annotated* frame, so the poster matches what the live video
# will show once it starts. One frame per camera; the entry is replaced in
# place rather than accumulated.
_latest_frame_lock = threading.Lock()
_latest_frames: dict[str, tuple[np.ndarray, float]] = {}


def latest_annotated_frame(source: str, max_age_seconds: float) -> np.ndarray | None:
    """Return the newest composed frame for `source`, or None if there isn't
    one recent enough. Age-gating matters here: this is safety-monitoring
    footage, so it is better to show nothing than to present a minutes-old
    frame as if it were the current view of the floor."""
    with _latest_frame_lock:
        entry = _latest_frames.get(source)
    if entry is None:
        return None
    frame, captured_at = entry
    if time.monotonic() - captured_at > max_age_seconds:
        return None
    return frame

BehaviorLabel = Literal["unknown", "others", "running", "falling"]


@dataclass(frozen=True, slots=True)
class RenderBox:
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True, slots=True)
class PoseRenderState:
    track_id: int
    bbox: RenderBox
    confidence: float
    behavior: BehaviorLabel = "unknown"
    behavior_score: float = 0.0
    keypoints: tuple[tuple[float, ...], ...] = ()
    synthetic: bool = False


@dataclass(frozen=True, slots=True)
class PpeRenderState:
    source_track_id: int | None
    bbox: RenderBox
    missing_equipment: tuple[str, ...]
    compliant: bool
    role: str | None
    frame_index: int
    stream_epoch: str
    zone_name: str | None = None
    zone_type: str | None = None


@dataclass(frozen=True, slots=True)
class SignRenderState:
    bbox: RenderBox
    class_id: int
    confidence: float
    frame_index: int
    stream_epoch: str


@dataclass(frozen=True, slots=True)
class ComposedPerson:
    pose: PoseRenderState
    ppe: PpeRenderState | None


@dataclass(frozen=True, slots=True)
class RenderSnapshot:
    frame_index: int
    stream_epoch: str
    people: tuple[ComposedPerson, ...]
    signs: tuple[SignRenderState, ...]
    geometry_mode: Literal["exact", "interpolated", "ppe_fallback", "missing"]


@dataclass(frozen=True, slots=True)
class RenderFeatures:
    ppe: bool = True
    zone: bool = True
    behavior: bool = True
    sign: bool = True

    @property
    def has_person_overlay(self) -> bool:
        return self.ppe or self.zone or self.behavior


@dataclass(frozen=True, slots=True)
class _PoseFrame:
    frame_index: int
    stream_epoch: str
    people: tuple[PoseRenderState, ...]


class AnnotatedStateStore:
    """Bounded, epoch-isolated AI state used by the presentation compositor."""

    def __init__(self, *, fps: float) -> None:
        self.fps = max(float(fps), 1.0)
        self._lock = threading.RLock()
        self._pose_frames: dict[int, _PoseFrame] = {}
        self._pose_order: deque[int] = deque()
        self._ppe_frames: deque[tuple[int, str, tuple[PpeRenderState, ...]]] = deque()
        self._sign_frames: deque[tuple[int, str, tuple[SignRenderState, ...]]] = deque()
        self._epoch: str | None = None
        self._features = RenderFeatures()
        self._history_frames = max(
            settings.ANNOTATED_STREAM_QUEUE_SIZE * 2,
            int(self.fps * (settings.ANNOTATED_STREAM_DELAY_SECONDS + 3.0)),
        )

    def update_pose(self, packet: FramePacket, detections: list[dict[str, Any]]) -> None:
        people = tuple(_pose_state(item) for item in detections)
        with self._lock:
            if not self._features.behavior:
                return
            self._ensure_epoch(packet.stream_epoch)
            previous = self._latest_pose_before(packet.frame_index)
            if previous is not None:
                self._repair_pose_gap(previous, packet.frame_index, people, packet.stream_epoch)
            self._store_pose(_PoseFrame(packet.frame_index, packet.stream_epoch, people))
            self._remap_ppe_frame(packet.frame_index, packet.stream_epoch, people)
            self._prune(packet.frame_index)

    def update_ppe(self, packet: FramePacket, frames: list[Any]) -> None:
        states = tuple(_ppe_state(frame, packet) for frame in frames)
        with self._lock:
            if not (self._features.ppe or self._features.zone):
                return
            self._ensure_epoch(packet.stream_epoch)
            pose_frame = self._pose_frames.get(packet.frame_index)
            if pose_frame is not None:
                states = tuple(
                    replace(
                        state,
                        source_track_id=_matching_pose_track(
                            state.bbox,
                            pose_frame.people,
                        ),
                    )
                    for state in states
                )
            self._ppe_frames.append((packet.frame_index, packet.stream_epoch, states))
            self._prune(packet.frame_index)

    def update_signs(self, packet: FramePacket, signs: list[dict[str, Any]]) -> None:
        states = tuple(
            SignRenderState(
                bbox=_box(item["bbox"]),
                class_id=int(item["class_id"]),
                confidence=float(item["conf"]),
                frame_index=packet.frame_index,
                stream_epoch=packet.stream_epoch,
            )
            for item in signs
        )
        with self._lock:
            if not self._features.sign:
                return
            self._ensure_epoch(packet.stream_epoch)
            self._sign_frames.append((packet.frame_index, packet.stream_epoch, states))
            self._prune(packet.frame_index)

    def snapshot(
        self,
        packet: FramePacket,
        features: RenderFeatures | None = None,
    ) -> RenderSnapshot:
        with self._lock:
            if features is not None:
                self._set_features(features)
            active = self._features
            if packet.stream_epoch != self._epoch:
                return RenderSnapshot(
                    packet.frame_index, packet.stream_epoch, (), (), "missing"
                )
            pose_frame = self._pose_frames.get(packet.frame_index)
            geometry_mode: Literal[
                "exact", "interpolated", "ppe_fallback", "missing"
            ] = "missing"
            people: tuple[PoseRenderState, ...] = ()
            if active.behavior and pose_frame is not None:
                people = pose_frame.people
                geometry_mode = (
                    "interpolated" if any(item.synthetic for item in people) else "exact"
                )
            ppe = self._latest_ppe(packet) if active.ppe or active.zone else ()
            if active.has_person_overlay and not people and ppe:
                people = tuple(
                    PoseRenderState(
                        track_id=item.source_track_id or -(index + 1),
                        bbox=item.bbox,
                        confidence=1.0,
                    )
                    for index, item in enumerate(ppe)
                )
                geometry_mode = "ppe_fallback"
            composed = tuple(
                ComposedPerson(pose=item, ppe=_match_ppe(item, ppe))
                for item in people
            )
            return RenderSnapshot(
                frame_index=packet.frame_index,
                stream_epoch=packet.stream_epoch,
                people=composed,
                signs=self._latest_signs(packet) if active.sign else (),
                geometry_mode=geometry_mode,
            )

    def set_features(self, features: RenderFeatures) -> None:
        with self._lock:
            self._set_features(features)

    def _set_features(self, features: RenderFeatures) -> None:
        previous = self._features
        self._features = features
        if previous.behavior and not features.behavior:
            self._pose_frames.clear()
            self._pose_order.clear()
        if (previous.ppe or previous.zone) and not (features.ppe or features.zone):
            self._ppe_frames.clear()
        if previous.sign and not features.sign:
            self._sign_frames.clear()

    def match_pose_track(self, packet: FramePacket, bbox: Any) -> int | None:
        with self._lock:
            if packet.stream_epoch != self._epoch:
                return None
            pose_frame = self._pose_frames.get(packet.frame_index)
            if pose_frame is None:
                return None
            return _matching_pose_track(_box(bbox), pose_frame.people)

    def _ensure_epoch(self, stream_epoch: str) -> None:
        if self._epoch == stream_epoch:
            return
        self._epoch = stream_epoch
        self._pose_frames.clear()
        self._pose_order.clear()
        self._ppe_frames.clear()
        self._sign_frames.clear()

    def _store_pose(self, frame: _PoseFrame) -> None:
        if frame.frame_index not in self._pose_frames:
            self._pose_order.append(frame.frame_index)
        self._pose_frames[frame.frame_index] = frame

    def _latest_pose_before(self, frame_index: int) -> _PoseFrame | None:
        candidates = [index for index in self._pose_order if index < frame_index]
        return self._pose_frames[candidates[-1]] if candidates else None

    def _repair_pose_gap(
        self,
        previous: _PoseFrame,
        current_index: int,
        current_people: tuple[PoseRenderState, ...],
        stream_epoch: str,
    ) -> None:
        missing = current_index - previous.frame_index - 1
        if missing < 1 or missing > settings.BEHAVIOR_POSE_REPAIR_MAX_GAP:
            return
        previous_by_track = {item.track_id: item for item in previous.people}
        current_by_track = {item.track_id: item for item in current_people}
        common_tracks = previous_by_track.keys() & current_by_track.keys()
        for offset in range(1, missing + 1):
            alpha = offset / (missing + 1)
            repaired = tuple(
                _interpolate_pose(
                    previous_by_track[track_id],
                    current_by_track[track_id],
                    alpha,
                )
                for track_id in common_tracks
                if _safe_geometry_interpolation(
                    previous_by_track[track_id], current_by_track[track_id]
                )
            )
            self._store_pose(
                _PoseFrame(previous.frame_index + offset, stream_epoch, repaired)
            )

    def _latest_ppe(self, packet: FramePacket) -> tuple[PpeRenderState, ...]:
        for frame_index, epoch, states in reversed(self._ppe_frames):
            age = packet.frame_index - frame_index
            if epoch == packet.stream_epoch and 0 <= age <= settings.ANNOTATED_PPE_TTL_FRAMES:
                return states
        return ()

    def _remap_ppe_frame(
        self,
        frame_index: int,
        stream_epoch: str,
        people: tuple[PoseRenderState, ...],
    ) -> None:
        for index in range(len(self._ppe_frames) - 1, -1, -1):
            ppe_index, epoch, states = self._ppe_frames[index]
            if ppe_index < frame_index:
                return
            if ppe_index != frame_index or epoch != stream_epoch:
                continue
            self._ppe_frames[index] = (
                ppe_index,
                epoch,
                tuple(
                    replace(
                        state,
                        source_track_id=_matching_pose_track(state.bbox, people),
                    )
                    for state in states
                ),
            )
            return

    def _latest_signs(self, packet: FramePacket) -> tuple[SignRenderState, ...]:
        ttl_frames = int(round(settings.ANNOTATED_SIGN_TTL_SECONDS * self.fps))
        for frame_index, epoch, states in reversed(self._sign_frames):
            age = packet.frame_index - frame_index
            if epoch == packet.stream_epoch and 0 <= age <= ttl_frames:
                return states
        return ()

    def _prune(self, current_frame_index: int) -> None:
        cutoff = current_frame_index - self._history_frames
        while self._pose_order and self._pose_order[0] < cutoff:
            self._pose_frames.pop(self._pose_order.popleft(), None)
        while self._ppe_frames and self._ppe_frames[0][0] < cutoff:
            self._ppe_frames.popleft()
        while self._sign_frames and self._sign_frames[0][0] < cutoff:
            self._sign_frames.popleft()


def annotated_rtsp_url(source: str) -> str:
    parsed = urlsplit(source)
    path_name = PurePosixPath(parsed.path).name or "stream"
    base = urlsplit(settings.ANNOTATED_RTSP_BASE_URL)
    output_path = f"/{path_name}{settings.ANNOTATED_PATH_SUFFIX}"
    return urlunsplit((base.scheme, base.netloc, output_path, "", ""))


def annotated_hls_path(source: str) -> str:
    parsed = urlsplit(source)
    path_name = PurePosixPath(parsed.path).name or "stream"
    return f"{path_name}{settings.ANNOTATED_PATH_SUFFIX}"


class RawFramePublisher:
    """Write CFR BGR frames to one MediaMTX RTSP publisher process."""

    def __init__(self, *, target_url: str, width: int, height: int, fps: float) -> None:
        self.target_url = target_url
        self.width = width
        self.height = height
        self.fps = max(float(fps), 1.0)
        self.encoder = self._select_encoder()
        self.process: subprocess.Popen | None = None
        self.restarts = 0

    def command(self) -> list[str]:
        common = [
            settings.ANNOTATED_FFMPEG_PATH,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-video_size",
            f"{self.width}x{self.height}",
            "-framerate",
            f"{self.fps:.6f}",
            "-i",
            "pipe:0",
            "-an",
        ]
        if self.encoder == "h264_nvenc":
            codec = [
                "-c:v", "h264_nvenc",
                "-preset", "p4",
                "-tune", "ll",
                "-rc", "constqp",
                "-qp", "23",
            ]
        else:
            codec = [
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-tune", "zerolatency",
                "-crf", "23",
            ]
        return common + codec + [
            "-profile:v", "main",
            "-pix_fmt", "yuv420p",
            "-g", str(max(1, round(self.fps))),
            "-keyint_min", str(max(1, round(self.fps))),
            "-sc_threshold", "0",
            "-bf", "0",
            "-fps_mode", "cfr",
            "-f", "rtsp",
            "-rtsp_transport", "tcp",
            self.target_url,
        ]

    def write(self, frame: np.ndarray) -> bool:
        if self.process is None or self.process.poll() is not None:
            self._start()
        try:
            assert self.process is not None and self.process.stdin is not None
            self.process.stdin.write(frame.tobytes())
            return True
        except (BrokenPipeError, OSError):
            self._stop_process()
            if self.encoder == "h264_nvenc":
                logger.warning("NVENC annotated publisher failed; falling back to libx264")
                self.encoder = "libx264"
            self.restarts += 1
            self._start()
            try:
                assert self.process is not None and self.process.stdin is not None
                self.process.stdin.write(frame.tobytes())
                return True
            except (BrokenPipeError, OSError):
                self._stop_process()
                return False

    def close(self) -> None:
        self._stop_process()

    def _select_encoder(self) -> str:
        configured = settings.ANNOTATED_ENCODER
        if configured != "auto":
            return configured
        executable = shutil.which(settings.ANNOTATED_FFMPEG_PATH)
        if executable is None:
            return "libx264"
        try:
            probe = subprocess.run(
                [executable, "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return "h264_nvenc" if "h264_nvenc" in probe.stdout else "libx264"
        except (OSError, subprocess.SubprocessError):
            return "libx264"

    def _start(self) -> None:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            self.command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            creationflags=creationflags,
        )

    def _stop_process(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()


class AnnotatedStreamPublisher:
    """Delay, compose, and publish every ordered source frame at its deadline."""

    def __init__(
        self,
        *,
        source: str,
        fps: float,
        store: AnnotatedStateStore,
        hubs: FrameHubRegistry | None = None,
        feature_flags: Callable[[], tuple[bool, bool, bool, bool]] | None = None,
        idle_grace_seconds: float = 0.0,
    ) -> None:
        self.source = source
        self.fps = max(float(fps), 1.0)
        self.store = store
        self.hubs = hubs or frame_hubs
        self.feature_flags = feature_flags
        self.idle_grace_seconds = idle_grace_seconds
        self.output_url = annotated_rtsp_url(source)
        self._task: asyncio.Task[None] | None = None
        self._publisher: RawFramePublisher | None = None
        # Pending "no viewer attached anymore" teardown — see release() below.
        self._idle_release_task: asyncio.Task[None] | None = None

    def start(self) -> bool:
        """Start once per output URL and return whether this instance owns it."""
        if self._task is not None and not self._task.done():
            return True
        with _publisher_registry_lock:
            current = _active_publishers.get(self.output_url)
            if current is not None and current is not self:
                current_task = current._task
                if current_task is not None and not current_task.done():
                    logger.warning(
                        "Annotated output %s already has an active publisher",
                        self.output_url,
                    )
                    return False
            _active_publishers[self.output_url] = self
        self._task = asyncio.create_task(self._run())
        update_stream_health(
            self.source,
            annotated_publisher_active=True,
            annotated_output_label=annotated_hls_path(self.source),
        )
        return True

    async def stop(self) -> None:
        if self._idle_release_task is not None:
            self._idle_release_task.cancel()
            self._idle_release_task = None
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._publisher is not None:
            await asyncio.to_thread(self._publisher.close)
            self._publisher = None
        self._release_ownership()

    async def release(self) -> None:
        """Detach the current viewer from this publisher.

        With no grace period configured this is identical to stop(). With one
        configured, the ffmpeg process, RTSP publish connection, and HLS
        stream are left running for that long in case a new connection
        reattaches (acquire_publisher() below) — e.g. a page reload — instead
        of tearing everything down and paying a fresh RTSP handshake plus HLS
        restart on every reconnect.
        """
        if self.idle_grace_seconds <= 0:
            await self.stop()
            return
        if self._idle_release_task is not None and not self._idle_release_task.done():
            return
        self._idle_release_task = asyncio.create_task(
            self._idle_release(self.idle_grace_seconds)
        )

    async def _idle_release(self, grace: float) -> None:
        try:
            await asyncio.sleep(grace)
        except asyncio.CancelledError:
            return
        with _publisher_registry_lock:
            if _active_publishers.get(self.output_url) is not self:
                return
        await self.stop()

    def _reattach(
        self,
        *,
        fps: float,
        store: AnnotatedStateStore,
        feature_flags: Callable[[], tuple[bool, bool, bool, bool]] | None,
    ) -> None:
        """Rebind a new connection's fps/state-store/feature-flags onto this
        already-running publisher, cancelling any pending idle teardown. The
        publish loop reads self.store/self.feature_flags fresh on every
        packet (see _publish_packet), so this takes effect on the very next
        frame — no task restart, no ffmpeg restart."""
        if self._idle_release_task is not None:
            self._idle_release_task.cancel()
            self._idle_release_task = None
        self.fps = max(float(fps), 1.0)
        self.store = store
        self.feature_flags = feature_flags

    async def _run(self) -> None:
        subscription = await self.hubs.subscribe(
            self.source,
            policy="ordered",
            queue_size=settings.ANNOTATED_STREAM_QUEUE_SIZE,
        )
        try:
            while True:
                packet = await subscription.get()
                if packet is None:
                    return
                try:
                    await self._publish_packet(packet, subscription)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    increment_stream_health(self.source, annotated_dropped_frames=1)
                    update_stream_health(
                        self.source,
                        last_error=f"Annotated frame failed: {exc}",
                    )
                    logger.warning(
                        "Annotated frame %s failed for %s: %s",
                        packet.frame_index,
                        self.source,
                        exc,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            update_stream_health(
                self.source,
                last_error=f"Annotated publisher failed: {exc}",
            )
            logger.exception("Annotated publisher failed for %s", self.source)
        finally:
            await subscription.close()
            if self._publisher is not None:
                await asyncio.to_thread(self._publisher.close)
                self._publisher = None
            update_stream_health(
                self.source,
                annotated_publisher_active=False,
                annotated_queue_depth=0,
            )
            self._release_ownership()

    async def _publish_packet(self, packet: FramePacket, subscription: Any) -> None:
        deadline = packet.captured_monotonic + settings.ANNOTATED_STREAM_DELAY_SECONDS
        wait_seconds = deadline - time.monotonic()
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
        late_ms = max(0.0, (time.monotonic() - deadline) * 1000.0)
        if late_ms > 1000.0 / self.fps:
            increment_stream_health(self.source, annotated_deadline_misses=1)
        compose_started = time.perf_counter()
        features = self._render_features()
        snapshot = self.store.snapshot(packet, features)
        frame = await asyncio.to_thread(
            render_annotated_frame,
            packet.image,
            snapshot,
            features,
        )
        compose_ms = (time.perf_counter() - compose_started) * 1000.0
        # Publish this frame as the source's poster before it goes to the
        # encoder, so a page that loads a moment from now has something real
        # to display while its player starts up.
        with _latest_frame_lock:
            _latest_frames[self.source] = (frame, time.monotonic())
        increment_stream_health(self.source, annotated_composed_frames=1)
        observe_stream_timing(self.source, "annotated_compose", compose_ms)
        observe_stream_timing(self.source, "annotated_deadline_late", late_ms)
        update_stream_health(
            self.source,
            annotated_queue_depth=subscription.queue.qsize(),
            annotated_queue_dropped_frames=subscription.dropped_frames,
            annotated_publisher_active=True,
            annotated_output_label=annotated_hls_path(self.source),
        )
        if self._publisher is None:
            height, width = frame.shape[:2]
            self._publisher = RawFramePublisher(
                target_url=self.output_url,
                width=width,
                height=height,
                fps=self.fps,
            )
            update_stream_health(
                self.source,
                annotated_encoder=self._publisher.encoder,
            )
        publish_started = time.perf_counter()
        published = await asyncio.to_thread(self._publisher.write, frame)
        publish_ms = (time.perf_counter() - publish_started) * 1000.0
        observe_stream_timing(self.source, "annotated_publish_write", publish_ms)
        update_stream_health(
            self.source,
            annotated_encoder_restarts=self._publisher.restarts,
            annotated_encoder=self._publisher.encoder,
        )
        if published:
            increment_stream_health(self.source, annotated_published_frames=1)
            mark_stream_event(self.source, "annotated_output")
        else:
            increment_stream_health(self.source, annotated_dropped_frames=1)

    def _release_ownership(self) -> None:
        with _publisher_registry_lock:
            if _active_publishers.get(self.output_url) is self:
                _active_publishers.pop(self.output_url, None)

    def _render_features(self) -> RenderFeatures:
        if self.feature_flags is None:
            return RenderFeatures()
        ppe, zone, behavior, sign = self.feature_flags()
        return RenderFeatures(
            ppe=bool(ppe),
            zone=bool(zone),
            behavior=bool(behavior),
            sign=bool(sign),
        )


def acquire_publisher(
    *,
    source: str,
    fps: float,
    store: AnnotatedStateStore,
    feature_flags: Callable[[], tuple[bool, bool, bool, bool]] | None = None,
    hubs: FrameHubRegistry | None = None,
) -> AnnotatedStreamPublisher:
    """Get the publisher for this camera's annotated output, reusing one
    that's still running (including one idling out its post-release grace
    window) instead of always starting a fresh ffmpeg process. Call
    .release() (not .stop()) when the caller's connection ends, so a quick
    reconnect (e.g. a page reload) can reattach here instead of forcing a
    full RTSP-publish + HLS restart."""
    output_url = annotated_rtsp_url(source)
    with _publisher_registry_lock:
        existing = _active_publishers.get(output_url)
        if existing is not None and existing._task is not None and not existing._task.done():
            existing._reattach(fps=fps, store=store, feature_flags=feature_flags)
            return existing
    publisher = AnnotatedStreamPublisher(
        source=source,
        fps=fps,
        store=store,
        hubs=hubs,
        feature_flags=feature_flags,
        idle_grace_seconds=settings.ANNOTATED_PUBLISHER_IDLE_GRACE_SECONDS,
    )
    publisher.start()
    return publisher


async def close_all_publishers() -> None:
    """Force-stop every active/idling annotated publisher, bypassing their
    grace window. Use only on process shutdown — the grace window otherwise
    keeps ffmpeg processes running past a request's lifetime on purpose."""
    for publisher in list(_active_publishers.values()):
        await publisher.stop()


def render_annotated_frame(
    source_frame: np.ndarray,
    snapshot: RenderSnapshot,
    features: RenderFeatures | None = None,
) -> np.ndarray:
    active = features or RenderFeatures()
    frame = source_frame.copy()
    for person in snapshot.people:
        pose = person.pose
        violations = _person_violation_labels(person, active)
        color = (0, 0, 255) if violations else (0, 200, 0)
        box = pose.bbox
        cv2.rectangle(
            frame,
            (round(box.x1), round(box.y1)),
            (round(box.x2), round(box.y2)),
            color,
            2,
        )
        labels = violations or ["Compliant"]
        _draw_label(frame, labels, round(box.x1), round(box.y1), color)
    for sign in snapshot.signs:
        box = sign.bbox
        color = (255, 200, 0)
        cv2.rectangle(
            frame,
            (round(box.x1), round(box.y1)),
            (round(box.x2), round(box.y2)),
            color,
            2,
        )
        name = settings.SIGN_CLASS_NAMES.get(sign.class_id, f"Sign {sign.class_id}")
        _draw_label(frame, [f"{name} {sign.confidence:.0%}"], round(box.x1), round(box.y1), color)
    return frame


def _person_violation_labels(
    person: ComposedPerson,
    features: RenderFeatures,
) -> list[str]:
    labels: list[str] = []
    if features.ppe and person.ppe is not None and person.ppe.missing_equipment:
        labels.append(f"PPE: {_ppe_violation_label(person.ppe.missing_equipment)}")
    if features.zone and person.ppe is not None and person.ppe.zone_type:
        zone_label = {
            "RESTRICTED": "Restricted zone",
            "SLIPPERY": "Slippery area",
            "WALKWAY": "Walkway violation",
        }.get(person.ppe.zone_type, person.ppe.zone_type.replace("_", " ").title())
        if person.ppe.zone_name:
            zone_label = f"{zone_label} - {person.ppe.zone_name}"
        labels.append(f"Zone: {zone_label}")
    if features.behavior and person.pose.behavior in {"running", "falling"}:
        labels.append(f"Behavior: {person.pose.behavior.title()}")
    return labels


def _ppe_violation_label(missing_equipment: tuple[str, ...]) -> str:
    normalized = {item.strip().lower() for item in missing_equipment}
    has_helmet = bool(normalized & {"helmet", "hardhat", "safety helmet"})
    has_vest = bool(normalized & {"vest", "safety vest"})
    has_coverall = "cleaning coverall" in normalized
    has_uniform = "role uniform" in normalized
    if has_helmet and has_vest:
        return "Missing Helmet and Vest"
    if has_helmet and has_coverall:
        return "Missing Helmet and Cleaning Coverall"
    if has_helmet and has_uniform:
        return "Missing Helmet and Role Uniform"
    if has_helmet:
        return "Missing Safety Helmet"
    if has_vest:
        return "Missing Safety Vest"
    if has_coverall:
        return "Missing Cleaning Coverall"
    if has_uniform:
        return "Missing Role Uniform"
    return "Missing " + " and ".join(item.strip() for item in missing_equipment)


def _draw_label(
    frame: np.ndarray,
    labels: list[str],
    x: int,
    y: int,
    color: tuple[int, int, int],
) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.45
    line_height = 18
    width = max(cv2.getTextSize(label, font, scale, 1)[0][0] for label in labels) + 10
    height = line_height * len(labels) + 6
    top = max(0, y - height)
    left = max(0, min(x, frame.shape[1] - width))
    cv2.rectangle(frame, (left, top), (left + width, top + height), (15, 23, 42), -1)
    cv2.rectangle(frame, (left, top), (left + width, top + height), color, 1)
    for index, label in enumerate(labels):
        cv2.putText(
            frame,
            label,
            (left + 5, top + 15 + index * line_height),
            font,
            scale,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )


def _box(value: Any) -> RenderBox:
    if isinstance(value, dict):
        return RenderBox(*(float(value[key]) for key in ("x1", "y1", "x2", "y2")))
    if hasattr(value, "model_dump"):
        return _box(value.model_dump())
    return RenderBox(*(float(item) for item in value))


def _pose_state(item: dict[str, Any]) -> PoseRenderState:
    keypoints = tuple(tuple(float(value) for value in point) for point in item.get("keypoints") or ())
    return PoseRenderState(
        track_id=int(item["track_id"]),
        bbox=_box(item["bbox"]),
        confidence=float(item.get("person_confidence", 0.0)),
        behavior=str(item.get("status", "unknown")),  # type: ignore[arg-type]
        behavior_score=float(item.get("score", 0.0)),
        keypoints=keypoints,
    )


def _ppe_state(frame: Any, packet: FramePacket) -> PpeRenderState:
    value = frame.model_dump() if hasattr(frame, "model_dump") else frame
    return PpeRenderState(
        source_track_id=value.get("track_id"),
        bbox=_box(value["bbox"]),
        missing_equipment=tuple(value.get("missing_equipment") or ()),
        compliant=bool(value.get("compliant", True)),
        role=value.get("role"),
        frame_index=packet.frame_index,
        stream_epoch=packet.stream_epoch,
        zone_name=value.get("zone_name"),
        zone_type=value.get("zone_type"),
    )


def _interpolate_pose(
    left: PoseRenderState,
    right: PoseRenderState,
    alpha: float,
) -> PoseRenderState:
    def value(start: float, end: float) -> float:
        return start + (end - start) * alpha

    bbox = RenderBox(
        value(left.bbox.x1, right.bbox.x1),
        value(left.bbox.y1, right.bbox.y1),
        value(left.bbox.x2, right.bbox.x2),
        value(left.bbox.y2, right.bbox.y2),
    )
    keypoints: tuple[tuple[float, ...], ...] = ()
    if len(left.keypoints) == len(right.keypoints):
        keypoints = tuple(
            tuple(value(a, b) for a, b in zip(lpoint, rpoint, strict=True))
            for lpoint, rpoint in zip(left.keypoints, right.keypoints, strict=True)
        )
    return replace(left, bbox=bbox, keypoints=keypoints, synthetic=True)


def _safe_geometry_interpolation(
    left: PoseRenderState,
    right: PoseRenderState,
) -> bool:
    width = max(1.0, left.bbox.x2 - left.bbox.x1)
    height = max(1.0, left.bbox.y2 - left.bbox.y1)
    left_center = ((left.bbox.x1 + left.bbox.x2) / 2, (left.bbox.y1 + left.bbox.y2) / 2)
    right_center = ((right.bbox.x1 + right.bbox.x2) / 2, (right.bbox.y1 + right.bbox.y2) / 2)
    shift = np.hypot(right_center[0] - left_center[0], right_center[1] - left_center[1])
    return shift / np.hypot(width, height) <= settings.BEHAVIOR_POSE_REPAIR_MAX_CENTER_SHIFT_RATIO


def _iou(left: RenderBox, right: RenderBox) -> float:
    width = max(0.0, min(left.x2, right.x2) - max(left.x1, right.x1))
    height = max(0.0, min(left.y2, right.y2) - max(left.y1, right.y1))
    intersection = width * height
    union = (
        (left.x2 - left.x1) * (left.y2 - left.y1)
        + (right.x2 - right.x1) * (right.y2 - right.y1)
        - intersection
    )
    return intersection / union if union > 0 else 0.0


def _match_ppe(
    pose: PoseRenderState,
    candidates: tuple[PpeRenderState, ...],
) -> PpeRenderState | None:
    same_track = next(
        (item for item in candidates if item.source_track_id == pose.track_id),
        None,
    )
    if same_track is not None:
        return same_track
    return _match_ppe_by_box(pose.bbox, candidates)


def _match_ppe_by_box(
    pose_box: RenderBox,
    candidates: tuple[PpeRenderState, ...],
) -> PpeRenderState | None:
    if not candidates:
        return None
    best = max(candidates, key=lambda item: _iou(pose_box, item.bbox))
    return best if _iou(pose_box, best.bbox) >= settings.ANNOTATED_PPE_MATCH_IOU else None


def _matching_pose_track(
    ppe_box: RenderBox,
    candidates: tuple[PoseRenderState, ...],
) -> int | None:
    if not candidates:
        return None
    best = max(candidates, key=lambda item: _iou(ppe_box, item.bbox))
    return best.track_id if _iou(ppe_box, best.bbox) >= settings.ANNOTATED_PPE_MATCH_IOU else None
