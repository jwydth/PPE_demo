"""Benchmark live preview cadence and backend stage timings.

The live pipeline always performs the PPE tracking pass because that pass
currently produces the preview frame. The matrix therefore isolates the two
optional GPU workloads: behavior and sign detection.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from urllib.parse import urlencode

import httpx
import websockets


CASES = {
    "ppe": {"enable_fall": False, "enable_sign": False},
    "ppe_sign": {"enable_fall": False, "enable_sign": True},
    "ppe_behavior": {"enable_fall": True, "enable_sign": False},
    "ppe_behavior_sign": {"enable_fall": True, "enable_sign": True},
}


@dataclass(slots=True)
class ClientSample:
    source: str
    preview_timestamps: list[float] = field(default_factory=list)
    frame_indexes: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self, measured_seconds: float) -> dict[str, object]:
        intervals = [
            (right - left) * 1000.0
            for left, right in zip(
                self.preview_timestamps,
                self.preview_timestamps[1:],
                strict=False,
            )
        ]
        return {
            "source": self.source,
            "preview_frames": len(self.preview_timestamps),
            "preview_fps": round(len(self.preview_timestamps) / measured_seconds, 3),
            "preview_interval_ms": _statistics(intervals),
            "gaps_over_100ms": sum(value > 100.0 for value in intervals),
            "gaps_over_250ms": sum(value > 250.0 for value in intervals),
            "gaps_over_500ms": sum(value > 500.0 for value in intervals),
            "gaps_over_1000ms": sum(value > 1000.0 for value in intervals),
            "first_frame_index": self.frame_indexes[0] if self.frame_indexes else None,
            "last_frame_index": self.frame_indexes[-1] if self.frame_indexes else None,
            "errors": self.errors,
        }


def _percentile(ordered: list[float], fraction: float) -> float:
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _statistics(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {
            "mean": 0.0,
            "p50": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "max": 0.0,
            "samples": 0,
        }
    ordered = sorted(values)
    return {
        "mean": round(fmean(values), 3),
        "p50": round(_percentile(ordered, 0.50), 3),
        "p95": round(_percentile(ordered, 0.95), 3),
        "p99": round(_percentile(ordered, 0.99), 3),
        "max": round(max(values), 3),
        "samples": len(values),
    }


async def _consume_stream(
    websocket_base: str,
    source: str,
    flags: dict[str, bool],
    measurement_started: asyncio.Event,
    stop_event: asyncio.Event,
) -> ClientSample:
    query = urlencode(
        {
            "video_name": source,
            "enable_ppe": "true",
            "enable_zone": "false",
            "enable_fall": str(flags["enable_fall"]).lower(),
            "enable_sign": str(flags["enable_sign"]).lower(),
        }
    )
    sample = ClientSample(source=source)
    pending_binary = False
    try:
        async with websockets.connect(
            f"{websocket_base}/ws/stream?{query}",
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
        ) as websocket:
            while not stop_event.is_set():
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                now = time.perf_counter()
                if isinstance(message, bytes):
                    pending_binary = True
                    continue
                payload = json.loads(message)
                if payload.get("event") == "error":
                    sample.errors.append(str(payload.get("data", {}).get("message")))
                if payload.get("event") == "frame" and pending_binary:
                    if measurement_started.is_set():
                        sample.preview_timestamps.append(now)
                        if payload.get("frame_index") is not None:
                            sample.frame_indexes.append(int(payload["frame_index"]))
                    pending_binary = False
    except Exception as exc:
        sample.errors.append(f"{type(exc).__name__}: {exc}")
    return sample


async def _poll_health(api_base: str, stop_event: asyncio.Event) -> list[dict]:
    samples: list[dict] = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        while not stop_event.is_set():
            try:
                response = await client.get(f"{api_base}/health/streams")
                response.raise_for_status()
                samples.append(response.json())
            except Exception as exc:
                samples.append({"error": f"{type(exc).__name__}: {exc}"})
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
    return samples


async def _run_case(
    api_base: str,
    websocket_base: str,
    case_name: str,
    sources: list[str],
    warmup_seconds: float,
    duration_seconds: float,
) -> dict[str, object]:
    flags = CASES[case_name]
    measurement_started = asyncio.Event()
    stop_streams = asyncio.Event()
    stop_health = asyncio.Event()
    started_at = datetime.now(timezone.utc).isoformat()
    stream_tasks = [
        asyncio.create_task(
            _consume_stream(
                websocket_base,
                source,
                flags,
                measurement_started,
                stop_streams,
            )
        )
        for source in sources
    ]
    await asyncio.sleep(warmup_seconds)
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.delete(f"{api_base}/health/streams")
        response.raise_for_status()
    health_task = asyncio.create_task(_poll_health(api_base, stop_health))
    measurement_started.set()
    await asyncio.sleep(duration_seconds)
    stop_streams.set()
    client_samples = await asyncio.gather(*stream_tasks)
    stop_health.set()
    health_samples = await health_task
    async with httpx.AsyncClient(timeout=5.0) as client:
        final_health = (await client.get(f"{api_base}/health/streams")).json()
    return {
        "case": case_name,
        "flags": {"ppe_tracking_always_on": True, **flags},
        "started_at": started_at,
        "warmup_seconds": warmup_seconds,
        "measured_seconds": duration_seconds,
        "clients": [sample.summary(duration_seconds) for sample in client_samples],
        "health_poll_samples": health_samples,
        "final_health": final_health,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", required=True, help="RTSP source; repeat for multiple cameras")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--ws-base", default="ws://127.0.0.1:8000")
    parser.add_argument("--duration", type=float, default=120.0)
    parser.add_argument("--warmup", type=float, default=20.0)
    parser.add_argument("--cases", nargs="+", choices=tuple(CASES), default=list(CASES))
    parser.add_argument("--pause", type=float, default=5.0, help="Seconds between cases")
    parser.add_argument("--output", type=Path, default=Path("benchmarks/stream-matrix.json"))
    args = parser.parse_args()

    report: dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": args.source,
        "note": "PPE tracking remains on because the current preview is produced from its result frames.",
        "cases": [],
    }
    for index, case_name in enumerate(args.cases):
        print(f"Running {case_name} ({index + 1}/{len(args.cases)})...")
        result = await _run_case(
            args.api_base,
            args.ws_base,
            case_name,
            args.source,
            args.warmup,
            args.duration,
        )
        report["cases"].append(result)
        if index + 1 < len(args.cases):
            await asyncio.sleep(args.pause)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {args.output.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
