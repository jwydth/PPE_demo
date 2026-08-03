import asyncio

from app.routers.testing import streams_health
from app.services.behavior_stream import BehaviorStreamWorker
from app.services.fall_detector import FallDetector
from app.services.stream_health import (
    clear_stream_health,
    stream_health_snapshot,
    update_stream_health,
)


def test_stream_health_redacts_url_credentials():
    clear_stream_health()
    update_stream_health(
        "rtsp://camera-user:camera-password@10.0.0.7:8554/line-a",
        source_fps=24.0,
        pose_inference_ms=12.5,
    )

    snapshot = stream_health_snapshot()[0]

    assert snapshot["source_label"] == "rtsp://10.0.0.7:8554/line-a"
    assert "camera-user" not in str(snapshot)
    assert "camera-password" not in str(snapshot)
    assert snapshot["pose_inference_ms"] == 12.5


def test_stream_health_endpoint_returns_stage_metrics():
    result = streams_health()

    assert result["stream_count"] == 1
    assert "xgboost_ms" in result["streams"][0]
    assert "websocket_ms" in result["streams"][0]
    assert "behavior_total_ms" in result["streams"][0]


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
