"""Realtime fall detection backed by the trained pose-behavior classifier.

Unlike the former geometric heuristic, this module uses the same 60-frame
feature contract and ``best_behavior_model.joblib`` classifier as the PPE
labeling project.
"""

from __future__ import annotations

import base64
import logging
import tempfile
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from app.core.config import BACKEND_DIR, settings
from app.schemas.detection import BoundingBox
from app.schemas.fall_detection import (
    BehaviorIncidentRead, FallDetectionSummary, FallImagePredictionResponse,
    FallPoseDetection, FallTimelineItem, FallVideoMetadata, FallVideoPredictionResponse,
)
from app.services.behavior_features import extract_window_features, feature_columns
from app.models.behavior_incident import BehaviorIncidentSeverity, BehaviorType
from app.services.behavior_incident_service import BehaviorIncidentService, open_behavior_incident_service
from app.services.ppe.device import _select_inference_device

logger = logging.getLogger(__name__)


COLORS = {"others": (70, 122, 20), "running": (255, 165, 0), "falling": (24, 35, 180)}
SKELETON = ((5, 6), (5, 11), (6, 12), (11, 12), (5, 7), (7, 9), (6, 8), (8, 10), (11, 13), (13, 15), (12, 14), (14, 16))


class FallModelUnavailable(RuntimeError):
    """Raised when a required pose, ReID, or behavior-model asset is missing."""


class PortableBehaviorClassifier:
    """Small compatibility adapter around XGBoost's stable Booster format."""

    def __init__(self, model_path: Path, *, threads: int) -> None:
        import xgboost as xgb

        self._xgb = xgb
        self.booster = xgb.Booster()
        self.booster.load_model(model_path)
        self.booster.set_param({"device": "cpu", "nthread": threads})

    def predict_proba(self, values: np.ndarray) -> np.ndarray:
        matrix = self._xgb.DMatrix(np.asarray(values, dtype=np.float32))
        probabilities = np.asarray(self.booster.predict(matrix), dtype=np.float32)
        if probabilities.ndim == 1:
            probabilities = probabilities.reshape(len(values), -1)
        return probabilities


class FallDetector:
    def __init__(self) -> None:
        self.model: Any | None = None
        self.behavior_model: Any | None = None
        self.device = _select_inference_device(settings.INFERENCE_DEVICE)
        self.model_path = _resolve_backend_path(settings.FALL_MODEL_PATH)
        self.behavior_model_path = _resolve_backend_path(settings.FALL_BEHAVIOR_MODEL_PATH)
        self.portable_behavior_model_path = _resolve_backend_path(
            settings.FALL_BEHAVIOR_PORTABLE_MODEL_PATH
        )
        self.reid_model_path = _resolve_backend_path(settings.FALL_REID_MODEL_PATH)
        self.tracker_path = BACKEND_DIR / "app" / "inference" / "botsort_dedicated_reid.yaml"
        self.window_size = settings.FALL_BEHAVIOR_WINDOW_FRAMES
        self.window_stride = settings.FALL_BEHAVIOR_WINDOW_STRIDE

    def create_live_session(self, *, fps: float, frame_stride: int | None = None) -> "FallLiveSession":
        # The behavior model was trained at 24 FPS.  The caller should use a
        # stride of 1; accepting the parameter preserves the existing API.
        return FallLiveSession(self, fps=max(float(fps), 1.0), frame_stride=max(1, frame_stride or 1))

    def predict_image(self, input_path: Path, *, source_name: str | None) -> FallImagePredictionResponse:
        image = cv2.imread(str(input_path))
        if image is None:
            raise ValueError(f"Could not decode image: {input_path}")
        model = self._ensure_model()
        result = model.predict(source=image, conf=settings.FALL_PERSON_CONFIDENCE, imgsz=settings.BEHAVIOR_POSE_IMGSZ, device=self.device, verbose=False)[0]
        detections = _pose_detections(result, fallback_track_ids=True)
        # A single image is intentionally not classified: behavior.joblib needs
        # a full 60-frame motion window.  Do not invent a temporary class.
        payloads: list[dict[str, Any]] = []
        return FallImagePredictionResponse(
            media_type="image", device=self.device, model_name=settings.FALL_MODEL_NAME,
            model_version=settings.FALL_MODEL_VERSION, summary=_summary(payloads, []),
            detections=[_schema(item) for item in payloads], annotated_image=encode_jpeg(image), incidents=[],
        )

    def predict_video(self, input_path: Path, *, source_name: str | None) -> FallVideoPredictionResponse:
        cap = cv2.VideoCapture(str(input_path))
        if not cap.isOpened():
            raise ValueError(f"Could not decode video: {input_path}")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        session = self.create_live_session(fps=fps, frame_stride=1)
        timeline: list[FallTimelineItem] = []
        all_detections: list[dict[str, Any]] = []
        incidents: list[BehaviorIncidentRead] = []
        frame_index = 0
        while frame_index < settings.FALL_MAX_FRAMES:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if not session.accept_source_frame(frame_index / fps):
                frame_index += 1
                continue
            output = session.process_frame(frame, frame_index=frame_index, source_name=source_name)
            detections = output["detections"]
            all_detections.extend(detections)
            incidents.extend(BehaviorIncidentRead.model_validate(value) for value in output["incidents"])
            summary = output["summary"]
            timeline.append(FallTimelineItem(frame_index=frame_index, time_sec=frame_index / fps, status=summary["status"], top_label=summary["top_label"], top_confidence=summary["top_confidence"], detections=[FallPoseDetection.model_validate(item) for item in detections]))
            frame_index += 1
        cap.release()
        return FallVideoPredictionResponse(
            media_type="video", device=self.device, model_name=settings.FALL_MODEL_NAME, model_version=settings.FALL_MODEL_VERSION,
            summary=_summary(all_detections, [item.id for item in incidents]),
            video=FallVideoMetadata(source_fps=fps, sample_stride=1, total_frames=total, processed_frames=len(timeline), frame_width=width, frame_height=height),
            timeline=timeline, incidents=incidents,
        )

    def _ensure_model(self) -> Any:
        if self.model is not None:
            return self.model
        for path, label in ((self.model_path, "pose weights"), (self.reid_model_path, "ReID weights"), (self.tracker_path, "BoT-SORT configuration")):
            if not path.is_file():
                raise FallModelUnavailable(f"Fall detection model is unavailable: {label} not found at {path}")
        try:
            from ultralytics import YOLO
        except Exception as exc:
            raise FallModelUnavailable("Fall detection model is unavailable: ultralytics could not be imported.") from exc
        self.model = YOLO(str(self.model_path))
        return self.model

    def _ensure_behavior_model(self) -> Any:
        if self.behavior_model is not None:
            return self.behavior_model
        # The configured joblib classifier is the primary artifact. Do not
        # let a leftover legacy UBJ file silently override it.
        if self.behavior_model_path.is_file():
            try:
                import joblib

                model = joblib.load(self.behavior_model_path)
                if not hasattr(model, "predict_proba"):
                    raise TypeError("model does not expose predict_proba")
                # ExtraTrees uses n_jobs; legacy sklearn-wrapped XGBoost also
                # supports this attribute. Keep a single CPU inference thread
                # per live behavior worker for predictable latency.
                if hasattr(model, "n_jobs"):
                    model.n_jobs = settings.BEHAVIOR_XGBOOST_THREADS
                get_booster = getattr(model, "get_booster", None)
                if callable(get_booster):
                    get_booster().set_param(
                        {"device": "cpu", "nthread": settings.BEHAVIOR_XGBOOST_THREADS}
                    )
                self.behavior_model = model
                logger.info(
                    "Behavior classifier loaded from %s (%s)",
                    self.behavior_model_path,
                    type(model).__name__,
                )
                return self.behavior_model
            except Exception as exc:
                raise FallModelUnavailable(
                    "Fall detection model is unavailable: configured behavior "
                    f"classifier could not be loaded from {self.behavior_model_path}. "
                    "Install its runtime dependencies (scikit-learn for the "
                    "current ExtraTrees model)."
                ) from exc

        # Retain the portable XGBoost artifact solely as a fallback when the
        # configured primary file is absent.
        if self.portable_behavior_model_path.is_file():
            try:
                self.behavior_model = PortableBehaviorClassifier(
                    self.portable_behavior_model_path,
                    threads=settings.BEHAVIOR_XGBOOST_THREADS,
                )
                logger.info(
                    "Primary behavior classifier missing; using portable fallback %s on CPU with %s thread(s)",
                    self.portable_behavior_model_path,
                    settings.BEHAVIOR_XGBOOST_THREADS,
                )
                return self.behavior_model
            except Exception as exc:
                raise FallModelUnavailable(
                    "Fall detection model is unavailable: portable behavior "
                    f"weights could not be loaded from {self.portable_behavior_model_path}."
                ) from exc
        raise FallModelUnavailable(
            "Fall detection model is unavailable: behavior weights not found at "
            f"{self.behavior_model_path} or {self.portable_behavior_model_path}"
        )

    def classify(self, frames: list[dict[str, Any] | None]) -> dict[str, Any] | None:
        if len(frames) < self.window_size:
            return None
        feature_started = time.perf_counter()
        feature = extract_window_features(frames[-self.window_size:])
        feature_ms = (time.perf_counter() - feature_started) * 1000.0
        if feature["quality"]["status"] != "good":
            return None
        raw = feature["raw"]
        columns = [name for name in feature_columns() if name not in {"track_gap_count", "valid_frame_ratio"}]
        values: list[float] = []
        square_root = {"ground_speed_mean", "ground_speed_max", "combined_speed_mean", "combined_speed_std", "body_acceleration_max"}
        double_log = {"skeleton_spread_ratio_max", "skeleton_spread_ratio_mean"}
        for name in columns:
            value = float(raw[name])
            # Training clamps the aggregate hip-ankle feature and its 15
            # sampled values to this physical range before fitting.
            if name == "hip_ankle_vertical_diff_mean" or name.startswith("step_hip_ankle_"):
                value = min(1.0, max(0.0, value))
            if name in square_root:
                value = float(np.sqrt(max(0.0, value)))
            elif name in double_log:
                value = float(np.log1p(np.log1p(max(0.0, value))))
            values.append(value)
        classifier_started = time.perf_counter()
        probabilities = np.asarray(self._ensure_behavior_model().predict_proba(np.asarray([values], dtype=np.float32)), dtype=np.float32)[0]
        classifier_ms = (time.perf_counter() - classifier_started) * 1000.0
        if len(probabilities) != 3:
            raise FallModelUnavailable("Fall detection model is unavailable: behavior model must return others/running/falling probabilities.")
        others, running, falling = (float(value) for value in probabilities)
        label, confidence = max((("others", others), ("running", running), ("falling", falling)), key=lambda item: item[1])
        return {
            "status": label,
            "score": confidence,
            "features": {
                **{key: float(value) for key, value in raw.items()},
                "others_probability": others,
                "running_probability": running,
                "falling_probability": falling,
                "behavior_confidence": confidence,
                "window_ready": 1.0,
            },
            "behavior_label": label,
            "runtime_timings": {
                "feature_extraction_ms": feature_ms,
                "behavior_classifier_ms": classifier_ms,
            },
        }

    def persist(self, detection: dict[str, Any], frame: np.ndarray, source_name: str | None, frame_index: int) -> BehaviorIncidentRead:
        snapshot = _write_temp_snapshot(frame)
        payload = _payload(detection)
        label = str(detection["status"])
        behavior_type = BehaviorType.RUNNING_DETECTED if label == "running" else BehaviorType.FALL_DETECTED
        severity = BehaviorIncidentSeverity.MEDIUM if label == "running" else BehaviorIncidentSeverity.HIGH
        with open_behavior_incident_service() as service:
            return service.persist_behavior_incident(behavior_type=behavior_type, severity=severity, timestamp=datetime.now(timezone.utc), details=f"Track {payload['track_id']} {label} detected at frame {frame_index}", local_snapshot_path=snapshot, video_name=source_name, frame_start=frame_index, frame_end=frame_index, track_id=payload["track_id"], person_index=payload["track_id"], bounding_box={key: value for key, value in zip(("x1", "y1", "x2", "y2"), payload["bbox"], strict=True)}, confidence=payload["score"], keypoints=payload["keypoints"], features=payload["features"], metadata={"model_name": settings.FALL_MODEL_NAME, "model_version": settings.FALL_MODEL_VERSION, "behavior_label": detection.get("behavior_label", label)}).incident


class FallLiveSession:
    def __init__(self, detector: FallDetector, *, fps: float, frame_stride: int) -> None:
        self.detector, self.fps, self.frame_stride = detector, fps, frame_stride
        self.canonical_fps = max(1, settings.FALL_BEHAVIOR_CANONICAL_FPS)
        self.last_canonical_frame = -1
        self.timestamp_origin: float | None = None
        self.windows: dict[int, deque[dict[str, Any] | None]] = defaultdict(lambda: deque(maxlen=detector.window_size))
        self.probability_history: dict[int, deque[dict[str, float]]] = defaultdict(lambda: deque(maxlen=3))
        self.last_prediction: dict[int, dict[str, Any]] = {}
        self.active_behaviors: dict[int, str] = {}
        self.last_incident_at: dict[tuple[int, str], float] = {}
        self.missing_samples_by_track: dict[int, int] = defaultdict(int)
        self.incident_by_track: dict[int, int] = {}
        self.sample_index = 0
        self.last_summary: dict[str, Any] | None = None
        self.last_detections: list[dict[str, Any]] = []
        self.last_frame_index: int | None = None
        self.last_feature_ms = 0.0
        self.last_classifier_ms = 0.0
        self.last_pose_repaired_samples = 0

    def mark_discontinuity(
        self,
        *,
        frame_index: int,
        timestamp_seconds: float,
        dropped_frames: int,
    ) -> None:
        """Invalidate temporal state after a source/queue frame gap.

        A 60-sample classifier window must never bridge an unknown interval.
        Incident cooldown timestamps are intentionally retained to avoid a
        reconnect/gap creating duplicate persisted incidents.
        """
        self.windows.clear()
        self.probability_history.clear()
        self.last_prediction.clear()
        self.active_behaviors.clear()
        self.missing_samples_by_track.clear()
        self.incident_by_track.clear()
        self.last_summary = None
        self.last_detections = []
        self.last_frame_index = None
        self.last_feature_ms = 0.0
        self.last_classifier_ms = 0.0
        self.last_pose_repaired_samples = 0
        self.timestamp_origin = timestamp_seconds
        self.last_canonical_frame = -1
        self.sample_index = 0

    def accept_source_frame(self, timestamp_seconds: float) -> bool:
        """Select frames by capture time onto the model's fixed 24-FPS timeline.

        The behavior model was trained on canonical 24-FPS videos. RTSP feeds
        commonly deliver 25 or 30 FPS (and may drop frames under load), so
        passing every source frame makes all motion-speed features incorrect.
        Gaps are inserted as missing samples so the model never mistakes a
        dropped-frame gap for slow motion.
        """
        if self.timestamp_origin is None:
            self.timestamp_origin = timestamp_seconds
        canonical_index = int((timestamp_seconds - self.timestamp_origin) * self.canonical_fps)
        if canonical_index <= self.last_canonical_frame:
            return False
        for _ in range(canonical_index - self.last_canonical_frame - 1):
            self._append_missing_sample()
        self.last_canonical_frame = canonical_index
        return True

    def _append_missing_sample(self) -> None:
        for track_id, window in self.windows.items():
            window.append(None)
            self.missing_samples_by_track[track_id] += 1
        self._prune_lost_tracks()
        self.sample_index += 1

    def _prune_lost_tracks(self) -> None:
        expired = [
            track_id
            for track_id, missing in self.missing_samples_by_track.items()
            if missing > settings.FALL_TRACK_MAX_MISSING_SAMPLES
        ]
        for track_id in expired:
            self.windows.pop(track_id, None)
            self.probability_history.pop(track_id, None)
            self.last_prediction.pop(track_id, None)
            self.missing_samples_by_track.pop(track_id, None)
            self.active_behaviors.pop(track_id, None)
            self.incident_by_track.pop(track_id, None)

    def process_frame(self, frame: np.ndarray, *, frame_index: int, source_name: str | None, timestamp_seconds: float | None = None) -> dict[str, Any]:
        model = self.detector._ensure_model()
        # Ultralytics maintains the BoT-SORT/ReID state across calls when
        # persist=True, giving a stable window per worker.
        result = model.track(source=frame, persist=True, tracker=str(self.detector.tracker_path), conf=settings.FALL_PERSON_CONFIDENCE, imgsz=settings.BEHAVIOR_POSE_IMGSZ, device=self.detector.device, half=str(self.detector.device).startswith("cuda"), verbose=False)[0]
        return self.process_pose_result(
            result,
            frame=frame,
            frame_index=frame_index,
            source_name=source_name,
            timestamp_seconds=timestamp_seconds,
        )

    def process_pose_result(
        self,
        result: Any,
        *,
        frame: np.ndarray,
        frame_index: int,
        source_name: str | None,
        timestamp_seconds: float | None = None,
        ignore_detection: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        """Consume an already-tracked YOLO-Pose result without re-running pose."""
        # Report classifier work performed for this frame only. Reusing the
        # previous non-zero value made rolling health samples misleading.
        self.last_feature_ms = 0.0
        self.last_classifier_ms = 0.0
        self.last_pose_repaired_samples = 0
        detections = _pose_detections(result)
        if ignore_detection is not None:
            detections = [detection for detection in detections if not ignore_detection(detection)]
        for track_id, window in self.windows.items():
            window.append(None)
            self.missing_samples_by_track[track_id] += 1
        for detection in detections:
            track_id = int(detection["track_id"])
            window = self.windows[track_id]
            if window:
                window[-1] = detection["frame"]
            else:
                window.append(detection["frame"])
            self.last_pose_repaired_samples += self._repair_trailing_pose_gap(
                window,
                detection["frame"],
            )
            self.missing_samples_by_track[track_id] = 0
            # Match the labeling pipeline's 12-frame window stride and its
            # three-window probability smoothing. Between window boundaries,
            # keep the last model result for that worker.
            # As in the batch labeling pipeline, incomplete 60-sample
            # windows are not a behavior prediction and are omitted entirely.
            if len(window) < self.detector.window_size:
                continue
            classification_ran = False
            if track_id not in self.last_prediction or self.sample_index % self.detector.window_stride == 0:
                prediction = self.detector.classify(list(window))
                classification_ran = prediction is not None
                if prediction is None:
                    self.last_prediction.pop(track_id, None)
                    continue
                history = self.probability_history[track_id]
                history.append({name: float(prediction["features"][f"{name}_probability"]) for name in ("others", "running", "falling")})
                averaged = {name: sum(item[name] for item in history) / len(history) for name in history[0]}
                label, confidence = max(averaged.items(), key=lambda item: item[1])
                prediction["features"].update({f"{name}_probability": value for name, value in averaged.items()})
                prediction["behavior_label"] = label
                prediction["score"] = confidence
                prediction["status"] = label
                self.last_prediction[track_id] = prediction
            else:
                prediction = self.last_prediction[track_id]
            if classification_ran:
                timings = prediction.get("runtime_timings", {})
                self.last_feature_ms = max(
                    self.last_feature_ms,
                    float(timings.get("feature_extraction_ms", 0.0)),
                )
                self.last_classifier_ms = max(
                    self.last_classifier_ms,
                    float(timings.get("behavior_classifier_ms", 0.0)),
                )
            detection.update(prediction)
            draw_detection(frame, detection)
        current_tracks = {int(item["track_id"]) for item in detections}
        self.active_behaviors = {
            track_id: label for track_id, label in self.active_behaviors.items()
            if track_id in current_tracks
        }
        persisted: list[BehaviorIncidentRead] = []
        for detection in detections:
            track_id = int(detection["track_id"])
            if "status" not in detection:
                continue
            label = str(detection["status"])
            is_confirmed_behavior = label in {"running", "falling"} and detection["score"] >= settings.FALL_BEHAVIOR_MIN_CONFIDENCE
            timestamp = timestamp_seconds if timestamp_seconds is not None else frame_index / self.fps
            if is_confirmed_behavior and self.active_behaviors.get(track_id) != label:
                incident_key = (track_id, label)
                last_incident_at = self.last_incident_at.get(incident_key)
                if last_incident_at is None or timestamp - last_incident_at >= settings.FALL_INCIDENT_COOLDOWN_SECONDS:
                    incident = self.detector.persist(detection, frame, source_name, frame_index)
                    persisted.append(incident)
                    self.incident_by_track[track_id] = incident.id
                    self.last_incident_at[incident_key] = timestamp
                self.active_behaviors[track_id] = label
            elif not is_confirmed_behavior:
                self.active_behaviors.pop(track_id, None)
        payloads = [
            _payload(item, self.incident_by_track.get(int(item["track_id"])))
            for item in detections
            if "status" in item
        ]
        self._prune_lost_tracks()
        # The WebSocket/UI contract uses a BoundingBox object, while the
        # internal inference and persistence code deliberately uses a compact
        # four-value list. Serialize at this boundary only.
        # The classifier needs a 60-frame window before it can assign a real
        # label.  Keep emitting pose boxes during that warm-up period so the
        # UI can render ``Behavior: Unknown`` rather than showing no box.
        live_payloads = []
        for item in detections:
            if "status" in item:
                payload = _payload(item, self.incident_by_track.get(int(item["track_id"])))
            else:
                payload = {
                    "track_id": int(item["track_id"]),
                    "status": "unknown",
                    "score": 0.0,
                    "person_confidence": float(item["person_confidence"]),
                    "bbox": [float(value) for value in item["bbox"]],
                    "features": {},
                    "keypoints": item["keypoints"],
                }
            live_payloads.append(_schema(payload).model_dump())
        self.sample_index += 1
        self.last_summary, self.last_detections, self.last_frame_index = _summary(payloads, [item.id for item in persisted]).model_dump(), live_payloads, frame_index
        return {"summary": self.last_summary, "detections": live_payloads, "incidents": [item.model_dump() for item in persisted], "frame_index": frame_index}

    def _repair_trailing_pose_gap(
        self,
        window: deque[dict[str, Any] | None],
        current: dict[str, Any],
    ) -> int:
        """Interpolate a short same-track gap after its closing endpoint arrives."""
        samples = list(window)
        right = len(samples) - 1
        left = right - 1
        while left >= 0 and samples[left] is None:
            left -= 1
        gap = right - left - 1
        if gap == 0 or gap > settings.BEHAVIOR_POSE_REPAIR_MAX_GAP or left < 0:
            return 0

        previous = samples[left]
        if previous is None or not _pose_repair_is_safe(previous, current):
            return 0
        for offset in range(1, gap + 1):
            alpha = offset / (gap + 1)
            samples[left + offset] = _interpolate_pose_frame(previous, current, alpha)
        window.clear()
        window.extend(samples)
        return gap

    def payload_for_frame(self, frame_index: int, *, max_age_frames: int) -> dict[str, Any]:
        if self.last_summary is None or self.last_frame_index is None:
            return {"summary": None, "detections": [], "incidents": [], "frame_index": frame_index}
        age = frame_index - self.last_frame_index
        return {"summary": {**self.last_summary, "frame_index": frame_index, "age_frames": age, "is_stale": age > max_age_frames, "is_interpolated": age > 0}, "detections": self.last_detections if age <= max_age_frames else [], "incidents": [], "frame_index": frame_index}


def _pose_detections(result: Any, fallback_track_ids: bool = False) -> list[dict[str, Any]]:
    if result.boxes is None or result.keypoints is None:
        return []
    boxes = result.boxes.xyxy.detach().cpu().numpy()
    confidences = result.boxes.conf.detach().cpu().numpy()
    ids = result.boxes.id.int().cpu().tolist() if result.boxes.id is not None else list(range(1, len(boxes) + 1))
    points = result.keypoints.xy.detach().cpu().numpy()
    scores = result.keypoints.conf.detach().cpu().numpy() if result.keypoints.conf is not None else np.zeros((len(boxes), 17), dtype=np.float32)
    detections = []
    for index, (box, confidence, track_id) in enumerate(zip(boxes, confidences, ids, strict=True)):
        if not fallback_track_ids and result.boxes.id is None:
            continue
        keypoints = [[float(x), float(y), float(score)] for (x, y), score in zip(points[index], scores[index], strict=True)]
        record = {"bbox": [float(value) for value in box], "keypoints": [[value[0], value[1]] for value in keypoints], "keypoint_scores": [value[2] for value in keypoints], "person_confidence": float(confidence)}
        detections.append({"track_id": int(track_id), "bbox": record["bbox"], "keypoints": keypoints, "person_confidence": float(confidence), "frame": record})
    return detections


def _pose_repair_is_safe(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    minimum = settings.BEHAVIOR_POSE_REPAIR_MIN_CONFIDENCE
    for frame in (previous, current):
        scores = [float(value) for value in frame.get("keypoint_scores", [])]
        if not scores or sum(scores) / len(scores) < minimum:
            return False

    previous_box = [float(value) for value in previous["bbox"]]
    current_box = [float(value) for value in current["bbox"]]
    previous_center = (
        (previous_box[0] + previous_box[2]) / 2.0,
        (previous_box[1] + previous_box[3]) / 2.0,
    )
    current_center = (
        (current_box[0] + current_box[2]) / 2.0,
        (current_box[1] + current_box[3]) / 2.0,
    )
    width = max(1.0, previous_box[2] - previous_box[0])
    height = max(1.0, previous_box[3] - previous_box[1])
    shift_ratio = np.hypot(
        current_center[0] - previous_center[0],
        current_center[1] - previous_center[1],
    ) / np.hypot(width, height)
    return shift_ratio <= settings.BEHAVIOR_POSE_REPAIR_MAX_CENTER_SHIFT_RATIO


def _interpolate_pose_frame(
    previous: dict[str, Any],
    current: dict[str, Any],
    alpha: float,
) -> dict[str, Any]:
    def values(left, right):
        return [
            float(first) + (float(second) - float(first)) * alpha
            for first, second in zip(left, right, strict=True)
        ]

    return {
        "bbox": values(previous["bbox"], current["bbox"]),
        "keypoints": [
            values(first, second)
            for first, second in zip(
                previous["keypoints"],
                current["keypoints"],
                strict=True,
            )
        ],
        "keypoint_scores": values(
            previous["keypoint_scores"],
            current["keypoint_scores"],
        ),
        "is_synthetic": True,
        "interpolation_alpha": alpha,
    }


def _payload(item: dict[str, Any], incident_id: int | None = None) -> dict[str, Any]:
    return {"track_id": int(item["track_id"]), "status": item["status"], "score": float(item["score"]), "person_confidence": float(item["person_confidence"]), "bbox": [float(value) for value in item["bbox"]], "features": {key: float(value) for key, value in item["features"].items()}, "keypoints": item["keypoints"], "incident_id": incident_id}


def _schema(item: dict[str, Any]) -> FallPoseDetection:
    return FallPoseDetection(track_id=item["track_id"], status=item["status"], score=item["score"], person_confidence=item["person_confidence"], bbox=BoundingBox(x1=item["bbox"][0], y1=item["bbox"][1], x2=item["bbox"][2], y2=item["bbox"][3]), features=item["features"], keypoints=item["keypoints"], incident_id=item.get("incident_id"))


def _summary(items: list[dict[str, Any]], incident_ids: list[int]) -> FallDetectionSummary:
    falling = sum(item["status"] == "falling" for item in items)
    running = sum(item["status"] == "running" for item in items)
    others = sum(item["status"] == "others" for item in items)
    top = max(items, key=lambda item: item["score"], default=None)
    return FallDetectionSummary(status="falling" if falling else ("running" if running else ("others" if others else "no_detection")), others_count=others, running_count=running, falling_count=falling, person_count=len(items), top_label=top["status"] if top else "none", top_confidence=top["score"] if top else 0.0, persisted_incident_ids=incident_ids)


def draw_detection(image: np.ndarray, detection: dict[str, Any]) -> None:
    color = COLORS[detection["status"]]
    x1, y1, x2, y2 = (int(value) for value in detection["bbox"])
    cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
    for first, second in SKELETON:
        points = detection["keypoints"]
        if points[first][2] >= .12 and points[second][2] >= .12:
            cv2.line(image, (int(points[first][0]), int(points[first][1])), (int(points[second][0]), int(points[second][1])), color, 2)
    cv2.putText(image, f"ID {detection['track_id']} {detection['status'].upper()} {detection['score']:.2f}", (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, .65, color, 2)


def encode_jpeg(image: np.ndarray) -> str:
    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
    return "data:image/jpeg;base64," + base64.b64encode(buffer).decode("ascii") if ok else ""


def _write_temp_snapshot(frame: np.ndarray) -> Path:
    handle = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    path = Path(handle.name)
    handle.close()
    cv2.imwrite(str(path), frame)
    return path


def _resolve_backend_path(value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else BACKEND_DIR / path).resolve()
