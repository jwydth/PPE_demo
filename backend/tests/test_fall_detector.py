import numpy as np
import pytest

from app.services.fall_detector import (
    FallDetector,
    FallDetectorConfig,
    FallModelUnavailable,
    KPT,
    score_detection,
)


def _fallen_detection() -> dict:
    keypoints = np.zeros((17, 3), dtype=np.float32)
    keypoints[KPT["nose"]] = [120, 230, 0.9]
    keypoints[KPT["left_shoulder"]] = [80, 250, 0.9]
    keypoints[KPT["right_shoulder"]] = [180, 250, 0.9]
    keypoints[KPT["left_hip"]] = [90, 260, 0.9]
    keypoints[KPT["right_hip"]] = [190, 260, 0.9]
    keypoints[KPT["left_ankle"]] = [80, 280, 0.8]
    keypoints[KPT["right_ankle"]] = [200, 280, 0.8]
    return {
        "bbox": np.array([40, 200, 260, 320], dtype=np.float32),
        "keypoints": keypoints,
    }


def test_fall_score_can_reach_confirmed_fall():
    config = FallDetectorConfig(
        person_confidence=0.1,
        risk_threshold=0.52,
        fall_threshold=0.68,
        persistence_seconds=1.0,
        max_frames=1200,
        frame_stride=1,
    )

    result = score_detection(
        _fallen_detection(),
        (480, 640, 3),
        velocity_y_norm=0.06,
        persistent_seconds=1.0,
        config=config,
    )

    assert result["status"] == "fall"
    assert result["score"] >= config.fall_threshold
    assert result["features"]["wide_box"] > 0


def test_fall_detector_missing_weights_is_unavailable(tmp_path):
    detector = FallDetector()
    detector.model_path = tmp_path / "missing.pt"

    with pytest.raises(FallModelUnavailable, match="weights not found"):
        detector._ensure_model()
