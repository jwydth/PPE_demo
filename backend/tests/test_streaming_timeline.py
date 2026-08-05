from app.schemas.streaming import StreamEvent
from app.services.stream_health import (
    clear_stream_health,
    increment_stream_health,
    observe_stream_timing,
    stream_health_snapshot,
    update_stream_health,
)


def test_stream_event_serializes_timeline_contract():
    event = StreamEvent(
        event="frame",
        frame_index=24,
        stream_epoch="epoch-a",
        media_pts_ms=1000.0,
        source_time_ms=1_800_000_000_000.0,
        inference_completed_ms=1_800_000_000_025.0,
        discontinuity_sequence=2,
        data={"frames": []},
    )

    payload = event.model_dump()
    assert payload["stream_epoch"] == "epoch-a"
    assert payload["media_pts_ms"] == 1000.0
    assert payload["source_time_ms"] == 1_800_000_000_000.0
    assert payload["discontinuity_sequence"] == 2


def test_health_exposes_hls_and_overlay_telemetry():
    clear_stream_health()
    source = "rtsp://user:secret@127.0.0.1:8554/stream1"
    increment_stream_health(source, hls_rebuffer_count=1, hls_dropped_video_frames=2)
    observe_stream_timing(source, "hls_live_delay", 2000.0)
    observe_stream_timing(source, "overlay_video_skew", 32.0)
    observe_stream_timing(source, "rendered_overlay_signed_skew", -32.0)
    update_stream_health(source, overlay_selection_mode="held")

    snapshot = stream_health_snapshot()[0]
    assert snapshot["hls_rebuffer_count"] == 1
    assert snapshot["hls_dropped_video_frames"] == 2
    assert snapshot["timings"]["hls_live_delay"]["last_ms"] == 2000.0
    assert snapshot["timings"]["overlay_video_skew"]["last_ms"] == 32.0
    assert snapshot["timings"]["rendered_overlay_signed_skew"]["last_ms"] == -32.0
    assert snapshot["overlay_selection_mode"] == "held"
    assert "secret" not in snapshot["source_label"]
