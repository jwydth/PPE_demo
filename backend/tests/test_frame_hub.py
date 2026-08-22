import asyncio
import time

import numpy as np

from app.services.frame_hub import FrameHubRegistry


class FakeCapture:
    opened = 0
    released = 0

    def __init__(self, _source: str):
        type(self).opened += 1
        self.index = 0
        self.closed = False

    def isOpened(self):
        return True

    def get(self, _prop):
        import cv2

        if _prop == cv2.CAP_PROP_FPS:
            return 24.0
        if _prop == cv2.CAP_PROP_POS_MSEC:
            return self.index * 1000.0 / 24.0
        return 0.0

    def read(self):
        if self.closed or self.index >= 200:
            return False, None
        image = np.full((2, 2, 3), self.index % 255, dtype=np.uint8)
        self.index += 1
        time.sleep(0.001)
        return True, image

    def release(self):
        if not self.closed:
            type(self).released += 1
            self.closed = True


def test_hub_shares_one_capture_and_closes_after_last_subscriber():
    async def run():
        FakeCapture.opened = FakeCapture.released = 0
        registry = FrameHubRegistry(FakeCapture, idle_grace_seconds=0)
        latest = await registry.subscribe("camera-a", policy="latest")
        ordered = await registry.subscribe("camera-a", policy="ordered", queue_size=8)

        assert FakeCapture.opened == 1
        assert latest.hub is ordered.hub
        latest_packet = await latest.get()
        ordered_packet = await ordered.get()
        assert latest_packet is not None
        assert ordered_packet is not None
        assert latest_packet.stream_epoch == ordered_packet.stream_epoch
        assert latest_packet.media_pts_ms >= 0
        assert latest_packet.source_time_ms > 0
        assert latest_packet.discontinuity_sequence == 0

        await latest.close()
        assert "camera-a" in registry.hubs
        await ordered.close()
        assert "camera-a" not in registry.hubs
        assert FakeCapture.released == 1

    asyncio.run(run())


def test_new_hub_uses_a_new_stream_epoch():
    async def run():
        registry = FrameHubRegistry(FakeCapture, idle_grace_seconds=0)
        first = await registry.subscribe("camera-c", policy="latest")
        first_packet = await first.get()
        await first.close()

        second = await registry.subscribe("camera-c", policy="latest")
        second_packet = await second.get()
        await second.close()

        assert first_packet is not None
        assert second_packet is not None
        assert first_packet.stream_epoch != second_packet.stream_epoch

    asyncio.run(run())


def test_latest_drops_stale_while_ordered_marks_overflow_gap():
    async def run():
        registry = FrameHubRegistry(FakeCapture, idle_grace_seconds=0)
        latest = await registry.subscribe("camera-b", policy="latest")
        ordered = await registry.subscribe("camera-b", policy="ordered", queue_size=2)
        await asyncio.sleep(0.03)

        latest_packet = await latest.get()
        ordered_packet = await ordered.get()

        assert latest.dropped_frames > 0
        assert latest_packet.gap_before
        assert ordered.dropped_frames > 0
        assert ordered_packet.gap_before
        assert ordered_packet.dropped_before > 0
        await latest.close()
        await ordered.close()

    asyncio.run(run())


def test_quick_resubscribe_within_grace_window_reuses_the_capture():
    async def run():
        FakeCapture.opened = FakeCapture.released = 0
        registry = FrameHubRegistry(FakeCapture, idle_grace_seconds=0.2)
        first = await registry.subscribe("camera-d", policy="latest")
        first_packet = await first.get()
        await first.close()

        # Still within the grace window — reuses the same capture/hub and
        # therefore the same stream_epoch, unlike an immediate-teardown
        # resubscribe (see test_new_hub_uses_a_new_stream_epoch).
        second = await registry.subscribe("camera-d", policy="latest")
        assert "camera-d" in registry.hubs
        assert FakeCapture.opened == 1
        assert FakeCapture.released == 0
        second_packet = await second.get()
        assert second_packet.stream_epoch == first_packet.stream_epoch
        await second.close()
        # second.close() leaves the hub idling out its grace window in a
        # background task — force it down now so its capture thread doesn't
        # keep running (and touching FakeCapture's shared counters) after
        # this test's event loop closes.
        await registry.close_all()

    asyncio.run(run())


def test_hub_tears_down_once_grace_window_elapses_unused():
    async def run():
        FakeCapture.opened = FakeCapture.released = 0
        registry = FrameHubRegistry(FakeCapture, idle_grace_seconds=0.1)
        sub = await registry.subscribe("camera-e", policy="latest")
        await sub.get()
        await sub.close()

        assert "camera-e" in registry.hubs
        await asyncio.sleep(0.3)
        assert "camera-e" not in registry.hubs
        assert FakeCapture.released == 1

    asyncio.run(run())
