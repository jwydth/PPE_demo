"""PPEDetector — orchestrates model loading, prediction, and video dispatch.

The three methods that interleave PPE detection with zone monitoring
(`_real_video_pipeline`, `_mock_process_video`, `_mock_stream_video`) are
thin delegators to `app.services.video_pipeline`, which is the only module
allowed to import from both `app.services.ppe` and `app.services.zone_service`.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from PIL import Image

from app.core.config import BACKEND_DIR, settings
from app.schemas.detection import (
    DetectionResponse,
    TrackingOverlay,
    TrackingOverlayFrame,
    VideoProcessingResponse,
    VideoSummary,
)
from app.schemas.streaming import StreamEvent
from app.schemas.violation import ViolationReport, ZoneViolation

from app.services import video_pipeline
from app.services.ppe.device import _select_inference_device
from app.services.ppe.response_builder import _build_response, _video_metadata

import logging

logger = logging.getLogger(__name__)


class PPEDetector:
    model = None
    sign_model = None  # set by _load_sign_model; stays None when weights are absent

    def __init__(self, *, enable_stream_pool: bool = True) -> None:
        """
        enable_stream_pool: pre-warm the concurrent-stream model pool (Tier 1.1).
            Only the detector instance actually used for `/ws/stream` needs this —
            other PPEDetector instances (e.g. the one backing `/predict-video`)
            never call `acquire_model_instance`, so building a pool for them
            would just load extra unused copies of the weights into VRAM.
        """
        self.model = None
        self.sign_model = None
        self.device = _select_inference_device(settings.INFERENCE_DEVICE)
        self._model_pool: asyncio.Queue | None = None
        self._pool_size = 0
        self._load_model()
        self._load_sign_model()
        if enable_stream_pool:
            self._init_model_pool()

    def _load_model(self) -> None:
        model_path = Path(settings.MODEL_PATH).expanduser()
        if not model_path.is_absolute():
            model_path = BACKEND_DIR / model_path
        model_path = model_path.resolve()

        if not model_path.exists():
            logger.error(f"Model file not found at: {model_path}")
            return

        try:
            from ultralytics import YOLO
            import numpy as np

            self.model = YOLO(str(model_path))
            # Warm up: one dummy inference so the CUDA context is ready before the first video
            dummy = np.zeros((640, 640, 3), dtype=np.uint8)
            self.model.predict(dummy, device=self.device, verbose=False)
            logger.info(f"PPE model loaded and warmed up on {self.device}")
        except Exception as e:
            logger.error(f"Failed to load YOLO model: {e}", exc_info=True)

    def _init_model_pool(self) -> None:
        """Pre-warm a bounded pool of tracker-isolated model instances for the
        live streaming path (PERF_PLAN.md Tier 1.1/1.4).

        `track(persist=True)` keeps tracker state on the model/predictor, so
        concurrent streams can't safely share one instance. Loading a fresh
        instance per websocket connection instead re-reads the weights from
        disk every time and holds N copies in VRAM under N concurrent
        streams. A fixed-size pool caps VRAM at a known ceiling and removes
        the per-connect disk load.
        """
        if self.model is None:
            return

        try:
            from ultralytics import YOLO
            import numpy as np

            # ultralytics.engine.predictor.BasePredictor.stream_inference only
            # imports torchvision the first time it sees a *stream*-type source
            # (webcam/RTSP) — never for the static dummy array used to warm up
            # below. Left lazy, that ~3s import happens on the first real
            # stream connection instead of here, adding multi-second latency
            # to "first frame" for whichever stream(s) hit it first (and, since
            # Python serializes concurrent imports of the same module, every
            # stream connecting around the same time pays it too).
            import torchvision  # noqa: F401

            pool_size = max(1, settings.MAX_CONCURRENT_STREAMS)
            model_path = self.model.ckpt_path or str(
                Path(settings.MODEL_PATH).resolve()
            )
            half = bool(settings.INFERENCE_HALF) and self.device.startswith("cuda")
            imgsz = settings.INFERENCE_IMGSZ
            dummy_square = np.zeros((imgsz, imgsz, 3), dtype=np.uint8)
            # Also warm up at a realistic 16:9 camera shape (the letterboxed
            # tensor shape a real RTSP frame produces differs from the square
            # dummy above). Measured on this repo's own RTSP demo setup: the
            # first real inference at a shape the GPU hasn't seen yet costs an
            # extra ~2.9s (CUDA allocator/kernel cache growing for that size),
            # on top of the RTSP connect itself — that cost lands on whichever
            # stream(s) connect first if we don't pay it here instead.
            dummy_wide = np.zeros((720, 1280, 3), dtype=np.uint8)

            pool: asyncio.Queue = asyncio.Queue(maxsize=pool_size)
            for i in range(pool_size):
                instance = self.model if i == 0 else YOLO(model_path)
                for dummy in (dummy_square, dummy_wide):
                    instance.predict(
                        dummy, device=self.device, half=half, imgsz=imgsz, verbose=False
                    )
                pool.put_nowait(instance)

            self._model_pool = pool
            self._pool_size = pool_size
            logger.info(
                f"Pre-warmed model pool: {pool_size} instance(s) on {self.device} "
                f"(half={half}, imgsz={imgsz})"
            )
        except Exception as e:
            logger.error(f"Failed to initialize model pool: {e}", exc_info=True)

    async def acquire_model_instance(self, timeout: float = 30.0):
        """Borrow a pre-warmed model instance from the pool for one stream.

        Falls back to the shared `self.model` when no pool was built (model
        failed to load). Blocks up to `timeout` seconds when every pool
        instance is in use — queueing new connections rather than silently
        loading another copy of the weights and growing VRAM usage.
        """
        pool = getattr(self, "_model_pool", None)
        if pool is None:
            return self.model
        try:
            return await asyncio.wait_for(pool.get(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"All {getattr(self, '_pool_size', 0)} model instance(s) are busy; "
                "too many concurrent streams."
            ) from exc

    def release_model_instance(self, instance) -> None:
        """Return a borrowed model instance to the pool."""
        pool = getattr(self, "_model_pool", None)
        if pool is not None and instance is not None:
            pool.put_nowait(instance)

    def _load_sign_model(self) -> None:
        sign_path = Path(settings.SIGN_MODEL_PATH).expanduser()
        if not sign_path.is_absolute():
            sign_path = BACKEND_DIR / sign_path
        sign_path = sign_path.resolve()

        if not sign_path.exists():
            logger.info(f"Sign model not found at {sign_path}; auto-zone feature disabled.")
            return

        try:
            from ultralytics import YOLO
            import numpy as np

            self.sign_model = YOLO(str(sign_path))
            # Warm up at both a square and a realistic 16:9 camera shape — same
            # reasoning as the main model's pool warmup above. Unlike the main
            # model, sign_model is never pooled/reused, so without this its
            # very first call (which happens on frame 0 of the first stream,
            # since frame_index % SIGN_PASS_FRAME_INTERVAL == 0 there) pays the
            # full cold-start cost live: measured ~6.3s on this repo's own RTSP
            # demo setup.
            for dummy in (
                np.zeros((640, 640, 3), dtype=np.uint8),
                np.zeros((720, 1280, 3), dtype=np.uint8),
            ):
                self.sign_model.predict(dummy, device=self.device, verbose=False)
            logger.info(f"Sign model loaded and warmed up on {self.device}")
        except Exception as e:
            logger.error(f"Failed to load sign model: {e}", exc_info=True)

    def predict(self, image: Image.Image) -> DetectionResponse:
        if self.model is None:
            return self._mock_predict(image)
        return self._real_predict(image)

    def process_video(
        self,
        video_path: Path,
        video_name: str,
        *,
        enable_ppe: bool = True,
        enable_zone: bool = True,
    ) -> VideoProcessingResponse:
        if self.model is None:
            return self._mock_process_video(
                video_path,
                video_name,
                enable_ppe=enable_ppe,
                enable_zone=enable_zone,
            )
        return self._real_process_video(
            video_path,
            video_name,
            enable_ppe=enable_ppe,
            enable_zone=enable_zone,
        )

    def _real_process_video(
        self,
        video_path: Path,
        video_name: str,
        *,
        enable_ppe: bool = True,
        enable_zone: bool = True,
    ) -> VideoProcessingResponse:
        """Synchronously process and return a full response."""
        fps, _ = _video_metadata(video_path)
        stride = max(1, settings.VIDEO_FRAME_STRIDE)

        final_summary: VideoSummary | None = None
        reports: list[ViolationReport] = []
        zone_violations: list[ZoneViolation] = []
        overlay_frames: list[TrackingOverlayFrame] = []
        frame_width: int = 1000
        frame_height: int = 1000

        for event in self._collect_real_video_events(
            video_path,
            video_name,
            stride=stride,
            enable_ppe=enable_ppe,
            enable_zone=enable_zone,
        ):
            if event.event == "violation":
                reports.append(ViolationReport(**event.data))
            elif event.event == "zone_violation":
                zone_violations.append(ZoneViolation(**event.data))
            elif event.event == "frame":
                for f_data in event.data["frames"]:
                    overlay_frames.append(TrackingOverlayFrame(**f_data))
            elif event.event == "summary":
                final_summary = VideoSummary(**event.data)

        return VideoProcessingResponse(
            summary=final_summary,
            reports=reports,
            zone_violations=zone_violations,
            tracking_overlay=TrackingOverlay(
                fps=round(fps, 2),
                stride=stride,
                frame_width=frame_width,
                frame_height=frame_height,
                frames=overlay_frames,
            ),
        )

    def _collect_real_video_events(
        self,
        video_path: str | Path,
        video_name: str,
        stride: int,
        *,
        enable_ppe: bool = True,
        enable_zone: bool = True,
    ) -> list[StreamEvent]:
        async def collect() -> list[StreamEvent]:
            events: list[StreamEvent] = []
            async for event in self._real_video_pipeline(
                video_path,
                video_name,
                stride=stride,
                enable_ppe=enable_ppe,
                enable_zone=enable_zone,
            ):
                events.append(event)
            return events

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(collect())

        import threading

        result: list[StreamEvent] = []
        error: BaseException | None = None

        def runner() -> None:
            nonlocal result, error
            try:
                result = asyncio.run(collect())
            except BaseException as exc:
                error = exc

        thread = threading.Thread(target=runner)
        thread.start()
        thread.join()
        if error is not None:
            raise error
        return result

    async def stream_video(
        self,
        video_path: str | Path,
        video_name: str,
        *,
        enable_ppe: bool = True,
        enable_zone: bool = True,
        enable_fall: bool = False,
        settings_state: dict | None = None,
    ):
        if self.model is None:
            async for event in self._mock_stream_video(
                video_path,
                video_name,
                enable_ppe=enable_ppe,
                enable_zone=enable_zone,
                enable_fall=enable_fall,
            ):
                yield event
            return

        stride = max(1, settings.VIDEO_FRAME_STRIDE)

        async for event in self._real_video_pipeline(
            video_path,
            video_name,
            stride=stride,
            enable_ppe=enable_ppe,
            enable_zone=enable_zone,
            enable_fall=enable_fall,
            settings_state=settings_state,
        ):
            if event.event == "frame":
                await asyncio.sleep(0)  # yield to event loop (settings listener etc.) without throttling
            yield event

    async def _real_video_pipeline(
        self,
        video_path: str | Path,
        video_name: str,
        stride: int,
        *,
        enable_ppe: bool = True,
        enable_zone: bool = True,
        enable_fall: bool = False,
        settings_state: dict | None = None,
    ):
        async for event in video_pipeline.real_video_pipeline(
            self,
            video_path,
            video_name,
            stride,
            enable_ppe=enable_ppe,
            enable_zone=enable_zone,
            enable_fall=enable_fall,
            settings_state=settings_state,
        ):
            yield event

    def _mock_predict(self, image: Image.Image) -> DetectionResponse:
        start = time.perf_counter()
        time.sleep(0.06)

        w, h = image.size

        def px(rel_box: tuple[float, float, float, float]) -> dict:
            rx1, ry1, rx2, ry2 = rel_box
            return {"x1": rx1 * w, "y1": ry1 * h, "x2": rx2 * w, "y2": ry2 * h}

        persons = [
            {**px((0.05, 0.02, 0.40, 0.98)), "conf": 0.96},
            {**px((0.55, 0.04, 0.95, 0.96)), "conf": 0.91},
        ]
        helmets = [{**px((0.10, 0.03, 0.35, 0.20)), "conf": 0.94}]
        vests = [{**px((0.08, 0.22, 0.38, 0.68)), "conf": 0.89}]
        cleaning_coveralls = [{**px((0.57, 0.18, 0.93, 0.88)), "conf": 0.87}]

        elapsed_ms = (time.perf_counter() - start) * 1000
        return _build_response(persons, helmets, vests, cleaning_coveralls, elapsed_ms)

    def _mock_process_video(
        self,
        video_path: Path,
        video_name: str,
        *,
        enable_ppe: bool = True,
        enable_zone: bool = True,
    ) -> VideoProcessingResponse:
        return video_pipeline.mock_process_video(
            self,
            video_path,
            video_name,
            enable_ppe=enable_ppe,
            enable_zone=enable_zone,
        )

    async def _mock_stream_video(
        self,
        video_path: Path,
        video_name: str,
        *,
        enable_ppe: bool = True,
        enable_zone: bool = True,
        enable_fall: bool = False,
    ):
        async for event in video_pipeline.mock_stream_video(
            self,
            video_path,
            video_name,
            enable_ppe=enable_ppe,
            enable_zone=enable_zone,
            enable_fall=enable_fall,
        ):
            yield event
