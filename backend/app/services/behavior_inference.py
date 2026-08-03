"""Finite, batched CUDA pose inference with per-camera tracking state."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

import numpy as np

from app.core.config import settings
from app.services.fall_detector import FallDetector
from app.services.inference_coordination import gpu_inference_lock

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BehaviorInferenceHealth:
    camera_key: str
    submitted_frames: int = 0
    completed_frames: int = 0
    failed_frames: int = 0
    batches: int = 0
    last_batch_size: int = 0
    last_queue_wait_ms: float = 0.0
    last_pose_ms: float = 0.0
    last_tracking_ms: float = 0.0
    last_total_ms: float = 0.0


@dataclass(slots=True)
class _InferenceRequest:
    camera_key: str
    image: np.ndarray
    submitted_at: float
    future: asyncio.Future


class ExplicitReID:
    """Ultralytics ReID encoder with explicit device and precision."""

    def __init__(self, model_path: Path, *, device: str, half: bool) -> None:
        import torch
        from ultralytics import YOLO

        self.device = device
        self.half = bool(half and device.startswith("cuda"))
        self.model = YOLO(str(model_path))
        self.embed_layer = len(self.model.model.model) - 2
        # Initialize AutoBackend on the requested device without downloading or
        # reading an arbitrary default image. A small multi-crop warm-up also
        # pays the first batched-kernel cost before ordered capture begins.
        dummy_crop = np.zeros((64, 64, 3), dtype=np.uint8)
        reid_warm_batch = max(4, settings.BEHAVIOR_BATCH_MAX_SIZE * 2)
        self.model.predict(
            [dummy_crop] * reid_warm_batch,
            embed=[self.embed_layer],
            device=device,
            half=self.half,
            verbose=False,
            save=False,
        )
        actual = str(self.model.predictor.device)
        if device.startswith("cuda") and not actual.startswith("cuda"):
            raise RuntimeError(f"ReID requested {device} but initialized on {actual}.")
        self._torch = torch

    def __call__(self, image: np.ndarray, detections: np.ndarray) -> list[np.ndarray]:
        return self.encode_batch([(image, detections)])[0]

    def encode_batch(
        self,
        items: list[tuple[np.ndarray, np.ndarray]],
    ) -> list[list[np.ndarray]]:
        """Encode crops from every ready camera in one CUDA forward pass."""
        from ultralytics.utils.ops import xywh2xyxy
        from ultralytics.utils.plotting import save_one_box

        crops: list[np.ndarray] = []
        counts: list[int] = []
        for image, detections in items:
            counts.append(len(detections))
            if len(detections) == 0:
                continue
            boxes = xywh2xyxy(self._torch.from_numpy(detections[:, :4]))
            crops.extend(save_one_box(box, image, save=False) for box in boxes)
        if not crops:
            return [[] for _ in items]
        features = self.model.predictor(crops)
        if len(features) != len(crops) and features[0].shape[0] == len(crops):
            features = features[0]
        arrays = [feature.cpu().numpy().reshape(-1) for feature in features]
        grouped: list[list[np.ndarray]] = []
        offset = 0
        for count in counts:
            grouped.append(arrays[offset : offset + count])
            offset += count
        return grouped

    def close(self) -> None:
        predictor = getattr(self.model, "predictor", None)
        dataset = getattr(predictor, "dataset", None)
        close = getattr(dataset, "close", None)
        if callable(close):
            close()


class _CameraTracker:
    def __init__(
        self,
        detector: FallDetector,
        *,
        reid_encoder: ExplicitReID | None = None,
    ) -> None:
        from ultralytics.trackers.bot_sort import BOTSORT
        from ultralytics.utils import IterableSimpleNamespace, YAML

        config = dict(YAML.load(str(detector.tracker_path)))
        use_reid = bool(config.get("with_reid", False))
        config["with_reid"] = False  # avoid the implicit-device encoder
        config["model"] = str(detector.reid_model_path)
        config["gmc_method"] = settings.BEHAVIOR_GMC_METHOD
        args = IterableSimpleNamespace(**config)
        self.tracker = BOTSORT(args=args)
        self.tracker.args.with_reid = use_reid
        self._owns_encoder = bool(use_reid and reid_encoder is None)
        self._reid_enabled = use_reid
        self._frames_seen = 0
        self._feature_dim: int | None = None
        self.tracker.encoder = reid_encoder
        if use_reid and self.tracker.encoder is None:
            self.tracker.encoder = ExplicitReID(
                detector.reid_model_path,
                device=detector.device,
                half=settings.BEHAVIOR_REID_HALF,
            )

    def update(
        self,
        result: Any,
        embeddings: list[np.ndarray] | None = None,
    ) -> Any:
        import torch

        detections = result.boxes.cpu().numpy()
        if embeddings:
            embeddings = [
                np.asarray(feature, dtype=np.float32).reshape(-1)
                for feature in embeddings
            ]
            self._feature_dim = int(embeddings[0].size)
            self._fill_missing_track_features()
        previous_encoder = self.tracker.encoder
        previous_with_reid = self.tracker.args.with_reid
        self.tracker.args.with_reid = bool(self._reid_enabled and embeddings is not None)
        if embeddings is not None:
            self.tracker.encoder = _PrecomputedReID(detections.xywh, embeddings)
        try:
            tracks = self.tracker.update(detections, result.orig_img, None)
        finally:
            self.tracker.encoder = previous_encoder
            self.tracker.args.with_reid = previous_with_reid
            self._frames_seen += 1
        self._fill_missing_track_features()
        if len(tracks) == 0:
            return result[:0]
        indices = tracks[:, -1].astype(int)
        tracked = result[indices]
        tracked.update(boxes=torch.as_tensor(tracks[:, :-1]))
        return tracked

    def requires_reid(self) -> bool:
        return bool(
            self._reid_enabled
            and self._frames_seen % settings.BEHAVIOR_REID_INTERVAL_FRAMES == 0
        )

    def _fill_missing_track_features(self) -> None:
        """Keep BoT-SORT feature arrays homogeneous between ReID refreshes."""
        if self._feature_dim is None:
            return
        for track in (
            *self.tracker.tracked_stracks,
            *self.tracker.lost_stracks,
            *self.tracker.removed_stracks,
        ):
            if track.smooth_feat is None:
                placeholder = np.zeros(self._feature_dim, dtype=np.float32)
                track.curr_feat = placeholder.copy()
                track.smooth_feat = placeholder

    def close(self) -> None:
        self.tracker.reset()
        if self._owns_encoder:
            close = getattr(self.tracker.encoder, "close", None)
            if callable(close):
                close()


class _PrecomputedReID:
    """Maps BoT-SORT's filtered boxes to embeddings computed for the frame."""

    def __init__(self, xywh: np.ndarray, embeddings: list[np.ndarray]) -> None:
        self.xywh = np.asarray(xywh, dtype=np.float32)
        self.embeddings = embeddings

    def __call__(self, _image: np.ndarray, detections: np.ndarray) -> list[np.ndarray]:
        if not len(detections):
            return []
        selected: list[np.ndarray] = []
        for box in np.asarray(detections[:, :4], dtype=np.float32):
            index = int(np.argmin(np.sum(np.abs(self.xywh - box), axis=1)))
            selected.append(self.embeddings[index])
        return selected


class BehaviorInferenceScheduler:
    """Micro-batches ready camera frames through one finite pose predictor.

    The scheduler has no camera concurrency limit. ``BEHAVIOR_BATCH_MAX_SIZE``
    only bounds a single GPU batch; remaining requests are handled by the next
    batch. Every camera owns a distinct BoT-SORT/ReID state machine.
    """

    def __init__(
        self,
        detector: FallDetector,
        *,
        batch_processor: Callable[[list[_InferenceRequest]], list[Any]] | None = None,
        tracker_factory: Callable[[FallDetector], Any] | None = None,
    ) -> None:
        self.detector = detector
        self._requests: asyncio.Queue[_InferenceRequest] = asyncio.Queue()
        self._runner: asyncio.Task[None] | None = None
        self._state_lock = asyncio.Lock()
        self._trackers: dict[str, _CameraTracker] = {}
        self._health: dict[str, BehaviorInferenceHealth] = {}
        self._batch_processor = batch_processor
        self._tracker_factory = tracker_factory
        self._shared_reid: ExplicitReID | None = None
        self._pose_warmed = False
        self._start_waiters: set[asyncio.Future] = set()
        self._start_release_task: asyncio.Task[None] | None = None
        self._closing = False

    @property
    def camera_keys(self) -> set[str]:
        return set(self._trackers)

    def health_snapshot(self) -> dict[str, BehaviorInferenceHealth]:
        return {
            key: replace(value)
            for key, value in self._health.items()
        }

    async def prepare_camera(self, camera_key: str) -> None:
        """Warm GPU assets and create isolated state before capture starts."""
        if self._closing:
            raise RuntimeError("Behavior inference scheduler is shutting down.")
        async with self._state_lock:
            await asyncio.to_thread(self._prepare_camera_sync, camera_key)

    async def await_start_cohort(self) -> None:
        """Release cameras enabled together onto ordered capture together."""
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._start_waiters.add(future)
        if self._start_release_task is None or self._start_release_task.done():
            self._start_release_task = asyncio.create_task(
                self._release_start_cohort(),
                name="behavior-start-cohort",
            )
        try:
            await future
        finally:
            self._start_waiters.discard(future)

    async def infer(self, camera_key: str, image: np.ndarray) -> Any:
        if self._closing:
            raise RuntimeError("Behavior inference scheduler is shutting down.")
        if self._runner is None or self._runner.done():
            self._runner = asyncio.create_task(self._run(), name="behavior-batch-scheduler")
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        health = self._health.setdefault(camera_key, BehaviorInferenceHealth(camera_key))
        health.submitted_frames += 1
        await self._requests.put(
            _InferenceRequest(camera_key, image, time.perf_counter(), future)
        )
        return await future

    async def unregister_camera(self, camera_key: str) -> None:
        async with self._state_lock:
            tracker = self._trackers.pop(camera_key, None)
            if tracker is not None:
                await asyncio.to_thread(tracker.close)
        self._health.pop(camera_key, None)

    async def shutdown(self) -> None:
        self._closing = True
        if self._start_release_task is not None:
            self._start_release_task.cancel()
            try:
                await self._start_release_task
            except asyncio.CancelledError:
                pass
            self._start_release_task = None
        for waiter in tuple(self._start_waiters):
            if not waiter.done():
                waiter.set_exception(
                    RuntimeError("Behavior inference scheduler shut down during startup.")
                )
        self._start_waiters.clear()
        if self._runner is not None:
            self._runner.cancel()
            try:
                await self._runner
            except asyncio.CancelledError:
                pass
            self._runner = None
        while True:
            try:
                request = self._requests.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not request.future.done():
                request.future.set_exception(
                    RuntimeError("Behavior inference scheduler shut down before inference.")
                )
        for camera_key in list(self._trackers):
            await self.unregister_camera(camera_key)
        if self._shared_reid is not None:
            await asyncio.to_thread(self._shared_reid.close)
            self._shared_reid = None
        predictor = getattr(self.detector.model, "predictor", None)
        dataset = getattr(predictor, "dataset", None)
        close = getattr(dataset, "close", None)
        if callable(close):
            close()
        self.detector.model = None
        self._pose_warmed = False
        if self.detector.device.startswith("cuda"):
            try:
                import torch

                torch.cuda.empty_cache()
            except Exception:
                logger.warning("Could not clear CUDA cache during behavior shutdown.", exc_info=True)

    async def _run(self) -> None:
        while True:
            first = await self._requests.get()
            batch = [first]
            if settings.BEHAVIOR_BATCH_WAIT_MS:
                await asyncio.sleep(settings.BEHAVIOR_BATCH_WAIT_MS / 1000.0)
            while len(batch) < settings.BEHAVIOR_BATCH_MAX_SIZE:
                try:
                    batch.append(self._requests.get_nowait())
                except asyncio.QueueEmpty:
                    break
            live_batch = [request for request in batch if not request.future.cancelled()]
            if not live_batch:
                continue
            try:
                async with self._state_lock:
                    if self._batch_processor is None:
                        results, pose_ms, tracking_times = await asyncio.to_thread(
                            self._predict_and_track_batch,
                            live_batch,
                        )
                    else:
                        started = time.perf_counter()
                        results = await asyncio.to_thread(self._batch_processor, live_batch)
                        pose_ms = (time.perf_counter() - started) * 1000.0
                        tracking_times = [0.0] * len(results)
                for request, result, tracking_ms in zip(
                    live_batch, results, tracking_times, strict=True
                ):
                    health = self._health.setdefault(
                        request.camera_key,
                        BehaviorInferenceHealth(request.camera_key),
                    )
                    health.completed_frames += 1
                    health.batches += 1
                    health.last_batch_size = len(live_batch)
                    health.last_queue_wait_ms = (
                        time.perf_counter() - request.submitted_at
                    ) * 1000.0
                    health.last_pose_ms = pose_ms
                    health.last_tracking_ms = tracking_ms
                    health.last_total_ms = (
                        time.perf_counter() - request.submitted_at
                    ) * 1000.0
                    if not request.future.done():
                        request.future.set_result(result)
            except Exception as exc:
                logger.exception("Behavior inference batch failed")
                for request in live_batch:
                    health = self._health.setdefault(
                        request.camera_key,
                        BehaviorInferenceHealth(request.camera_key),
                    )
                    health.failed_frames += 1
                    if not request.future.done():
                        request.future.set_exception(exc)

    async def _release_start_cohort(self) -> None:
        await asyncio.sleep(settings.BEHAVIOR_START_COHORT_WAIT_MS / 1000.0)
        waiters, self._start_waiters = self._start_waiters, set()
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(None)

    def _predict_and_track_batch(
        self,
        requests: list[_InferenceRequest],
    ) -> tuple[list[Any], float, list[float]]:
        with gpu_inference_lock:
            return self._predict_and_track_batch_locked(requests)

    def _predict_and_track_batch_locked(
        self,
        requests: list[_InferenceRequest],
    ) -> tuple[list[Any], float, list[float]]:
        model = self.detector._ensure_model()
        pose_started = time.perf_counter()
        try:
            results = model.predict(
                [request.image for request in requests],
                conf=settings.FALL_PERSON_CONFIDENCE,
                imgsz=settings.BEHAVIOR_POSE_IMGSZ,
                device=self.detector.device,
                half=self.detector.device.startswith("cuda"),
                verbose=False,
            )
        except Exception as exc:
            try:
                import torch

                is_oom = isinstance(exc, torch.cuda.OutOfMemoryError)
            except Exception:
                is_oom = False
            if is_oom:
                raise RuntimeError(
                    "CUDA out of memory during behavior pose batching. "
                    "Reduce pose image size/model size or active GPU workloads."
                ) from exc
            raise
        pose_ms = (time.perf_counter() - pose_started) * 1000.0
        tracked_results: list[Any] = []
        tracking_times: list[float] = []
        trackers: list[Any] = []
        for request in requests:
            tracker = self._trackers.get(request.camera_key)
            if tracker is None:
                tracker = self._create_tracker()
                self._trackers[request.camera_key] = tracker
            trackers.append(tracker)

        embeddings_by_result: list[list[np.ndarray] | None] = [None] * len(results)
        reid_ms = 0.0
        if self._tracker_factory is None and self._shared_reid is not None:
            reid_indices = [
                index
                for index, tracker in enumerate(trackers)
                if tracker.requires_reid()
            ]
            if reid_indices:
                reid_started = time.perf_counter()
                encoded = self._shared_reid.encode_batch(
                    [
                        (results[index].orig_img, results[index].boxes.cpu().numpy().xywh)
                        for index in reid_indices
                    ]
                )
                reid_ms = (time.perf_counter() - reid_started) * 1000.0
                for index, embeddings in zip(reid_indices, encoded, strict=True):
                    embeddings_by_result[index] = embeddings
        for result, embeddings, tracker in zip(
            results, embeddings_by_result, trackers, strict=True
        ):
            tracking_started = time.perf_counter()
            if self._tracker_factory is None:
                tracked_results.append(tracker.update(result, embeddings))
            else:
                tracked_results.append(tracker.update(result))
            tracking_times.append(
                (reid_ms if embeddings is not None else 0.0)
                + (time.perf_counter() - tracking_started) * 1000.0
            )
        return tracked_results, pose_ms, tracking_times

    def _create_tracker(self) -> Any:
        if self._tracker_factory is not None:
            return self._tracker_factory(self.detector)
        if self._shared_reid is None:
            self._shared_reid = ExplicitReID(
                self.detector.reid_model_path,
                device=self.detector.device,
                half=settings.BEHAVIOR_REID_HALF,
            )
        return _CameraTracker(self.detector, reid_encoder=self._shared_reid)

    def _prepare_camera_sync(self, camera_key: str) -> None:
        model = self.detector._ensure_model()
        if not self._pose_warmed:
            image_size = settings.BEHAVIOR_POSE_IMGSZ
            dummy_frame = np.zeros((image_size, image_size, 3), dtype=np.uint8)
            model.predict(
                [dummy_frame] * settings.BEHAVIOR_BATCH_MAX_SIZE,
                conf=settings.FALL_PERSON_CONFIDENCE,
                imgsz=image_size,
                device=self.detector.device,
                half=self.detector.device.startswith("cuda"),
                verbose=False,
            )
            self._pose_warmed = True
        if camera_key not in self._trackers:
            self._trackers[camera_key] = self._create_tracker()


_scheduler: BehaviorInferenceScheduler | None = None


def get_behavior_scheduler(detector: FallDetector) -> BehaviorInferenceScheduler:
    global _scheduler
    if _scheduler is None or _scheduler.detector is not detector:
        _scheduler = BehaviorInferenceScheduler(detector)
    return _scheduler


async def shutdown_behavior_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        await _scheduler.shutdown()
        _scheduler = None
