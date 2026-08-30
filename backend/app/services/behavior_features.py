"""Feature extraction compatible with the trained behavior.joblib artifact."""

from __future__ import annotations

import math
from statistics import fmean, pstdev
from typing import Any, Iterable


CANONICAL_FPS = 24
# Share of the 60-sample window that must carry a real pose for the window to
# be classified at all. A source slower than CANONICAL_FPS can never reach it:
# accept_source_frame pads the canonical timeline with missing samples, so the
# ceiling is source_fps / CANONICAL_FPS.
MIN_VALID_FRAME_RATIO = 0.70
COCO = {"nose": 0, "left_shoulder": 5, "right_shoulder": 6, "left_hip": 11, "right_hip": 12, "left_ankle": 15, "right_ankle": 16}
STEP_METRICS = ("torso_angle", "compression", "spread_ratio", "combined_speed", "hip_ankle", "ground_speed", "scale_speed", "body_speed")


def _mean(values: Iterable[float | None], default: float = 0.0) -> float:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return fmean(clean) if clean else default


def _std(values: Iterable[float | None]) -> float:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return pstdev(clean) if len(clean) > 1 else 0.0


def _distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _point(frame: dict[str, Any], names: tuple[str, ...], threshold: float) -> tuple[float, float] | None:
    points: list[tuple[float, float]] = []
    for name in names:
        index = COCO[name]
        scores, keypoints = frame.get("keypoint_scores", []), frame.get("keypoints", [])
        if index >= len(scores) or index >= len(keypoints) or float(scores[index]) < threshold:
            continue
        points.append((float(keypoints[index][0]), float(keypoints[index][1])))
    return (_mean(point[0] for point in points), _mean(point[1] for point in points)) if len(points) == len(names) else None


def _geometry(frame: dict[str, Any] | None, threshold: float) -> dict[str, Any]:
    empty = {name: None for name in (*STEP_METRICS, "body_center", "ground_point", "bbox_height", "body_size", "joint_positions")}
    if frame is None:
        return empty
    x1, y1, x2, y2 = (float(value) for value in frame["bbox"])
    width, height = max(0.0, x2 - x1), max(0.0, y2 - y1)
    shoulder = _point(frame, ("left_shoulder", "right_shoulder"), threshold)
    hip = _point(frame, ("left_hip", "right_hip"), threshold)
    ankles = _point(frame, ("left_ankle", "right_ankle"), threshold)
    nose = _point(frame, ("nose",), threshold)
    body_size = math.hypot(width, height) or height or 1.0
    valid = [(float(point[0]), float(point[1])) for point, score in zip(frame.get("keypoints", []), frame.get("keypoint_scores", []), strict=False) if float(score) >= threshold]
    spread_height = max((point[1] for point in valid), default=0.0) - min((point[1] for point in valid), default=0.0)
    spread = (max((point[0] for point in valid), default=0.0) - min((point[0] for point in valid), default=0.0)) / spread_height if spread_height else (width / height if height else 0.0)
    return {
        "torso_angle": math.degrees(math.atan2(abs(shoulder[1] - hip[1]), abs(shoulder[0] - hip[0]))) if shoulder and hip else None,
        "compression": abs(nose[1] - hip[1]) / height if nose and hip and height else None,
        "spread_ratio": spread,
        "hip_ankle": abs(hip[1] - ankles[1]) / body_size if hip and ankles else None,
        "body_center": ((_mean((shoulder[0], hip[0])), _mean((shoulder[1], hip[1])))) if shoulder and hip else None,
        "ground_point": ankles or ((x1 + x2) / 2.0, y2), "bbox_height": height, "body_size": body_size,
        "joint_positions": valid, "ground_speed": None, "scale_speed": None, "combined_speed": None, "body_speed": None,
    }


def _interpolate(values: list[float | None], max_gap: int = 6) -> list[float | None]:
    result = list(values)
    index = 0
    while index < len(result):
        if result[index] is not None:
            index += 1
            continue
        start = index
        while index < len(result) and result[index] is None:
            index += 1
        gap, left, right = index - start, start - 1, index
        if gap <= max_gap and left >= 0 and right < len(result) and result[left] is not None and result[right] is not None:
            for offset, target in enumerate(range(start, right), 1):
                result[target] = float(result[left]) + (float(result[right]) - float(result[left])) * offset / (gap + 1)
    return result


def feature_columns() -> list[str]:
    summary = ["torso_angle_mean", "torso_angle_std", "head_hip_compression_mean", "hip_ankle_vertical_diff_mean", "skeleton_spread_ratio_mean", "skeleton_spread_ratio_max", "ground_speed_mean", "ground_speed_max", "scale_speed_mean", "combined_speed_mean", "combined_speed_std", "body_speed_max", "body_acceleration_max"]
    steps = [f"step_{name}_t{index}" for index in range(15) for name in STEP_METRICS]
    final = ["final_lying_score", "final_1s_body_speed_mean", "final_1s_joint_motion_mean", "final_1s_joint_motion_std"]
    quality = ["avg_keypoint_confidence", "missing_ankle_ratio", "valid_frame_ratio", "missing_hip_ratio", "track_gap_count", "skeleton_jump_score"]
    return [*summary, *steps, *final, *quality]


def extract_window_features(frames: list[dict[str, Any] | None]) -> dict[str, Any]:
    """Return the exact raw feature names/order expected by behavior.joblib."""
    if len(frames) != 60:
        raise ValueError("Behavior inference requires exactly 60 frames")
    metrics = [_geometry(frame, 0.10) for frame in frames]
    for index in range(1, len(metrics)):
        current, previous = metrics[index], metrics[index - 1]
        dt = 1.0 / CANONICAL_FPS
        if current["ground_point"] and previous["ground_point"] and current["bbox_height"]:
            current["ground_speed"] = _distance(current["ground_point"], previous["ground_point"]) / (current["bbox_height"] * dt)
        if current["bbox_height"] and previous["bbox_height"]:
            current["scale_speed"] = abs(current["bbox_height"] - previous["bbox_height"]) / (current["bbox_height"] * dt)
        if current["ground_speed"] is not None or current["scale_speed"] is not None:
            current["combined_speed"] = (current["ground_speed"] or 0.0) + .45 * (current["scale_speed"] or 0.0)
        if current["body_center"] and previous["body_center"] and current["body_size"]:
            current["body_speed"] = _distance(current["body_center"], previous["body_center"]) / (current["body_size"] * dt)
    arrays = {name: _interpolate([item[name] for item in metrics]) for name in STEP_METRICS}
    accelerations = [abs(float(current) - float(previous)) * CANONICAL_FPS if current is not None and previous is not None else None for previous, current in zip(arrays["body_speed"], arrays["body_speed"][1:], strict=False)]
    raw = {
        "torso_angle_mean": _mean(arrays["torso_angle"]), "torso_angle_std": _std(arrays["torso_angle"]),
        "head_hip_compression_mean": _mean(arrays["compression"]), "hip_ankle_vertical_diff_mean": _mean(arrays["hip_ankle"]),
        "skeleton_spread_ratio_mean": _mean(arrays["spread_ratio"]), "skeleton_spread_ratio_max": max((value for value in arrays["spread_ratio"] if value is not None), default=0.0),
        "ground_speed_mean": _mean(arrays["ground_speed"]), "ground_speed_max": max((value for value in arrays["ground_speed"] if value is not None), default=0.0),
        "scale_speed_mean": _mean(arrays["scale_speed"]), "combined_speed_mean": _mean(arrays["combined_speed"]), "combined_speed_std": _std(arrays["combined_speed"]),
        "body_speed_max": max((value for value in arrays["body_speed"] if value is not None), default=0.0), "body_acceleration_max": max((value for value in accelerations if value is not None), default=0.0),
    }
    for step, frame_index in enumerate(range(0, 60, 4)):
        for name in STEP_METRICS:
            raw[f"step_{name}_t{step}"] = float(arrays[name][frame_index] or 0.0)
    final = metrics[-24:]
    joint_motion = [_mean(_distance(first, second) for first, second in zip(previous["joint_positions"], current["joint_positions"], strict=True)) / max(current["body_size"], 1.0) for previous, current in zip(final, final[1:], strict=False) if previous["joint_positions"] and current["joint_positions"] and len(previous["joint_positions"]) == len(current["joint_positions"])]
    torso, spread, still = _mean(arrays["torso_angle"][-24:]), _mean(arrays["spread_ratio"][-24:]), _mean(joint_motion)
    ramp = lambda value, low, high: min(1.0, max(0.0, (value - low) / (high - low)))
    raw.update({"final_lying_score": _mean((1 - ramp(torso, 25, 75), ramp(spread, .8, 1.6), 1 - ramp(still, .01, .20))), "final_1s_body_speed_mean": _mean(arrays["body_speed"][-24:]), "final_1s_joint_motion_mean": still, "final_1s_joint_motion_std": _std(joint_motion)})
    confidences = [float(score) for frame in frames if frame for score in frame.get("keypoint_scores", [])]
    observed = [index for index, frame in enumerate(frames) if frame]
    centers = [metric["body_center"] for metric in metrics]
    jumps = [_distance(current, previous) / max(metrics[index]["body_size"], 1.0) for index, (previous, current) in enumerate(zip(centers, centers[1:], strict=False), 1) if previous and current]
    raw.update({"avg_keypoint_confidence": _mean(confidences), "missing_ankle_ratio": sum(frame is None or any(float(frame.get("keypoint_scores", [0.0] * 17)[index]) < .10 for index in (15, 16)) for frame in frames) / 60, "valid_frame_ratio": len(observed) / 60, "missing_hip_ratio": sum(frame is None or any(float(frame.get("keypoint_scores", [0.0] * 17)[index]) < .10 for index in (11, 12)) for frame in frames) / 60, "track_gap_count": float(sum((right - left) > 1 for left, right in zip(observed, observed[1:], strict=False))), "skeleton_jump_score": max(jumps, default=0.0)})
    return {"raw": raw, "quality": {"status": "good" if len(observed) / 60 >= MIN_VALID_FRAME_RATIO else "low_quality", "valid_frame_ratio": len(observed) / 60}}
