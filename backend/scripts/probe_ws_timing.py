"""Ad-hoc probe: connect to the /ws/stream endpoint exactly like the frontend
does and measure wall-clock delta between successive image (binary) frames,
to see if lag is introduced by backend inference/encode/send rather than
by the RTSP source itself."""
import asyncio
import json
import sys
import time

import websockets

async def main():
    video_name = sys.argv[1] if len(sys.argv) > 1 else "rtsp://127.0.0.1:8554/stream1"
    duration_s = float(sys.argv[2]) if len(sys.argv) > 2 else 40.0
    url = f"ws://127.0.0.1:8000/ws/stream?video_name={video_name}&enable_ppe=true&enable_zone=true&enable_fall=false"

    async with websockets.connect(url, max_size=None, open_timeout=30) as ws:
        print(f"Connected to {url}")
        start = time.perf_counter()
        last_frame_t = None
        n_frames = 0
        deltas = []
        while time.perf_counter() - start < duration_s:
            msg = await ws.recv()
            t = time.perf_counter()
            if isinstance(msg, (bytes, bytearray)):
                if last_frame_t is not None:
                    delta_ms = (t - last_frame_t) * 1000
                    deltas.append(delta_ms)
                    if delta_ms > 250:
                        print(f"frame {n_frames}: STALL {delta_ms:.0f}ms (t={t-start:.2f}s)")
                last_frame_t = t
                n_frames += 1
            else:
                try:
                    data = json.loads(msg)
                    if data.get("event") not in ("frame",):
                        pass  # reliable events (violation/summary/etc.) — not timed
                except Exception:
                    pass

        deltas.sort()
        if deltas:
            print(f"\nTotal image frames: {n_frames}")
            print(f"min={deltas[0]:.0f}ms p50={deltas[len(deltas)//2]:.0f}ms "
                  f"p95={deltas[int(len(deltas)*0.95)]:.0f}ms max={deltas[-1]:.0f}ms")

asyncio.run(main())
