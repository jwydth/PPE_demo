"""Regression coverage for the IGNORE-zone person filter in the live pipeline.

The filter normalizes each person's foot point against the frame dimensions,
so it has to run *after* those dimensions are read off the current frame.
They start out None, which crashed the very first frame of any stream whose
camera had a zone configured (TypeError: unsupported operand type(s) for /:
'float' and 'NoneType').
"""

import asyncio
from types import SimpleNamespace

import numpy as np

from app.services import video_pipeline
from app.services.zone_service import COORD_SCALE, ZoneViolationRecord

FRAME_WIDTH = 160
FRAME_HEIGHT = 100
# Foot point of PERSON below: x = (20 + 80) / 2 / 160, y = 90 / 100.
PERSON = {"x1": 20, "y1": 10, "x2": 80, "y2": 90, "conf": 0.95, "track_id": 42}


def _ignore_zone(x1: float, y1: float, x2: float, y2: float) -> ZoneViolationRecord:
    """Ignore zone from normalized (0-1) corners."""
    poly = [
        (x1 * COORD_SCALE, y1 * COORD_SCALE),
        (x2 * COORD_SCALE, y1 * COORD_SCALE),
        (x2 * COORD_SCALE, y2 * COORD_SCALE),
        (x1 * COORD_SCALE, y2 * COORD_SCALE),
    ]
    return ZoneViolationRecord(
        camera_zone_view_id=1,
        physical_zone_id=1,
        zone_name="Restricted Area",
        zone_type="IGNORE",
        poly=poly,
        threshold=4,
    )


def _run_one_frame(monkeypatch, zone: ZoneViolationRecord) -> list:
    result = SimpleNamespace(
        orig_img=np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
    )

    class FakeModel:
        def track(self, **_kwargs):
            return iter([result])

    async def acquire_model_instance():
        return FakeModel()

    detector = SimpleNamespace(
        device="cpu",
        sign_model=None,
        acquire_model_instance=acquire_model_instance,
        release_model_instance=lambda _instance: None,
    )

    monkeypatch.setattr(video_pipeline, "_video_metadata", lambda _path: (10.0, 5))
    monkeypatch.setattr(
        video_pipeline, "_extract_result_boxes", lambda _result: ([PERSON], [], [], [])
    )
    monkeypatch.setattr(video_pipeline, "load_zones", lambda _video_name: [zone])

    async def collect() -> list:
        return [
            event
            async for event in video_pipeline.real_video_pipeline(
                detector,
                "ignored.mp4",
                "factory.mp4",
                stride=1,
                enable_ppe=False,
                enable_zone=True,
            )
        ]

    return asyncio.run(collect())


def _frame_events(events: list) -> list:
    return [event for event in events if event.event == "frame"]


def test_ignore_zone_filters_person_on_the_very_first_frame(monkeypatch):
    # Covers the bottom-left quadrant, where PERSON's foot point sits.
    events = _run_one_frame(monkeypatch, _ignore_zone(0.0, 0.5, 0.5, 1.0))

    frames = _frame_events(events)
    assert len(frames) == 1
    assert frames[0].data["frame_width"] == FRAME_WIDTH
    assert frames[0].data["frame_height"] == FRAME_HEIGHT
    assert frames[0].data["frames"] == []


def test_person_outside_ignore_zone_survives_the_filter(monkeypatch):
    # Top-left quadrant — PERSON's foot point is outside it.
    events = _run_one_frame(monkeypatch, _ignore_zone(0.0, 0.0, 0.5, 0.5))

    frames = _frame_events(events)
    assert len(frames) == 1
    assert [f["track_id"] for f in frames[0].data["frames"]] == [42]
