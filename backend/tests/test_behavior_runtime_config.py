import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_behavior_runtime_defaults_preserve_temporal_contract():
    configured = Settings(_env_file=None)

    assert configured.FALL_LIVE_FRAME_STRIDE == 1
    assert configured.FALL_BEHAVIOR_WINDOW_FRAMES == 60
    assert configured.FALL_BEHAVIOR_CANONICAL_FPS == 24
    assert configured.BEHAVIOR_GMC_METHOD == "none"
    assert configured.BEHAVIOR_POSE_IMGSZ == 448
    assert configured.BEHAVIOR_REID_INTERVAL_FRAMES == 2
    assert configured.BEHAVIOR_LIVE_WARMUP_FRAMES == 3
    assert configured.BEHAVIOR_CAMERA_BURST_SIZE == 4


def test_fixed_camera_forces_gmc_off():
    configured = Settings(
        _env_file=None,
        BEHAVIOR_FIXED_CAMERA=True,
        BEHAVIOR_GMC_METHOD="sparseOptFlow",
    )

    assert configured.BEHAVIOR_GMC_METHOD == "none"


def test_behavior_live_stride_cannot_subsample_temporal_model():
    with pytest.raises(ValidationError, match="FALL_LIVE_FRAME_STRIDE must remain 1"):
        Settings(_env_file=None, FALL_LIVE_FRAME_STRIDE=2)


def test_behavior_runtime_rejects_non_positive_queue():
    with pytest.raises(ValidationError, match="BEHAVIOR_ORDERED_QUEUE_SIZE"):
        Settings(_env_file=None, BEHAVIOR_ORDERED_QUEUE_SIZE=0)
