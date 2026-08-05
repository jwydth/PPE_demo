# Live stream performance findings

Measured on 2026-08-05 with two 24 FPS RTSP sources. Each controlled case used
a warm-up period followed by a 25-30 second measurement window.

## Root causes

1. The dashboard preview is gated by the PPE tracking result. With two streams,
   PPE preview output is about 10.3-10.5 FPS per camera even though capture is
   24 FPS. JPEG encoding and WebSocket delivery are not bottlenecks.
2. OpenCV RTSP delivery itself is bursty. A separate two-camera `VideoCapture`
   test, with no FastAPI or inference, measured about five 746 ms gaps in 25
   seconds on both streams. `ffplay` hides these gaps with playback buffering;
   the dashboard's latest-JPEG preview has no jitter buffer.
3. Behavior previously held the process-wide GPU lock during CPU BoT-SORT
   updates. In the initial matrix this raised PPE lock-wait p95 to 500-650 ms
   and reduced preview to 5-7.5 FPS.
4. Sign inference is not the primary cause. Its steady compute time was about
   16-21 ms per invocation and disabling it did not remove the baseline gaps.

## Implemented optimization

The Behavior scheduler now holds the global GPU lock only for Pose and ReID GPU
work. CPU tracker updates run outside the lock. In the focused post-change run:

| Case | Preview FPS per camera | Behavior FPS | Behavior queue | Preview p95 |
|---|---:|---:|---:|---:|
| PPE | 10.33-10.50 | - | - | 107-109 ms |
| PPE + Behavior | 10.00-10.13 | 24.01-24.05 | 0 | 176-187 ms |

Behavior therefore retains its required 24 FPS without halving preview
throughput. Tail latency is still worse than PPE-only because GPU work remains
serialized, but the former sustained 500-650 ms PPE lock contention is gone
after steady-state warm-up.

## Remaining architectural fix

For smooth playback, carry video through a buffered WebRTC/HLS player and send
AI results as timestamped metadata. Optimizing inference alone cannot make the
current preview smooth because the source delivers periodic OpenCV gaps and the
dashboard displays inference-gated JPEGs without a playback buffer.

Raw benchmark artifacts:

- `stream-matrix-before.json`: initial four-case matrix.
- `stream-matrix-after.json`: four-case validation; its first PPE case was
  interrupted by the development server reloader and should be ignored.
- `stream-focused-after.json`: clean focused PPE versus PPE+Behavior validation.
