"""Measure LL-HLS playlist continuity and timestamped metadata delivery."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from urllib.parse import urlencode, urljoin, urlsplit

import httpx
import websockets


@dataclass(slots=True)
class StreamSamples:
    source: str
    playlist_updates: list[float] = field(default_factory=list)
    playlist_request_ms: list[float] = field(default_factory=list)
    playlist_errors: list[str] = field(default_factory=list)
    metadata_ages_ms: list[float] = field(default_factory=list)
    metadata_events: dict[str, int] = field(default_factory=dict)
    metadata_errors: list[str] = field(default_factory=list)
    _last_fingerprint: str | None = None

    def summary(self, duration: float) -> dict:
        update_intervals = [
            (right - left) * 1000.0
            for left, right in zip(self.playlist_updates, self.playlist_updates[1:])
        ]
        return {
            "source": self.source,
            "playlist_update_hz": round(len(self.playlist_updates) / duration, 3),
            "playlist_update_interval_ms": statistics(update_intervals),
            "playlist_request_ms": statistics(self.playlist_request_ms),
            "playlist_errors": self.playlist_errors,
            "metadata_events": self.metadata_events,
            "metadata_delivery_age_ms": statistics(self.metadata_ages_ms),
            "metadata_errors": self.metadata_errors,
        }


def statistics(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0, "samples": 0}
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower, upper = math.floor(position), math.ceil(position)
        if lower == upper:
            return ordered[lower]
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    return {
        "mean": round(fmean(values), 3),
        "p50": round(percentile(0.50), 3),
        "p95": round(percentile(0.95), 3),
        "max": round(max(values), 3),
        "samples": len(values),
    }


def playlist_fingerprint(content: str) -> str:
    sequence = re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", content)
    parts = re.findall(r'URI="([^"]+)"', content)
    return f"{sequence.group(1) if sequence else 'none'}:{parts[-1] if parts else 'none'}"


def hls_url(base: str, source: str, path_suffix: str = "") -> str:
    path = urlsplit(source).path.strip("/")
    return f"{base.rstrip('/')}/{path}{path_suffix}/index.m3u8"


async def poll_playlist(
    base: str,
    samples: StreamSamples,
    stop: asyncio.Event,
    path_suffix: str = "",
) -> None:
    master_url = hls_url(base, samples.source, path_suffix)
    url: str | None = None
    async with httpx.AsyncClient(timeout=3.0, follow_redirects=True) as client:
        while not stop.is_set():
            started = time.perf_counter()
            try:
                if url is None:
                    master = await client.get(master_url)
                    master.raise_for_status()
                    variant_path = next(
                        line.strip()
                        for line in master.text.splitlines()
                        if line.strip() and not line.startswith("#")
                    )
                    url = urljoin(str(master.url), variant_path)
                response = await client.get(url)
                response.raise_for_status()
                samples.playlist_request_ms.append((time.perf_counter() - started) * 1000.0)
                fingerprint = playlist_fingerprint(response.text)
                if fingerprint != samples._last_fingerprint:
                    samples._last_fingerprint = fingerprint
                    samples.playlist_updates.append(time.perf_counter())
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                if error not in samples.playlist_errors:
                    samples.playlist_errors.append(error)
                url = None
            await asyncio.sleep(0.2)


async def consume_metadata(websocket_base: str, samples: StreamSamples, stop: asyncio.Event) -> None:
    query = urlencode({
        "video_name": samples.source,
        "enable_ppe": "true",
        "enable_zone": "false",
        "enable_fall": "true",
        "enable_sign": "true",
        "metadata_only": "true",
    })
    while not stop.is_set():
        try:
            async with websockets.connect(
                f"{websocket_base.rstrip('/')}/ws/stream?{query}",
                max_size=None,
            ) as websocket:
                while not stop.is_set():
                    try:
                        message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    if isinstance(message, bytes):
                        error = "Unexpected binary frame in metadata-only mode"
                        if error not in samples.metadata_errors:
                            samples.metadata_errors.append(error)
                        continue
                    payload = json.loads(message)
                    event = str(payload.get("event", "unknown"))
                    samples.metadata_events[event] = samples.metadata_events.get(event, 0) + 1
                    source_time_ms = payload.get("source_time_ms")
                    if isinstance(source_time_ms, (int, float)):
                        samples.metadata_ages_ms.append(
                            max(0.0, time.time() * 1000.0 - float(source_time_ms))
                        )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if error not in samples.metadata_errors:
                samples.metadata_errors.append(error)
            await asyncio.sleep(0.5)


async def run(args) -> dict:
    samples = [StreamSamples(source) for source in args.sources]
    stop = asyncio.Event()
    tasks = [
        task
        for sample in samples
        for task in (
            asyncio.create_task(
                poll_playlist(
                    args.hls_base,
                    sample,
                    stop,
                    args.hls_path_suffix,
                )
            ),
            asyncio.create_task(consume_metadata(args.websocket_base, sample, stop)),
        )
    ]
    await asyncio.sleep(args.warmup)
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            await client.delete(f"{args.api_base.rstrip('/')}/health/streams")
        except Exception:
            pass
    for sample in samples:
        sample.playlist_updates.clear()
        sample.playlist_request_ms.clear()
        sample.metadata_ages_ms.clear()
        sample.metadata_events.clear()
        sample.playlist_errors.clear()
        sample.metadata_errors.clear()
    started = time.perf_counter()
    await asyncio.sleep(args.duration)
    measured = time.perf_counter() - started
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            health = (await client.get(f"{args.api_base.rstrip('/')}/health/streams")).json()
        except Exception as exc:
            health = {"error": f"{type(exc).__name__}: {exc}"}
    stop.set()
    await asyncio.gather(*tasks)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(measured, 3),
        "streams": [sample.summary(measured) for sample in samples],
        "health": health,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", nargs="+", required=True)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--warmup", type=float, default=5.0)
    parser.add_argument("--hls-base", default="http://127.0.0.1:8888")
    parser.add_argument(
        "--hls-path-suffix",
        default="",
        help="Suffix appended to each RTSP stream path, for example _annotated",
    )
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--websocket-base", default="ws://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("benchmarks/llhls.json"))
    args = parser.parse_args()
    report = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
