import logging
from pathlib import Path
from unittest.mock import Mock

from app.routers import detection
from app.services import ppe_violation_service


def test_video_cleanup_permission_error_is_best_effort(caplog):
    tmp_path = Mock(spec=Path)
    tmp_path.unlink.side_effect = PermissionError("file is in use")

    with caplog.at_level(logging.WARNING):
        detection._cleanup_temp_video(tmp_path)

    tmp_path.unlink.assert_called_once_with(missing_ok=True)
    assert "still in use" in caplog.text


def test_ppe_snapshot_cleanup_failure_is_best_effort(caplog):
    snapshot_path = Mock(spec=Path)
    snapshot_path.unlink.side_effect = PermissionError("file is in use")

    with caplog.at_level(logging.WARNING):
        ppe_violation_service._cleanup_local_snapshot(snapshot_path)

    snapshot_path.unlink.assert_called_once_with(missing_ok=True)
    assert "Could not delete uploaded local PPE snapshot" in caplog.text
