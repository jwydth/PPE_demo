from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.schemas import BoundingBox, PersonResult
from app.services import ppe_detector as ppe


def _person(track_id: int, bbox: BoundingBox) -> PersonResult:
    return PersonResult(
        person_id=track_id,
        track_id=track_id,
        bbox=bbox,
        confidence=0.95,
        equipment=[],
        compliant=False,
    )


def test_save_violation_snapshot_does_not_mutate_source_frame(monkeypatch, tmp_path):
    import cv2

    monkeypatch.setattr(ppe, "SNAPSHOT_DIR", tmp_path)
    frame = np.zeros((120, 160, 3), dtype=np.uint8)

    first = ppe._save_violation_snapshot(
        frame=frame,
        person=_person(1, BoundingBox(x1=10, y1=20, x2=50, y2=80)),
        missing=["Vest"],
        video_stem="factory",
        frame_index=1,
    )
    second = ppe._save_violation_snapshot(
        frame=frame,
        person=_person(2, BoundingBox(x1=90, y1=20, x2=130, y2=80)),
        missing=["Helmet"],
        video_stem="factory",
        frame_index=1,
    )

    assert np.count_nonzero(frame) == 0

    first_image = cv2.imread(str(tmp_path / first))
    second_image = cv2.imread(str(tmp_path / second))
    assert first_image is not None
    assert second_image is not None
    assert np.count_nonzero(first_image[:, :70]) > 0
    assert np.count_nonzero(second_image[:, :70]) == 0
    assert np.count_nonzero(second_image[:, 80:]) > 0
