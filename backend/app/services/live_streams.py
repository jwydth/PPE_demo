"""Process-wide registry of sources that currently have a live stream.

Lives here rather than in routers/streaming.py so that services (analytics) can
read live-stream state without importing a router.

In-process only, like the rest of the streaming state — under multi-worker
uvicorn each worker sees only its own connections (same caveat as the report
scheduler, see docs/internal_deployment.md).
"""

from __future__ import annotations

import asyncio

# source (a camera source_key for live cameras, or an uploaded filename for
# simulated ones) -> the cancel Event of the connection that currently owns it.
# A newer connection for the same source supersedes the older one, so there is
# at most one owner per source at any moment.
_current_cancels: dict[str, asyncio.Event] = {}


def claim_stream(source: str, cancel_event: asyncio.Event) -> asyncio.Event | None:
    """Make `cancel_event` the owner of `source`.

    Returns the previous owner's event so the caller can signal that older
    connection to stop, or None when the source was idle.
    """
    previous = _current_cancels.get(source)
    _current_cancels[source] = cancel_event
    return previous


def release_stream(source: str, cancel_event: asyncio.Event) -> None:
    """Drop `source`, but only if `cancel_event` still owns it — a newer
    connection may have already taken over, and it must keep its slot."""
    if _current_cancels.get(source) is cancel_event:
        _current_cancels.pop(source, None)


def active_stream_sources() -> set[str]:
    """Sources with a connection streaming them right now."""
    return set(_current_cancels)
