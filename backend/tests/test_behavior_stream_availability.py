"""What the behavior worker tells the UI about its own availability.

Only a hard failure reaches the operator. The startup check
(FallDetector.unsupported_source_reason) compares the raw source rate against
the canonical timeline, which is window quality *before* pose-gap repair —
repair routinely carries a source that projects as too slow, so a 15 FPS camera
projects 62% against a 70% gate and classifies continuously anyway. Reporting
that projection as an error contradicted the falls and runs visibly being
detected on the same screen, so it is now a log line only.
"""

import logging

from app.services.behavior_stream import BehaviorStreamWorker


class _StubSession:
    def __init__(self, reason: str | None) -> None:
        self._reason = reason
        self.last_classifier_ms = 0.0

    def unsupported_source_reason(self) -> str | None:
        return self._reason


class _StubDetector:
    def __init__(self, reason: str | None) -> None:
        self.session = _StubSession(reason)

    def create_live_session(self, **_kwargs: object) -> _StubSession:
        return self.session


class _StubScheduler:
    pass


def _worker(reason: str | None) -> BehaviorStreamWorker:
    return BehaviorStreamWorker(
        source="rtsp://example/stream1",
        source_name="stream1",
        fps=15.0,
        detector=_StubDetector(reason),  # type: ignore[arg-type]
        scheduler=_StubScheduler(),  # type: ignore[arg-type]
    )


SLOW_SOURCE = "source runs at 15 FPS but the behavior classifier needs a 24 FPS timeline"


def test_a_sub_canonical_source_is_never_reported_to_the_ui():
    worker = _worker(SLOW_SOURCE)

    assert worker.unavailable is None
    assert worker.snapshot()[1] is None


def test_the_sub_canonical_projection_is_still_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.behavior_stream"):
        _worker(SLOW_SOURCE)

    # Kept as diagnostics: the one place that records why a source is marginal,
    # which is what you want when tuning the publisher.
    assert SLOW_SOURCE in caplog.text


def test_a_healthy_source_logs_nothing_and_reports_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.behavior_stream"):
        worker = _worker(None)

    assert worker.snapshot()[1] is None
    assert "canonical rate" not in caplog.text


def test_a_real_failure_is_reported():
    worker = _worker(SLOW_SOURCE)

    # Set by the worker loop's exception handlers, at which point the loop is
    # dead and nothing further is produced. This is the only state the operator
    # is told about, which is what keeps it worth believing.
    worker.unavailable = "Behavior detection failed during live stream processing."

    assert worker.snapshot()[1] == "Behavior detection failed during live stream processing."
