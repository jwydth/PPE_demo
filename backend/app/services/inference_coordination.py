"""Small process-wide coordination primitives for shared CUDA execution."""

from __future__ import annotations

import itertools
import threading
import time
from contextlib import contextmanager


# Multiple independent Ultralytics predictors launching from Python threads can
# contend on the same CUDA context and produce long latency spikes even when
# average GPU utilization is low. Temporal behavior gets batching; PPE preview
# is latest-only, so serializing the short CUDA launch sections is preferable
# to unpredictable concurrent launches.
class PriorityInferenceLock:
    """A re-entrant priority lock with deadlines that prevent starvation."""

    _MAX_WAIT_SECONDS = {1: 0.040, 2: 0.500}

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._waiters: list[tuple[int, int, int, float]] = []
        self._counter = itertools.count()
        self._owner: int | None = None
        self._depth = 0

    def acquire(self, priority: int = 1) -> None:
        thread_id = threading.get_ident()
        with self._condition:
            if self._owner == thread_id:
                self._depth += 1
                return
            ticket = (priority, next(self._counter), thread_id, time.monotonic())
            self._waiters.append(ticket)
            while self._owner is not None or self._next_waiter() != ticket:
                self._condition.wait()
            self._waiters.remove(ticket)
            self._owner = thread_id
            self._depth = 1

    def _next_waiter(self) -> tuple[int, int, int, float]:
        now = time.monotonic()
        overdue = [
            waiter
            for waiter in self._waiters
            if waiter[0] in self._MAX_WAIT_SECONDS
            and now - waiter[3] >= self._MAX_WAIT_SECONDS[waiter[0]]
        ]
        if overdue:
            return min(
                overdue,
                key=lambda waiter: (
                    waiter[3] + self._MAX_WAIT_SECONDS[waiter[0]],
                    waiter[1],
                ),
            )
        return min(self._waiters, key=lambda waiter: (waiter[0], waiter[1]))

    def release(self) -> None:
        thread_id = threading.get_ident()
        with self._condition:
            if self._owner != thread_id:
                raise RuntimeError("GPU inference lock released by a non-owner thread")
            self._depth -= 1
            if self._depth == 0:
                self._owner = None
                self._condition.notify_all()

    def __enter__(self) -> "PriorityInferenceLock":
        self.acquire()
        return self

    def __exit__(self, *_args) -> None:
        self.release()

    @contextmanager
    def priority(self, value: int):
        self.acquire(value)
        try:
            yield self
        finally:
            self.release()


@contextmanager
def inference_priority(lock, priority: int):
    """Use priority support when available and preserve simple test locks."""
    priority_context = getattr(lock, "priority", None)
    with priority_context(priority) if callable(priority_context) else lock:
        yield


gpu_inference_lock = PriorityInferenceLock()
