import joblib
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
    detector.portable_behavior_model_path = tmp_path / "missing.ubj"

    with pytest.raises(FallModelUnavailable, match="behavior weights not found"):
        detector._ensure_behavior_model()


def test_portable_behavior_classifier_uses_cpu_and_configured_threads():
    detector = FallDetector()
    classifier = detector._ensure_behavior_model()

    assert classifier.booster.attributes().get("device") in {None, "cpu"}
    assert detector.portable_behavior_model_path.suffix == ".ubj"


def test_portable_behavior_classifier_matches_legacy_probabilities():
    detector = FallDetector()
    with pytest.warns(UserWarning, match="serialized model"):
        legacy = joblib.load(detector.behavior_model_path)
    portable = detector._ensure_behavior_model()
    inputs = np.random.default_rng(42).normal(size=(4, 141)).astype(np.float32)

    legacy_probabilities = np.asarray(legacy.predict_proba(inputs), dtype=np.float32)
    portable_probabilities = portable.predict_proba(inputs)

    np.testing.assert_allclose(portable_probabilities, legacy_probabilities, atol=1e-7)


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


def test_discontinuity_invalidates_temporal_windows_but_keeps_incident_cooldown():
    session = FallDetector().create_live_session(fps=24)
    session.windows[4].extend(_pose_frame() for _ in range(59))
    session.probability_history[4].append({"falling": 0.9})
    session.last_incident_at[(4, "falling")] = 12.0

    session.mark_discontinuity(
        frame_index=100,
        timestamp_seconds=100 / 24,
        dropped_frames=3,
    )

    assert not session.windows
    assert not session.probability_history
    assert session.last_incident_at[(4, "falling")] == 12.0
    assert session.sample_index == 0
