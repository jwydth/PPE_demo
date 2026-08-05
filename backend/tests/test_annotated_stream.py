import asyncio
from types import SimpleNamespace

import numpy as np

from app.services.annotated_stream import (
    AnnotatedStateStore,
    AnnotatedStreamPublisher,
    RawFramePublisher,
    RenderFeatures,
    _person_violation_labels,
    annotated_rtsp_url,
    render_annotated_frame,
)
from app.core.config import settings
from app.services.frame_hub import FramePacket


def packet(frame_index: int, *, epoch: str = "epoch-a") -> FramePacket:
    return FramePacket(
        source="rtsp://127.0.0.1:8554/stream1",
        frame_index=frame_index,
        media_timestamp=frame_index / 24,
        captured_monotonic=100.0 + frame_index / 24,
        image=np.zeros((100, 160, 3), dtype=np.uint8),
        stream_epoch=epoch,
        media_pts_ms=frame_index * 1000 / 24,
        source_time_ms=1_800_000_000_000 + frame_index * 1000 / 24,
    )


def pose(track_id: int, x1: float, *, status: str = "others") -> dict:
    return {
        "track_id": track_id,
        "bbox": {"x1": x1, "y1": 10, "x2": x1 + 20, "y2": 60},
        "person_confidence": 0.9,
        "status": status,
        "score": 0.8,
        "keypoints": [[x1 + 5, 20], [x1 + 10, 30]],
    }


def ppe(
    track_id: int | None,
    x1: float,
    *,
    missing: list[str] | None = None,
    zone_name: str | None = None,
    zone_type: str | None = None,
) -> dict:
    return {
        "track_id": track_id,
        "bbox": {"x1": x1, "y1": 10, "x2": x1 + 20, "y2": 60},
        "missing_equipment": missing or [],
        "compliant": not missing,
        "role": "worker",
        "zone_name": zone_name,
        "zone_type": zone_type,
    }


def test_pose_geometry_is_repaired_before_compositor_snapshot():
    store = AnnotatedStateStore(fps=24)
    store.update_pose(packet(0), [pose(7, 10)])
    store.update_pose(packet(3), [pose(7, 16, status="falling")])

    first_gap = store.snapshot(packet(1))
    second_gap = store.snapshot(packet(2))

    assert first_gap.geometry_mode == "interpolated"
    assert second_gap.geometry_mode == "interpolated"
    assert first_gap.people[0].pose.bbox.x1 == 12
    assert second_gap.people[0].pose.bbox.x1 == 14
    assert first_gap.people[0].pose.synthetic is True


def test_ppe_is_semantic_only_and_expires_after_ttl():
    store = AnnotatedStateStore(fps=24)
    store.update_pose(packet(0), [pose(70, 10)])
    store.update_ppe(packet(0), [ppe(3, 10, missing=["Hardhat"])])
    store.update_pose(packet(7), [pose(70, 11)])
    store.update_pose(packet(9), [pose(70, 12)])

    valid = store.snapshot(packet(7))
    expired = store.snapshot(packet(9))

    assert valid.people[0].pose.track_id == 70
    assert valid.people[0].ppe is not None
    assert valid.people[0].ppe.source_track_id == 70
    assert valid.people[0].ppe.missing_equipment == ("Hardhat",)
    assert expired.people[0].ppe is None


def test_ppe_is_remapped_when_pose_for_the_same_frame_finishes_later():
    store = AnnotatedStateStore(fps=24)
    store.update_ppe(packet(0), [ppe(None, 10, missing=["Hardhat"])])
    store.update_pose(packet(0), [pose(70, 10)])
    store.update_pose(packet(4), [pose(70, 30)])

    snapshot = store.snapshot(packet(4))

    assert snapshot.people[0].pose.track_id == 70
    assert snapshot.people[0].ppe is not None
    assert snapshot.people[0].ppe.source_track_id == 70


def test_epoch_change_clears_pose_ppe_and_sign_state():
    store = AnnotatedStateStore(fps=24)
    store.update_pose(packet(0), [pose(1, 10)])
    store.update_ppe(packet(0), [ppe(1, 10)])
    store.update_signs(
        packet(0),
        [{"bbox": (2, 2, 12, 12), "class_id": 2, "conf": 0.9}],
    )

    before = store.snapshot(packet(1))
    after = store.snapshot(packet(0, epoch="epoch-b"))

    assert before.signs
    assert after.geometry_mode == "missing"
    assert after.people == ()
    assert after.signs == ()


def test_renderer_uses_pose_geometry_not_ppe_geometry():
    store = AnnotatedStateStore(fps=24)
    store.update_pose(packet(0), [pose(70, 10)])
    store.update_ppe(packet(0), [ppe(3, 12, missing=["Hardhat"])])

    snapshot = store.snapshot(packet(0))
    rendered = render_annotated_frame(packet(0).image, snapshot)

    assert snapshot.people[0].pose.bbox.x1 == 10
    assert snapshot.people[0].ppe is not None
    assert snapshot.people[0].ppe.bbox.x1 == 12
    assert np.any(rendered[10, 10] != 0)


def test_all_disabled_features_produce_a_clean_video_frame():
    store = AnnotatedStateStore(fps=24)
    source = packet(0)
    source.image[:] = 17
    store.update_pose(source, [pose(70, 10, status="falling")])
    store.update_ppe(source, [ppe(70, 10, missing=["Helmet"])])
    store.update_signs(
        source,
        [{"bbox": (2, 2, 12, 12), "class_id": 2, "conf": 0.9}],
    )
    disabled = RenderFeatures(False, False, False, False)

    snapshot = store.snapshot(source, disabled)
    rendered = render_annotated_frame(source.image, snapshot, disabled)

    assert snapshot.people == ()
    assert snapshot.signs == ()
    assert np.array_equal(rendered, source.image)

    store.update_pose(source, [pose(70, 10, status="falling")])
    store.update_ppe(source, [ppe(70, 10, missing=["Helmet"])])
    store.update_signs(
        source,
        [{"bbox": (2, 2, 12, 12), "class_id": 2, "conf": 0.9}],
    )
    assert store.snapshot(source, disabled).people == ()
    assert store.snapshot(source, disabled).signs == ()


def test_feature_toggles_select_only_the_corresponding_person_semantics():
    source = packet(0)
    ppe_only = RenderFeatures(ppe=True, zone=False, behavior=False, sign=False)
    ppe_store = AnnotatedStateStore(fps=24)
    ppe_store.set_features(ppe_only)
    ppe_store.update_pose(source, [pose(70, 10, status="falling")])
    ppe_store.update_ppe(
        source,
        [ppe(70, 10, missing=["Helmet"], zone_type="RESTRICTED")],
    )

    ppe_snapshot = ppe_store.snapshot(source, ppe_only)

    assert ppe_snapshot.geometry_mode == "ppe_fallback"
    assert _person_violation_labels(ppe_snapshot.people[0], ppe_only) == [
        "PPE: Missing Safety Helmet"
    ]

    behavior_only = RenderFeatures(ppe=False, zone=False, behavior=True, sign=False)
    behavior_store = AnnotatedStateStore(fps=24)
    behavior_store.set_features(behavior_only)
    behavior_store.update_pose(source, [pose(70, 10, status="falling")])
    behavior_store.update_ppe(source, [ppe(70, 10, missing=["Helmet"])])

    behavior_snapshot = behavior_store.snapshot(source, behavior_only)

    assert _person_violation_labels(
        behavior_snapshot.people[0], behavior_only
    ) == ["Behavior: Falling"]


def test_unified_labels_hide_normal_model_states_and_behavior_score():
    store = AnnotatedStateStore(fps=24)
    source = packet(0)
    store.update_pose(source, [pose(70, 10, status="others")])
    store.update_ppe(source, [ppe(70, 10)])

    snapshot = store.snapshot(source)
    person = snapshot.people[0]

    assert _person_violation_labels(person, RenderFeatures()) == []
    rendered = render_annotated_frame(source.image, snapshot)
    assert tuple(rendered[50, 10]) == (0, 200, 0)


def test_ppe_zone_and_behavior_violations_share_red_label_system():
    store = AnnotatedStateStore(fps=24)
    source = packet(0)
    store.update_pose(source, [pose(70, 10, status="falling")])
    store.update_ppe(
        source,
        [
            ppe(
                70,
                10,
                missing=["Helmet", "Vest"],
                zone_name="Line A",
                zone_type="RESTRICTED",
            )
        ],
    )

    snapshot = store.snapshot(source)
    labels = _person_violation_labels(snapshot.people[0], RenderFeatures())
    rendered = render_annotated_frame(source.image, snapshot)

    assert labels == [
        "PPE: Missing Helmet and Vest",
        "Zone: Restricted zone - Line A",
        "Behavior: Falling",
    ]
    assert all("%" not in label and "unknown" not in label.lower() for label in labels)
    assert np.any(np.all(rendered == (0, 0, 255), axis=2))


def test_behavior_renderer_draws_box_without_skeleton():
    source = packet(0)
    behavior_pose = pose(70, 10, status="falling")
    behavior_pose["keypoints"] = [[80, 80]]
    store = AnnotatedStateStore(fps=24)
    store.update_pose(source, [behavior_pose])

    rendered = render_annotated_frame(source.image, store.snapshot(source))

    assert tuple(rendered[50, 10]) == (0, 0, 255)
    assert tuple(rendered[80, 80]) == (0, 0, 0)


def test_ffmpeg_command_publishes_cfr_h264_without_b_frames(monkeypatch):
    monkeypatch.setattr(settings, "ANNOTATED_ENCODER", "libx264")
    publisher = RawFramePublisher(
        target_url="rtsp://127.0.0.1:8554/stream1_annotated",
        width=832,
        height=480,
        fps=24,
    )

    command = publisher.command()

    assert "libx264" in command
    assert command[command.index("-g") + 1] == "24"
    assert command[command.index("-bf") + 1] == "0"
    assert command[command.index("-fps_mode") + 1] == "cfr"
    assert command[-1] == "rtsp://127.0.0.1:8554/stream1_annotated"


def test_annotated_output_url_uses_separate_mediamtx_path(monkeypatch):
    monkeypatch.setattr(settings, "ANNOTATED_RTSP_BASE_URL", "rtsp://127.0.0.1:8554")
    monkeypatch.setattr(settings, "ANNOTATED_PATH_SUFFIX", "_annotated")

    assert annotated_rtsp_url("rtsp://camera.local/factory/stream1") == (
        "rtsp://127.0.0.1:8554/stream1_annotated"
    )


def test_compositor_releases_every_ordered_frame_without_waiting_for_ai(monkeypatch):
    import app.services.annotated_stream as annotated_stream

    packets = [packet(index) for index in range(4)]
    for item in packets:
        item.image[:] = item.frame_index

    class FakeSubscription:
        dropped_frames = 0
        queue = SimpleNamespace(qsize=lambda: 0)

        def __init__(self):
            self.items = [*packets, None]

        async def get(self):
            return self.items.pop(0)

        async def close(self):
            return None

    class FakeHubs:
        async def subscribe(self, *_args, **_kwargs):
            return FakeSubscription()

    class FakeRawPublisher:
        frames: list[int] = []
        restarts = 0
        encoder = "fake"

        def __init__(self, **_kwargs):
            pass

        def write(self, frame):
            self.frames.append(int(frame[0, 0, 0]))
            return True

        def close(self):
            pass

    monkeypatch.setattr(settings, "ANNOTATED_STREAM_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(annotated_stream, "RawFramePublisher", FakeRawPublisher)
    publisher = AnnotatedStreamPublisher(
        source=packets[0].source,
        fps=24,
        store=AnnotatedStateStore(fps=24),
        hubs=FakeHubs(),
    )

    asyncio.run(publisher._run())

    assert FakeRawPublisher.frames == [0, 1, 2, 3]


def test_only_one_publisher_can_own_an_annotated_output():
    class BlockingSubscription:
        dropped_frames = 0
        queue = SimpleNamespace(qsize=lambda: 0)

        async def get(self):
            await asyncio.Event().wait()

        async def close(self):
            return None

    class FakeHubs:
        async def subscribe(self, *_args, **_kwargs):
            return BlockingSubscription()

    async def exercise():
        first = AnnotatedStreamPublisher(
            source=packet(0).source,
            fps=24,
            store=AnnotatedStateStore(fps=24),
            hubs=FakeHubs(),
        )
        duplicate = AnnotatedStreamPublisher(
            source=packet(0).source,
            fps=24,
            store=AnnotatedStateStore(fps=24),
            hubs=FakeHubs(),
        )

        assert first.start() is True
        assert duplicate.start() is False
        await first.stop()
        assert duplicate.start() is True
        await duplicate.stop()

    asyncio.run(exercise())
