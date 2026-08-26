"""Covers the live-stream registry and the live_cameras KPI built on it.

live_cameras exists because active_cameras counts cameras with an *incident in
the range* — a historical figure the PDF report needs, but one that reads as a
bug on a live dashboard (it stays high for days after a camera stops
streaming). These tests pin the distinction so the two don't get merged again.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.models.camera import Camera
from app.models.factory import Factory
from app.services.analytics_service import AnalyticsService
from app.services.incident_service import UnifiedIncident
from app.services.live_streams import (
    _current_cancels,
    active_stream_sources,
    claim_stream,
    release_stream,
)

# get_summary derives its range from the real clock, so anchor to a real "now"
# rather than a fixed date (same reasoning as test_analytics_service).
NOW = datetime.now(timezone.utc)


def _service(incidents: list[UnifiedIncident], cameras: list[Camera]) -> AnalyticsService:
    incident_service = Mock()
    incident_service.list_incidents.return_value = incidents
    physical_zone_repository = Mock()
    physical_zone_repository.get_by_factory.return_value = []
    camera_repository = Mock()
    camera_repository.list_all.return_value = cameras
    factory_repository = Mock()
    factory_repository.get_or_create_default_factory.return_value = Factory(id=1, name="Default")
    return AnalyticsService(
        incident_service, physical_zone_repository, camera_repository, factory_repository
    )


def _incident(*, id_: int, camera_id: int) -> UnifiedIncident:
    return UnifiedIncident(
        id=id_,
        category="ppe",
        type="Missing Helmet",
        severity="High",
        timestamp=NOW,
        camera_id=camera_id,
        zone_id=1,
        zone_name="Zone A",
        camera_label=f"Camera {camera_id}",
        snapshot_url=None,
    )


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is module-level process state; don't leak between tests."""
    _current_cancels.clear()
    yield
    _current_cancels.clear()


def test_claim_then_release_tracks_the_source():
    event = asyncio.Event()

    assert claim_stream("cam-a", event) is None
    assert active_stream_sources() == {"cam-a"}

    release_stream("cam-a", event)
    assert active_stream_sources() == set()


def test_multiple_sources_are_tracked_independently():
    a, b = asyncio.Event(), asyncio.Event()
    claim_stream("cam-a", a)
    claim_stream("cam-b", b)

    assert active_stream_sources() == {"cam-a", "cam-b"}

    release_stream("cam-a", a)
    assert active_stream_sources() == {"cam-b"}


def test_reclaiming_a_source_returns_the_previous_owner_to_cancel():
    old, new = asyncio.Event(), asyncio.Event()
    claim_stream("cam-a", old)

    assert claim_stream("cam-a", new) is old
    # Still exactly one live stream — the newer connection took the slot over,
    # it did not add a second one.
    assert active_stream_sources() == {"cam-a"}


def test_superseded_connection_cannot_release_the_newer_owners_slot():
    """The losing connection's cleanup runs *after* the winner has claimed the
    source. If it released blindly, the camera would silently drop out of the
    count while still streaming."""
    old, new = asyncio.Event(), asyncio.Event()
    claim_stream("cam-a", old)
    claim_stream("cam-a", new)

    release_stream("cam-a", old)

    assert active_stream_sources() == {"cam-a"}


def _camera(id_: int, source_key: str, *, is_active: bool = True) -> Camera:
    return Camera(
        id=id_, factory_id=1, name=f"Camera {id_}", source_key=source_key, is_active=is_active
    )


def test_live_cameras_counts_only_cameras_currently_streaming():
    cameras = [
        _camera(1, "rtsp://host/stream1"),
        _camera(2, "rtsp://host/stream2"),
        _camera(3, "rtsp://host/stream3"),
    ]
    claim_stream("rtsp://host/stream1", asyncio.Event())
    claim_stream("rtsp://host/stream3", asyncio.Event())

    summary = _service([], cameras=cameras).get_summary(range_="7D", zone_id=None)

    assert summary.live_cameras == 2
    assert summary.total_cameras == 3


def test_live_cameras_is_zero_when_nothing_is_streaming():
    summary = _service([], cameras=[_camera(1, "rtsp://host/stream1")]).get_summary(
        range_="7D", zone_id=None
    )

    assert summary.live_cameras == 0


def test_live_cameras_ignores_streams_with_no_camera_row():
    """An ad-hoc uploaded video is a live stream but not a camera, so it must
    not push the numerator past the denominator."""
    claim_stream("some-upload.mp4", asyncio.Event())

    summary = _service([], cameras=[_camera(1, "rtsp://host/stream1")]).get_summary(
        range_="7D", zone_id=None
    )

    assert summary.live_cameras == 0
    assert summary.total_cameras == 1


def test_live_cameras_ignores_soft_deleted_cameras():
    claim_stream("rtsp://host/stream1", asyncio.Event())
    cameras = [_camera(1, "rtsp://host/stream1", is_active=False)]

    summary = _service([], cameras=cameras).get_summary(range_="7D", zone_id=None)

    assert summary.live_cameras == 0
    assert summary.total_cameras == 0


def test_live_cameras_is_independent_of_incident_history():
    """The bug this field fixes: camera 2 last reported days ago and is not
    streaming, yet it still counts toward active_cameras. live_cameras must
    not follow it."""
    cameras = [_camera(1, "rtsp://host/stream1"), _camera(2, "rtsp://host/stream2")]
    incidents = [_incident(id_=1, camera_id=1), _incident(id_=2, camera_id=2)]
    # Only camera 1 is actually streaming.
    claim_stream("rtsp://host/stream1", asyncio.Event())

    summary = _service(incidents, cameras=cameras).get_summary(range_="7D", zone_id=None)

    assert summary.active_cameras == 2  # historical: both reported in range
    assert summary.live_cameras == 1  # point-in-time: only one is connected
