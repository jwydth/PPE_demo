import asyncio

from app.routers.testing import reset_streams_health, streams_health
from app.services.behavior_stream import BehaviorStreamWorker
from app.services.fall_detector import FallDetector
from app.services.stream_health import (
    clear_stream_health,
    mark_stream_event,
    observe_stream_timing,
    stream_health_snapshot,
    update_stream_health,
)


def test_stream_health_redacts_url_credentials():
    clear_stream_health()
    update_stream_health(
        "rtsp://camera-user:camera-password@10.0.0.7:8554/line-a",
        source_fps=24.0,
    )
    observe_stream_timing(
        "rtsp://camera-user:camera-password@10.0.0.7:8554/line-a",
        "behavior_pose_batch",
        12.5,
    )

    snapshot = stream_health_snapshot()[0]

    assert snapshot["source_label"] == "rtsp://10.0.0.7:8554/line-a"
    assert "camera-user" not in str(snapshot)
    assert "camera-password" not in str(snapshot)
    assert snapshot["timings"]["behavior_pose_batch"]["last_ms"] == 12.5


def test_stream_health_endpoint_returns_stage_metrics():
    clear_stream_health()
    source = "rtsp://camera/metrics"
    update_stream_health(source, source_fps=24.0)
    observe_stream_timing(source, "behavior_pose_batch", 12.5)
    result = streams_health()

    assert result["stream_count"] == 1
    assert "behavior_pose_batch" in result["streams"][0]["timings"]
    assert "rates_fps" in result["streams"][0]
    assert "behavior_classifier" not in result["streams"][0]["timings"]


def test_stream_health_reports_rolling_percentiles_and_event_rate():
    clear_stream_health()
    source = "rtsp://camera/line-a"
    for value in (10.0, 20.0, 30.0, 40.0):
        observe_stream_timing(source, "ppe_compute", value)
    mark_stream_event(source, "preview_sent")
    mark_stream_event(source, "preview_sent")

    snapshot = stream_health_snapshot()[0]
    timing = snapshot["timings"]["ppe_compute"]

    assert timing["samples"] == 4
    assert timing["last_ms"] == 40.0
    assert timing["mean_ms"] == 25.0
    assert timing["p50_ms"] == 25.0
    assert timing["p95_ms"] == 38.5
    assert snapshot["rates_fps"]["preview_sent"] > 0


def test_stream_health_reset_clears_ephemeral_samples():
    update_stream_health("rtsp://camera/reset-test", source_fps=24.0)

    assert reset_streams_health() == {"status": "reset"}
    assert streams_health() == {"stream_count": 0, "streams": []}


def test_behavior_worker_stop_releases_subscription_and_tracker():
    async def run():
        started = asyncio.Event()

        class Subscription:
            def __init__(self):
                self.closed = False

            async def get(self):
                started.set()
                await asyncio.Event().wait()

            async def close(self):
                self.closed = True

        subscription = Subscription()

        class Hubs:
            async def subscribe(self, *_args, **_kwargs):
                return subscription

        class Scheduler:
            def __init__(self):
                self.unregistered = []

            async def unregister_camera(self, key):
                self.unregistered.append(key)

        scheduler = Scheduler()
        worker = BehaviorStreamWorker(
            source="rtsp://camera/one",
            source_name="camera-one",
            fps=24,
            detector=FallDetector(),
            scheduler=scheduler,
            hubs=Hubs(),
        )
        worker.start()
        await asyncio.wait_for(started.wait(), timeout=1)
        await worker.stop()

        assert subscription.closed
        assert scheduler.unregistered == ["rtsp://camera/one"]
        assert worker._task is None

    asyncio.run(run())
