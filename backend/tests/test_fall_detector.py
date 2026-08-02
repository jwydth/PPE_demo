import numpy as np
import pytest

from app.services.behavior_features import extract_window_features, feature_columns
from app.services.fall_detector import FallDetector, FallModelUnavailable, _schema


def _pose_frame() -> dict:
    keypoints = np.zeros((17, 2), dtype=np.float32)
    scores = np.zeros(17, dtype=np.float32)
    for index, point in {0: (120, 100), 5: (90, 140), 6: (150, 140), 11: (95, 210), 12: (145, 210), 15: (100, 300), 16: (140, 300)}.items():
        keypoints[index] = point
        scores[index] = .9
    return {
        "bbox": [70, 90, 170, 320],
        "keypoints": keypoints.tolist(),
        "keypoint_scores": scores.tolist(),
    }


def test_behavior_feature_window_matches_model_contract():
    features = extract_window_features([_pose_frame() for _ in range(60)])

    assert set(features["raw"]) == set(feature_columns())
    assert features["quality"]["status"] == "good"


def test_fall_detector_missing_behavior_weights_is_unavailable(tmp_path):
    detector = FallDetector()
    detector.behavior_model_path = tmp_path / "missing.joblib"

    with pytest.raises(FallModelUnavailable, match="behavior weights not found"):
        detector._ensure_behavior_model()


def test_live_payload_serializes_bbox_as_an_object():
    schema = _schema({
        "track_id": 4,
        "status": "others",
        "score": 1.0,
        "person_confidence": .9,
        "bbox": [10, 20, 30, 40],
        "features": {},
        "keypoints": [],
    })

    assert schema.model_dump()["bbox"] == {"x1": 10.0, "y1": 20.0, "x2": 30.0, "y2": 40.0}


def test_live_session_resamples_a_30_fps_source_to_24_fps():
    session = FallDetector().create_live_session(fps=30)

    accepted = [index for index in range(30) if session.accept_source_frame(index / 30)]

    assert accepted == [0, 2, 3, 4, 5, 7, 8, 9, 10, 12, 13, 14, 15, 17, 18, 19, 20, 22, 23, 24, 25, 27, 28, 29]


def test_lost_track_discards_its_behavior_window():
    session = FallDetector().create_live_session(fps=24)
    session.windows[4].append(_pose_frame())
    session.missing_samples_by_track[4] = 13

    session._prune_lost_tracks()

    assert 4 not in session.windows
