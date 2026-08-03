import asyncio

import numpy as np

from app.services.behavior_inference import BehaviorInferenceScheduler
from app.services.fall_detector import FallDetector


def test_scheduler_batches_cameras_without_a_concurrency_limit(monkeypatch):
    async def run():
        monkeypatch.setattr("app.services.behavior_inference.settings.BEHAVIOR_BATCH_WAIT_MS", 10)
        monkeypatch.setattr("app.services.behavior_inference.settings.BEHAVIOR_BATCH_MAX_SIZE", 8)
        batches: list[list[str]] = []

        def process(requests):
            batches.append([request.camera_key for request in requests])
            return [request.camera_key for request in requests]

        scheduler = BehaviorInferenceScheduler(FallDetector(), batch_processor=process)
        results = await asyncio.gather(
            scheduler.infer("camera-a", np.zeros((2, 2, 3))),
            scheduler.infer("camera-b", np.zeros((2, 2, 3))),
            scheduler.infer("camera-c", np.zeros((2, 2, 3))),
        )
        await scheduler.shutdown()

        assert results == ["camera-a", "camera-b", "camera-c"]
        assert batches == [["camera-a", "camera-b", "camera-c"]]

    asyncio.run(run())


def test_scheduler_failures_are_returned_to_each_request(monkeypatch):
    async def run():
        monkeypatch.setattr("app.services.behavior_inference.settings.BEHAVIOR_BATCH_WAIT_MS", 5)

        def fail(_requests):
            raise RuntimeError("broken batch")

        scheduler = BehaviorInferenceScheduler(FallDetector(), batch_processor=fail)
        results = await asyncio.gather(
            scheduler.infer("camera-a", np.zeros((2, 2, 3))),
            scheduler.infer("camera-b", np.zeros((2, 2, 3))),
            return_exceptions=True,
        )
        health = scheduler.health_snapshot()
        await scheduler.shutdown()

        assert all(isinstance(result, RuntimeError) for result in results)
        assert health["camera-a"].failed_frames == 1
        assert health["camera-b"].failed_frames == 1

    asyncio.run(run())


def test_scheduler_uses_isolated_tracker_state_per_camera(monkeypatch):
    async def run():
        monkeypatch.setattr("app.services.behavior_inference.settings.BEHAVIOR_BATCH_WAIT_MS", 10)

        class FakePoseModel:
            def predict(self, images, **_kwargs):
                return list(images)

        class FakeDetector(FallDetector):
            def _ensure_model(self):
                return FakePoseModel()

        tracker_ids = iter(("tracker-a", "tracker-b"))

        class FakeTracker:
            def __init__(self, _detector):
                self.identity = next(tracker_ids)

            def update(self, result):
                return self.identity, result

            def close(self):
                pass

        scheduler = BehaviorInferenceScheduler(
            FakeDetector(),
            tracker_factory=FakeTracker,
        )
        results = await asyncio.gather(
            scheduler.infer("camera-a", np.zeros((2, 2, 3))),
            scheduler.infer("camera-b", np.ones((2, 2, 3))),
        )

        assert results[0][0] != results[1][0]
        assert scheduler.camera_keys == {"camera-a", "camera-b"}
        await scheduler.shutdown()

    asyncio.run(run())


def test_scheduler_continues_after_a_failed_batch(monkeypatch):
    async def run():
        monkeypatch.setattr("app.services.behavior_inference.settings.BEHAVIOR_BATCH_WAIT_MS", 0)
        calls = 0

        def process(requests):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("temporary failure")
            return [request.camera_key for request in requests]

        scheduler = BehaviorInferenceScheduler(FallDetector(), batch_processor=process)
        first = await asyncio.gather(
            scheduler.infer("camera-a", np.zeros((2, 2, 3))),
            return_exceptions=True,
        )
        second = await scheduler.infer("camera-b", np.zeros((2, 2, 3)))
        await scheduler.shutdown()

        assert isinstance(first[0], RuntimeError)
        assert second == "camera-b"

    asyncio.run(run())


def test_prepare_camera_warms_before_first_submission():
    async def run():
        events = []

        class FakePoseModel:
            def predict(self, images, **_kwargs):
                events.append("pose")
                return list(images) if isinstance(images, list) else [images]

        class FakeDetector(FallDetector):
            def _ensure_model(self):
                return FakePoseModel()

        class FakeTracker:
            def __init__(self, _detector):
                events.append("tracker")

            def update(self, result):
                return result

            def close(self):
                pass

        scheduler = BehaviorInferenceScheduler(
            FakeDetector(), tracker_factory=FakeTracker
        )
        await scheduler.prepare_camera("camera-a")

        assert events == ["pose", "tracker"]
        assert scheduler.camera_keys == {"camera-a"}
        assert scheduler.health_snapshot() == {}
        await scheduler.shutdown()

    asyncio.run(run())
