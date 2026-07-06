from __future__ import annotations

import base64
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.core.config import BACKEND_DIR, settings
from app.schemas.detection import BoundingBox
from app.schemas.fall_detection import (
    BehaviorIncidentRead,
    FallDetectionSummary,
    FallImagePredictionResponse,
    FallPoseDetection,
    FallTimelineItem,
    FallVideoMetadata,
    FallVideoPredictionResponse,
)
from app.services.behavior_incident_service import (
    BehaviorIncidentService,
    open_behavior_incident_service,
)
from app.services.ppe.device import _select_inference_device


KEYPOINT_NAMES = [
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
]
KPT = {name: index for index, name in enumerate(KEYPOINT_NAMES)}
SKELETON = [
    ("left_shoulder", "right_shoulder"), ("left_shoulder", "left_hip"), ("right_shoulder", "right_hip"),
    ("left_hip", "right_hip"), ("left_shoulder", "left_elbow"), ("left_elbow", "left_wrist"),
    ("right_shoulder", "right_elbow"), ("right_elbow", "right_wrist"), ("left_hip", "left_knee"),
    ("left_knee", "left_ankle"), ("right_hip", "right_knee"), ("right_knee", "right_ankle"),
]
COLORS = {
    "normal": (70, 122, 20),
    "fall_risk": (0, 165, 255),
    "fall": (24, 35, 180),
}


class FallModelUnavailable(RuntimeError):
    """Raised when fall detection weights are unavailable."""


@dataclass
class TrackState:
    track_id: int
    bbox: np.ndarray
    center_y: float
    last_frame_index: int
    abnormal_seconds: float = 0.0
    missed_frames: int = 0


@dataclass(frozen=True)
class FallDetectorConfig:
    person_confidence: float
    risk_threshold: float
    fall_threshold: float
    persistence_seconds: float
    max_frames: int
    frame_stride: int
    image_size: int = 640
    iou_threshold: float = 0.20
    max_missed_frames: int = 12


class FallDetector:
    def __init__(self) -> None:
        self.model = None
        self.device = _select_inference_device(settings.INFERENCE_DEVICE)
        self.model_path = _resolve_backend_path(settings.FALL_MODEL_PATH)
        self.config = FallDetectorConfig(
            person_confidence=settings.FALL_PERSON_CONFIDENCE,
            risk_threshold=settings.FALL_RISK_THRESHOLD,
            fall_threshold=settings.FALL_THRESHOLD,
            persistence_seconds=settings.FALL_PERSISTENCE_SECONDS,
            max_frames=settings.FALL_MAX_FRAMES,
            frame_stride=max(1, settings.FALL_FRAME_STRIDE),
        )

    def predict_image(
        self,
        input_path: Path,
        *,
        source_name: str | None,
    ) -> FallImagePredictionResponse:
        model = self._ensure_model()
        image = cv2.imread(str(input_path))
        if image is None:
            raise ValueError(f"Could not decode image: {input_path}")

        result = model.predict(
            source=image,
            conf=self.config.person_confidence,
            imgsz=self.config.image_size,
            device=self.device,
            verbose=False,
        )[0]
        detections = extract_pose_detections(result, self.config.person_confidence)
        tracker = SimplePoseTracker(
            fps=24.0,
            iou_threshold=self.config.iou_threshold,
            max_missed_frames=self.config.max_missed_frames,
            config=self.config,
        )
        detections = tracker.update(detections, 0)
        scored = [
            tracker.score_and_commit(
                detection,
                image.shape,
                0,
                still_image=True,
            )
            for detection in detections
        ]
        annotated = image.copy()
        for detection in scored:
            draw_detection(annotated, detection)

        payloads = [detection_payload(item) for item in scored]
        incidents = self._persist_confirmed_detections(
            detections=scored,
            frame=annotated,
            source_name=source_name,
            frame_index=0,
            timestamp=datetime.now(timezone.utc),
            incident_service=None,
        )
        by_track = {incident.track_id: incident.id for incident in incidents}
        pose_detections = [_to_pose_schema(item, by_track.get(item["track_id"])) for item in payloads]
        return FallImagePredictionResponse(
            media_type="image",
            device=self.device,
            model_name=settings.FALL_MODEL_NAME,
            model_version=settings.FALL_MODEL_VERSION,
            summary=_summary_schema(payloads, [incident.id for incident in incidents]),
            detections=pose_detections,
            annotated_image=encode_jpeg(annotated),
            incidents=incidents,
        )

    def predict_video(
        self,
        input_path: Path,
        *,
        source_name: str | None,
    ) -> FallVideoPredictionResponse:
        model = self._ensure_model()
        cap = cv2.VideoCapture(str(input_path))
        if not cap.isOpened():
            raise ValueError(f"Could not decode video: {input_path}")

        source_fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        stride = self.config.frame_stride
        tracker = SimplePoseTracker(
            fps=source_fps / max(stride, 1),
            iou_threshold=self.config.iou_threshold,
            max_missed_frames=self.config.max_missed_frames,
            config=self.config,
        )

        timeline: list[FallTimelineItem] = []
        all_payloads: list[dict[str, Any]] = []
        incidents: list[BehaviorIncidentRead] = []
        active_fall_tracks: set[int] = set()
        incident_by_track: dict[int, int] = {}
        processed = 0
        frame_index = 0

        while processed < self.config.max_frames:
            ok, frame = cap.read()
            if not ok or frame is None:
                break

            if frame_index % stride == 0:
                result = model.predict(
                    source=frame,
                    conf=self.config.person_confidence,
                    imgsz=self.config.image_size,
                    device=self.device,
                    verbose=False,
                )[0]
                detections = extract_pose_detections(result, self.config.person_confidence)
                detections = tracker.update(detections, processed)
                scored = [
                    tracker.score_and_commit(detection, frame.shape, processed)
                    for detection in detections
                ]
                annotated = frame.copy()
                for detection in scored:
                    draw_detection(annotated, detection)

                payloads = [detection_payload(item) for item in scored]
                current_tracks = {item["track_id"] for item in payloads}
                active_fall_tracks.intersection_update(set(tracker.tracks))
                active_fall_tracks.intersection_update(current_tracks)
                timestamp = datetime.now(timezone.utc)
                for detection, payload in zip(scored, payloads):
                    track_id = int(payload["track_id"])
                    if payload["status"] == "fall" and track_id not in active_fall_tracks:
                        persisted = self._persist_confirmed_detections(
                            detections=[detection],
                            frame=annotated,
                            source_name=source_name,
                            frame_index=frame_index,
                            timestamp=timestamp,
                            incident_service=None,
                        )
                        if persisted:
                            incidents.extend(persisted)
                            incident_by_track[track_id] = persisted[0].id
                        active_fall_tracks.add(track_id)
                    elif payload["status"] == "normal":
                        active_fall_tracks.discard(track_id)

                summary = summarize(payloads)
                all_payloads.extend(payloads)
                timeline.append(
                    FallTimelineItem(
                        frame_index=frame_index,
                        time_sec=frame_index / source_fps if source_fps > 0 else 0.0,
                        status=summary["status"],
                        top_label=summary["top_label"],
                        top_confidence=summary["top_confidence"],
                        detections=[
                            _to_pose_schema(item, incident_by_track.get(int(item["track_id"])))
                            for item in payloads
                        ],
                    )
                )
                processed += 1

            frame_index += 1

        cap.release()
        return FallVideoPredictionResponse(
            media_type="video",
            device=self.device,
            model_name=settings.FALL_MODEL_NAME,
            model_version=settings.FALL_MODEL_VERSION,
            summary=_summary_schema(all_payloads, [incident.id for incident in incidents]),
            video=FallVideoMetadata(
                source_fps=source_fps,
                sample_stride=stride,
                total_frames=total_frames,
                processed_frames=processed,
                frame_width=width,
                frame_height=height,
            ),
            timeline=timeline,
            incidents=incidents,
        )

    def _persist_confirmed_detections(
        self,
        *,
        detections: list[dict[str, Any]],
        frame: np.ndarray,
        source_name: str | None,
        frame_index: int,
        timestamp: datetime,
        incident_service: BehaviorIncidentService | None,
    ) -> list[BehaviorIncidentRead]:
        confirmed = [
            detection
            for detection in detections
            if detection.get("fall", {}).get("status") == "fall"
        ]
        if not confirmed:
            return []

        if incident_service is None:
            with open_behavior_incident_service() as service:
                return self._persist_confirmed_detections(
                    detections=confirmed,
                    frame=frame,
                    source_name=source_name,
                    frame_index=frame_index,
                    timestamp=timestamp,
                    incident_service=service,
                )

        incidents: list[BehaviorIncidentRead] = []
        for detection in confirmed:
            local_snapshot = _write_temp_snapshot(frame)
            payload = detection_payload(detection)
            bbox = payload["bbox"]
            track_id = int(payload["track_id"])
            details = f"Track {track_id} fall detected at frame {frame_index}"
            bundle = incident_service.persist_fall_incident(
                timestamp=timestamp,
                details=details,
                local_snapshot_path=local_snapshot,
                video_name=source_name,
                frame_start=frame_index,
                frame_end=frame_index,
                track_id=track_id,
                person_index=track_id,
                bounding_box={"x1": bbox[0], "y1": bbox[1], "x2": bbox[2], "y2": bbox[3]},
                confidence=float(payload["score"]),
                keypoints=_keypoints_payload(detection["keypoints"]),
                features=payload["features"],
                metadata={
                    "model_name": settings.FALL_MODEL_NAME,
                    "model_version": settings.FALL_MODEL_VERSION,
                    "person_confidence": payload["person_confidence"],
                    "fall_status": payload["status"],
                },
            )
            incidents.append(bundle.incident)
        return incidents

    def _ensure_model(self):
        if self.model is not None:
            return self.model
        if not self.model_path.exists():
            raise FallModelUnavailable(
                f"Fall detection model is unavailable: weights not found at {self.model_path}"
            )
        try:
            from ultralytics import YOLO
        except Exception as exc:
            raise FallModelUnavailable(
                "Fall detection model is unavailable: ultralytics could not be imported."
            ) from exc
        self.model = YOLO(str(self.model_path))
        return self.model


class SimplePoseTracker:
    def __init__(
        self,
        *,
        fps: float,
        iou_threshold: float,
        max_missed_frames: int,
        config: FallDetectorConfig,
    ) -> None:
        self.fps = max(float(fps), 1.0)
        self.iou_threshold = iou_threshold
        self.max_missed_frames = max_missed_frames
        self.config = config
        self.tracks: dict[int, TrackState] = {}
        self.next_id = 1

    def update(self, detections: list[dict[str, Any]], frame_index: int) -> list[dict[str, Any]]:
        assigned_tracks: set[int] = set()
        assigned_detections: set[int] = set()
        for det_index, detection in enumerate(detections):
            best_id = None
            best_iou = 0.0
            for track_id, track in self.tracks.items():
                if track_id in assigned_tracks:
                    continue
                iou = box_iou(detection["bbox"], track.bbox)
                if iou > best_iou:
                    best_iou = iou
                    best_id = track_id
            if best_id is not None and best_iou >= self.iou_threshold:
                detection["track_id"] = best_id
                assigned_tracks.add(best_id)
                assigned_detections.add(det_index)

        for det_index, detection in enumerate(detections):
            if det_index in assigned_detections:
                continue
            track_id = self.next_id
            self.next_id += 1
            detection["track_id"] = track_id
            x1, y1, x2, y2 = detection["bbox"]
            self.tracks[track_id] = TrackState(
                track_id,
                detection["bbox"].copy(),
                float((y1 + y2) / 2),
                frame_index,
            )

        current_ids = {int(detection["track_id"]) for detection in detections}
        for track_id in list(self.tracks):
            if track_id not in current_ids:
                self.tracks[track_id].missed_frames += 1
                if self.tracks[track_id].missed_frames > self.max_missed_frames:
                    del self.tracks[track_id]
        return detections

    def score_and_commit(
        self,
        detection: dict[str, Any],
        frame_shape: tuple[int, int, int],
        frame_index: int,
        *,
        still_image: bool = False,
    ) -> dict[str, Any]:
        track = self.tracks[int(detection["track_id"])]
        x1, y1, x2, y2 = detection["bbox"]
        center_y = float((y1 + y2) / 2)
        frame_delta = max(1, frame_index - track.last_frame_index)
        seconds_delta = frame_delta / self.fps
        velocity_y_norm = ((center_y - track.center_y) / max(frame_shape[0], 1)) / max(seconds_delta, 1e-6)
        persistence_seconds = (
            self.config.persistence_seconds
            if still_image
            else track.abnormal_seconds
        )

        tentative = score_detection(
            detection,
            frame_shape,
            velocity_y_norm,
            persistence_seconds,
            self.config,
        )
        is_abnormal = tentative["score"] >= self.config.risk_threshold
        if still_image and is_abnormal:
            track.abnormal_seconds = self.config.persistence_seconds
        elif is_abnormal:
            track.abnormal_seconds += seconds_delta
        else:
            track.abnormal_seconds = max(0.0, track.abnormal_seconds - seconds_delta)
        scored = score_detection(
            detection,
            frame_shape,
            velocity_y_norm,
            track.abnormal_seconds,
            self.config,
        )

        track.bbox = detection["bbox"].copy()
        track.center_y = center_y
        track.last_frame_index = frame_index
        track.missed_frames = 0
        detection["fall"] = scored
        return detection


def extract_pose_detections(result: Any, person_conf: float) -> list[dict[str, Any]]:
    detections: list[dict[str, Any]] = []
    if result.boxes is None or len(result.boxes) == 0:
        return detections
    boxes = result.boxes.xyxy.detach().cpu().numpy()
    confidences = result.boxes.conf.detach().cpu().numpy()
    keypoints = None
    if result.keypoints is not None and result.keypoints.data is not None:
        keypoints = result.keypoints.data.detach().cpu().numpy()
    for index, (box, confidence) in enumerate(zip(boxes, confidences)):
        if float(confidence) < person_conf:
            continue
        kpts = (
            keypoints[index]
            if keypoints is not None and index < len(keypoints)
            else np.zeros((17, 3), dtype=np.float32)
        )
        detections.append(
            {
                "bbox": box.astype(np.float32),
                "person_confidence": float(confidence),
                "keypoints": kpts.astype(np.float32),
                "track_id": None,
            }
        )
    return detections


def score_detection(
    detection: dict[str, Any],
    frame_shape: tuple[int, int, int],
    velocity_y_norm: float,
    persistent_seconds: float,
    config: FallDetectorConfig,
) -> dict[str, Any]:
    height, width = frame_shape[:2]
    x1, y1, x2, y2 = [float(value) for value in detection["bbox"]]
    box_w = max(1.0, x2 - x1)
    box_h = max(1.0, y2 - y1)
    box_ratio = box_w / box_h
    box_center_y = (y1 + y2) / 2.0 / max(height, 1)
    box_bottom_y = y2 / max(height, 1)
    box_area = (box_w * box_h) / max(width * height, 1)

    kpts = detection["keypoints"]
    shoulder_mid = midpoint([safe_point(kpts, KPT["left_shoulder"]), safe_point(kpts, KPT["right_shoulder"])])
    hip_mid = midpoint([safe_point(kpts, KPT["left_hip"]), safe_point(kpts, KPT["right_hip"])])
    ankle_mid = midpoint([safe_point(kpts, KPT["left_ankle"], 0.10), safe_point(kpts, KPT["right_ankle"], 0.10)])
    nose = safe_point(kpts, KPT["nose"], 0.10)

    torso_angle = 90.0
    torso_horizontal = 0.0
    if shoulder_mid is not None and hip_mid is not None:
        torso_angle = angle_to_horizontal(shoulder_mid, hip_mid)
        torso_horizontal = 1.0 - ramp(torso_angle, 25.0, 75.0)

    head_hip_compressed = 0.0
    head_low = 0.0
    if nose is not None and hip_mid is not None:
        head_hip_dy = abs(float(nose[1] - hip_mid[1])) / box_h
        head_hip_compressed = 1.0 - ramp(head_hip_dy, 0.32, 0.85)
        head_low = ramp(float(nose[1]) / max(height, 1), 0.38, 0.78)

    ankle_hip_flat = 0.0
    if ankle_mid is not None and hip_mid is not None:
        ankle_hip_dy = abs(float(ankle_mid[1] - hip_mid[1])) / box_h
        ankle_hip_flat = 1.0 - ramp(ankle_hip_dy, 0.28, 0.90)

    wide_box = ramp(box_ratio, 0.95, 1.85)
    low_box = 0.65 * ramp(box_center_y, 0.45, 0.78) + 0.35 * ramp(box_bottom_y, 0.60, 0.92)
    small_far_penalty = 1.0 - 0.35 * (1.0 - ramp(box_area, 0.015, 0.06))
    downward_motion = ramp(velocity_y_norm, 0.015, 0.060)
    persistence = ramp(persistent_seconds, 0.35, config.persistence_seconds)
    score = clamp01((
        0.28 * wide_box
        + 0.24 * torso_horizontal
        + 0.17 * head_hip_compressed
        + 0.11 * ankle_hip_flat
        + 0.12 * low_box
        + 0.08 * head_low
        + 0.12 * downward_motion
        + 0.18 * persistence
    ) * small_far_penalty)

    if score >= config.fall_threshold and persistent_seconds >= config.persistence_seconds:
        status = "fall"
    elif score >= config.risk_threshold:
        status = "fall_risk"
    else:
        status = "normal"

    return {
        "status": status,
        "score": score,
        "features": {
            "wide_box": wide_box,
            "torso_horizontal": torso_horizontal,
            "head_hip_compressed": head_hip_compressed,
            "ankle_hip_flat": ankle_hip_flat,
            "low_box": low_box,
            "head_low": head_low,
            "downward_motion": downward_motion,
            "persistence": persistence,
            "box_ratio": box_ratio,
            "torso_angle": torso_angle,
            "velocity_y_norm": velocity_y_norm,
            "persistent_seconds": persistent_seconds,
        },
    }


def detection_payload(detection: dict[str, Any]) -> dict[str, Any]:
    fall = detection["fall"]
    return {
        "track_id": int(detection["track_id"]),
        "status": fall["status"],
        "score": float(fall["score"]),
        "person_confidence": float(detection["person_confidence"]),
        "bbox": [float(v) for v in detection["bbox"].tolist()],
        "features": {key: float(value) for key, value in fall["features"].items()},
        "keypoints": _keypoints_payload(detection["keypoints"]),
    }


def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
    fall = sum(1 for item in items if item["status"] == "fall")
    risk = sum(1 for item in items if item["status"] == "fall_risk")
    normal = sum(1 for item in items if item["status"] == "normal")
    top = max(items, key=lambda item: item["score"], default=None)
    status = "fall" if fall else ("fall_risk" if risk else ("normal" if normal else "no_detection"))
    return {
        "status": status,
        "fall_count": fall,
        "fall_risk_count": risk,
        "normal_count": normal,
        "person_count": len(items),
        "top_label": top["status"] if top else "none",
        "top_confidence": top["score"] if top else 0.0,
    }


def draw_detection(image: np.ndarray, detection: dict[str, Any]) -> None:
    fall = detection.get("fall", {"status": "normal", "score": 0.0, "features": {}})
    status = fall["status"]
    score = float(fall["score"])
    color = COLORS.get(status, COLORS["normal"])
    x1, y1, x2, y2 = detection["bbox"].astype(int).tolist()
    cv2.rectangle(image, (x1, y1), (x2, y2), color, 3)
    draw_skeleton(image, detection["keypoints"], color)
    label = f"ID {detection.get('track_id', 0)} {status.upper()} {score:.2f}"
    cv2.rectangle(image, (x1, max(0, y1 - 34)), (min(image.shape[1] - 1, x1 + 310), y1), color, -1)
    cv2.putText(image, label, (x1 + 8, max(22, y1 - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    features = fall.get("features", {})
    details = f"wide {features.get('wide_box', 0):.2f} | torso {features.get('torso_horizontal', 0):.2f} | persist {features.get('persistent_seconds', 0):.1f}s"
    cv2.putText(image, details, (x1, min(image.shape[0] - 12, y2 + 24)), cv2.FONT_HERSHEY_SIMPLEX, 0.52, color, 2)


def draw_skeleton(image: np.ndarray, keypoints: np.ndarray, color: tuple[int, int, int]) -> None:
    for a_name, b_name in SKELETON:
        a = safe_point(keypoints, KPT[a_name], 0.12)
        b = safe_point(keypoints, KPT[b_name], 0.12)
        if a is not None and b is not None:
            cv2.line(image, tuple(a.astype(int)), tuple(b.astype(int)), color, 2, cv2.LINE_AA)
    for x, y, conf in keypoints:
        if conf >= 0.12:
            cv2.circle(image, (int(x), int(y)), 3, color, -1, cv2.LINE_AA)


def safe_point(keypoints: np.ndarray, index: int, min_conf: float = 0.15) -> np.ndarray | None:
    if keypoints is None or index >= len(keypoints):
        return None
    x, y, conf = keypoints[index]
    if conf < min_conf or x <= 0 or y <= 0:
        return None
    return np.array([float(x), float(y)], dtype=np.float32)


def midpoint(points: list[np.ndarray | None]) -> np.ndarray | None:
    usable = [point for point in points if point is not None]
    if not usable:
        return None
    return np.mean(np.stack(usable, axis=0), axis=0)


def angle_to_horizontal(point_a: np.ndarray, point_b: np.ndarray) -> float:
    dx = abs(float(point_b[0] - point_a[0]))
    dy = abs(float(point_b[1] - point_a[1]))
    if dx < 1e-6 and dy < 1e-6:
        return 90.0
    return float(np.degrees(np.arctan2(dy, dx)))


def box_iou(box_a: np.ndarray, box_b: np.ndarray) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    intersection = iw * ih
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    return float(intersection / union) if union > 0 else 0.0


def ramp(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return clamp01((value - low) / (high - low))


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def encode_jpeg(image: np.ndarray, quality: int = 78) -> str:
    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buffer).decode("ascii")


def _write_temp_snapshot(frame: np.ndarray) -> Path:
    handle = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    path = Path(handle.name)
    handle.close()
    cv2.imwrite(str(path), frame)
    return path


def _to_pose_schema(item: dict[str, Any], incident_id: int | None = None) -> FallPoseDetection:
    x1, y1, x2, y2 = item["bbox"]
    return FallPoseDetection(
        track_id=int(item["track_id"]),
        status=item["status"],
        score=float(item["score"]),
        person_confidence=float(item["person_confidence"]),
        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
        features=item["features"],
        keypoints=item.get("keypoints"),
        incident_id=incident_id,
    )


def _summary_schema(
    items: list[dict[str, Any]],
    incident_ids: list[int],
) -> FallDetectionSummary:
    summary = summarize(items)
    return FallDetectionSummary(
        status=summary["status"],
        fall_count=summary["fall_count"],
        fall_risk_count=summary["fall_risk_count"],
        normal_count=summary["normal_count"],
        person_count=summary["person_count"],
        top_label=summary["top_label"],
        top_confidence=summary["top_confidence"],
        persisted_incident_ids=incident_ids,
    )


def _keypoints_payload(keypoints: np.ndarray) -> list[list[float]]:
    return [[float(x), float(y), float(conf)] for x, y, conf in keypoints.tolist()]


def _resolve_backend_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path.resolve()
