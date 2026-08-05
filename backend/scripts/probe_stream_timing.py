"""Ad-hoc probe: measure per-frame wall-clock delta reading an RTSP source,
the same way the live pipeline does (cv2.VideoCapture + TCP transport),
to find where in the stream real stalls occur."""
import os
import sys
import time

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|timeout;3000000"

import cv2

url = sys.argv[1] if len(sys.argv) > 1 else "rtsp://127.0.0.1:8554/stream1"
duration_s = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0

cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
if not cap.isOpened():
    print(f"FAILED to open {url}")
    sys.exit(1)

print(f"Opened {url}, reading for {duration_s}s...")
start = time.perf_counter()
last = start
n = 0
deltas = []
while time.perf_counter() - start < duration_s:
    t0 = time.perf_counter()
    ok, frame = cap.read()
    t1 = time.perf_counter()
    if not ok:
        print(f"frame {n}: READ FAILED at t={t1-start:.2f}s")
        continue
    delta_ms = (t1 - last) * 1000
    deltas.append(delta_ms)
    if delta_ms > 150:
        print(f"frame {n}: STALL {delta_ms:.0f}ms (t={t1-start:.2f}s, read_call={  (t1-t0)*1000:.0f}ms)")
    last = t1
    n += 1

cap.release()
deltas.sort()
if deltas:
    print(f"\nTotal frames: {n}")
    print(f"min={deltas[0]:.0f}ms p50={deltas[len(deltas)//2]:.0f}ms p95={deltas[int(len(deltas)*0.95)]:.0f}ms max={deltas[-1]:.0f}ms")
