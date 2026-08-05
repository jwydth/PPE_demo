import asyncio
import itertools
import logging
import time
from collections import deque
from pathlib import Path
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse
from app.schemas.streaming import StreamEvent
from app.services.ppe_detector import PPEDetector
from app.services.stream_health import (
    increment_stream_health,
    mark_stream_event,
    observe_stream_timing,
    update_stream_health,
)
from app.storage.local_paths import UPLOAD_DIR, ensure_upload_dir

router = APIRouter(tags=["streaming"])
logger = logging.getLogger(__name__)

_detector = PPEDetector()
# Map of video_name -> Lock to serialize connections for the same camera/video.
# Prevents duplicate trackers for the same source on page reloads/StrictMode double-mounts.
_stream_locks: dict[str, asyncio.Lock] = {}
# Monotonic counter so each WebSocket connection has a stable id in the logs.
_conn_counter = itertools.count(1)
# Map of video_name -> Event to cancel orphaned connections for a specific camera/video.
_current_cancels: dict[str, asyncio.Event] = {}
# How long listen_for_settings() waits for a client message before probing
# liveness with a ping. Bounds how long a truly-dead connection (dropped
# network, sleep, a hard reload that skips a clean WS close frame) can hold
# a camera's stream_lock — without this, a plain receive_json() call just
# blocks forever, and nothing else in the connection detects the disconnect.
_LIVENESS_PROBE_SECONDS = 15.0

@router.websocket("/ws/stream")
async def stream_video_ws(
    websocket: WebSocket,
    video_name: str = Query(...),
    enable_ppe: bool = Query(True),
    enable_zone: bool = Query(True),
    enable_fall: bool = Query(False),
    enable_sign: bool = Query(True),
    metadata_only: bool = Query(False),
):
    global _current_cancel
    conn_id = next(_conn_counter)
    await websocket.accept()
    ensure_upload_dir()
    logger.info(
        "[conn %s] WS accepted (ppe=%s, zone=%s, fall=%s, metadata_only=%s)",
        conn_id,
        enable_ppe,
        enable_zone,
        enable_fall,
        metadata_only,
    )
    
    # Dynamic settings state
    settings_state = {
        "enable_ppe": enable_ppe,
        "enable_zone": enable_zone,
        "enable_fall": enable_fall,
        "enable_sign": enable_sign,
        "metadata_only": metadata_only,
        "dismissed_signatures": [],
        "dismissed_ppe_signatures": [],
        "reload_zones": False,
        "viewing": True,
    }

    # Set when the client disconnects. The settings listener (which calls
    # receive_json) is the only place a disconnect is reliably detected, so it
    # signals the main streaming loop to stop. Otherwise an infinite RTSP stream
    # keeps running forever and never releases _stream_lock.
    disconnect_event = asyncio.Event()

    # Set when a newer connection wants to take over for this specific stream.
    # Allows this stream to be superseded even if the browser never closed it (orphaned WebSocket).
    cancel_event = asyncio.Event()
    previous_cancel = _current_cancels.get(video_name)
    _current_cancels[video_name] = cancel_event
    if previous_cancel is not None:
        previous_cancel.set()
        logger.info(f"[conn {conn_id}] superseding previous stream for {video_name} — signalled it to stop")
    
    # Resolve the video path
    is_url = video_name.startswith(("rtsp://", "rtmp://", "http://", "https://"))
    if is_url:
        video_path = video_name
    else:
        video_path = UPLOAD_DIR / video_name
        if not video_path.exists():
            root_path = Path(video_name)
            if root_path.exists():
                video_path = root_path

    if not is_url and not Path(video_path).exists():
        await websocket.send_json({"event": "error", "data": {"message": f"Video {video_name} not found"}})
        await websocket.close()
        return

    logger.info(f"Starting {'live' if is_url else 'simulated'} stream for {video_name} (initial ppe={enable_ppe}, zone={enable_zone}, fall={enable_fall})")
    
    # Task to handle incoming setting updates
    async def listen_for_settings():
        logger.info(f"[conn {conn_id}] [SIGNAL] Settings listener task started")
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_json(), timeout=_LIVENESS_PROBE_SECONDS
                )
            except asyncio.TimeoutError:
                # No message in a while — probe liveness instead of blocking
                # forever. A truly-dead peer (dropped network, sleep, a hard
                # reload that skips a clean WS close frame) makes this send
                # fail, which is treated the same as a disconnect below. A
                # live-but-idle client just gets an unrecognized "ping" event
                # it silently ignores (see useLiveStream.ts's onmessage).
                try:
                    await websocket.send_json({"event": "ping", "data": {}})
                except Exception as e:
                    logger.info(f"[conn {conn_id}] [SIGNAL] Liveness probe failed ({type(e).__name__}: {e}) — treating as disconnect")
                    disconnect_event.set()
                    break
                continue
            except WebSocketDisconnect:
                logger.info(f"[conn {conn_id}] [SIGNAL] Client disconnected (WebSocketDisconnect) — signalling stream to stop")
                disconnect_event.set()
                break
            except Exception as e:
                # An abrupt close can surface as something other than
                # WebSocketDisconnect — treat any receive failure as a
                # disconnect so the stream loop stops and releases the lock.
                logger.warning(f"[conn {conn_id}] [SIGNAL] receive_json failed ({type(e).__name__}: {e}) — treating as disconnect")
                disconnect_event.set()
                break

            if data.get("event") == "update_settings":
                new_settings = data.get("data", {})
                if "features" in new_settings:
                    feats = new_settings["features"]
                    if "ppe_detection" in feats:
                        settings_state["enable_ppe"] = bool(feats["ppe_detection"])
                    if "zone_monitoring" in feats:
                        settings_state["enable_zone"] = bool(feats["zone_monitoring"])
                    if "behavior_detection" in feats or "fall_detection" in feats:
                        settings_state["enable_fall"] = bool(feats.get("behavior_detection", feats.get("fall_detection")))
                else:
                    if "enable_ppe" in new_settings:
                        settings_state["enable_ppe"] = bool(new_settings["enable_ppe"])
                    if "enable_zone" in new_settings:
                        settings_state["enable_zone"] = bool(new_settings["enable_zone"])
                    if "enable_fall" in new_settings:
                        settings_state["enable_fall"] = bool(new_settings["enable_fall"])
                    if "enable_sign" in new_settings:
                        settings_state["enable_sign"] = bool(new_settings["enable_sign"])
                if "viewing" in new_settings:
                    settings_state["viewing"] = bool(new_settings["viewing"])
                logger.debug(f"[conn {conn_id}] [SIGNAL] Received dynamic settings update: {settings_state}")
            elif data.get("event") == "dismiss_suggestion":
                sig = data.get("data", {}).get("suggestion_id")
                if sig:
                    settings_state["dismissed_signatures"].append(sig)
                    logger.info(f"[conn {conn_id}] [SIGNAL] Queued dismissal for suggestion: {sig}")
            elif data.get("event") == "dismiss_ppe_suggestion":
                sig = data.get("data", {}).get("suggestion_id")
                if sig:
                    settings_state["dismissed_ppe_signatures"].append(sig)
                    logger.info(f"[conn {conn_id}] [SIGNAL] Queued PPE suggestion dismissal: {sig}")
            elif data.get("event") == "reload_zones":
                settings_state["reload_zones"] = True
                logger.info(f"[conn {conn_id}] [SIGNAL] Zone reload requested by client")
            elif data.get("event") == "playback_metrics":
                metrics = data.get("data", {})
                update_stream_health(
                    video_name,
                    hls_rebuffer_count=max(0, int(metrics.get("rebuffer_count", 0))),
                    hls_dropped_video_frames=max(
                        0,
                        int(metrics.get("dropped_video_frames", 0)),
                    ),
                    overlay_selection_mode=(
                        metrics.get("overlay_selection_mode")
                        if metrics.get("overlay_selection_mode")
                        in {"exact", "interpolated", "held", "missing"}
                        else "missing"
                    ),
                )
                for metric_name, timing_name in (
                    ("live_delay_ms", "hls_live_delay"),
                    ("overlay_skew_ms", "overlay_video_skew"),
                ):
                    value = metrics.get(metric_name)
                    if isinstance(value, (int, float)) and value >= 0:
                        observe_stream_timing(video_name, timing_name, float(value))
                signed_skew = metrics.get("rendered_overlay_signed_skew_ms")
                if isinstance(signed_skew, (int, float)):
                    observe_stream_timing(
                        video_name,
                        "rendered_overlay_signed_skew",
                        float(signed_skew),
                    )

    settings_task = asyncio.create_task(listen_for_settings())

    # Wait up to 12 s for any previous stream to finish closing its model.track()
    # session. The superseded stream breaks within one frame, then tears down
    # (aclose, capped at 6 s) and releases the lock — so 12 s leaves comfortable
    # margin.
    if video_name not in _stream_locks:
        _stream_locks[video_name] = asyncio.Lock()
    stream_lock = _stream_locks[video_name]

    logger.info(f"[conn {conn_id}] acquiring stream lock for {video_name} (locked={stream_lock.locked()})")
    try:
        await asyncio.wait_for(stream_lock.acquire(), timeout=12.0)
        logger.info(f"[conn {conn_id}] acquired stream lock for {video_name}")
    except asyncio.TimeoutError:
        logger.warning(f"[conn {conn_id}] Stream lock timeout for {video_name} — previous stream did not release in time")
        try:
            await websocket.send_json({"event": "error", "data": {"message": "Previous stream still shutting down, please try again in a few seconds."}})
            await websocket.close()
        except Exception:
            pass
        settings_task.cancel()
        return

    stream_gen = _detector.stream_video(
        video_path,
        video_name,
        settings_state=settings_state,
    )
    sent = 0
    dropped_frames = 0
    # Set when this connection loses its slot to a newer one for the same
    # camera (see cancel_event above). The frontend's auto-reconnect (which
    # retries any *unexpected* close, e.g. React StrictMode's dev-mode double
    # mount) must NOT retry this case — reconnecting here would just re-cancel
    # the newer connection right back, an infinite ping-pong between the two
    # tabs/sessions. A distinct close code lets it tell the difference.
    superseded = False

    # Decouples pulling from the pipeline generator (inference, tracking,
    # violation recording) from sending over the websocket (PERF_PLAN.md Tier
    # 4.2). Before this, both happened in one sequential loop: a slow client
    # (backgrounded tab, poor network) stalled the *next* pipeline call too,
    # pausing inference/tracking, and once the client caught up it received a
    # burst of stale frames instead of the current one. Now a producer task
    # drives the pipeline at full speed regardless of send speed — "frame"
    # (preview-image) events are coalesced into a single latest-only slot, so
    # a slow client only ever gets the newest one and older ones are dropped,
    # never queued. Every other event type (violation/zone_violation/
    # behavior_incident/summary/error/start/end/...) represents something
    # already persisted and must never be dropped, so those go on an unbounded
    # FIFO instead.
    latest_frame: StreamEvent | None = None
    reliable_events: deque[StreamEvent] = deque()
    new_event = asyncio.Event()
    producer_done = asyncio.Event()
    producer_exc: list[BaseException] = []

    async def send_event(event: StreamEvent) -> None:
        # Binary JPEG frame first, then the JSON envelope referencing it via
        # has_image — same connection, so WS delivers them to the client in
        # this order (PERF_PLAN.md Tier 2.2: raw bytes instead of base64-in-JSON).
        send_started = time.perf_counter()
        if event.image_bytes is not None:
            await websocket.send_bytes(event.image_bytes)
        await websocket.send_text(event.model_dump_json())
        websocket_ms = (time.perf_counter() - send_started) * 1000.0
        observe_stream_timing(video_name, "websocket_send", websocket_ms)
        if event.source_time_ms is not None:
            now_ms = time.time() * 1000.0
            observe_stream_timing(
                video_name,
                "metadata_delivery_age",
                max(0.0, now_ms - event.source_time_ms),
            )
            if event.inference_completed_ms is not None:
                observe_stream_timing(
                    video_name,
                    "inference_ready_age",
                    max(0.0, event.inference_completed_ms - event.source_time_ms),
                )
            mark_stream_event(video_name, "metadata_sent")
        if event.event == "frame" and event.image_bytes is not None:
            increment_stream_health(video_name, preview_sent_frames=1)
            mark_stream_event(video_name, "preview_sent")

    async def produce() -> None:
        nonlocal latest_frame, dropped_frames
        try:
            async for event in stream_gen:
                if disconnect_event.is_set() or cancel_event.is_set():
                    break
                if event.event == "frame":
                    if latest_frame is not None:
                        dropped_frames += 1
                        increment_stream_health(video_name, preview_coalesced_frames=1)
                    latest_frame = event
                else:
                    reliable_events.append(event)
                new_event.set()
        except Exception as e:
            producer_exc.append(e)
        finally:
            # Close the generator (fully tears down this model.track() session)
            # here, in the same task that was iterating it, before the sender's
            # finally releases stream_lock. PPEDetector is a singleton, so the
            # next stream must not start model.track() on the shared pooled
            # instance while this one is still tearing down — that corrupts the
            # predictor and the new stream stalls. The 3s RTSP read timeout caps
            # how long aclose() can block here.
            try:
                await asyncio.wait_for(stream_gen.aclose(), timeout=6.0)
                logger.info(f"[conn {conn_id}] generator closed")
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"[conn {conn_id}] generator aclose did not finish cleanly: {e}")
            producer_done.set()
            new_event.set()

    producer_task = asyncio.create_task(produce())

    try:
        while True:
            if disconnect_event.is_set():
                logger.info(f"[conn {conn_id}] disconnect_event set — stopping stream loop after {sent} events")
                break
            if cancel_event.is_set():
                logger.info(f"[conn {conn_id}] superseded by a newer stream — stopping after {sent} events")
                superseded = True
                break
            if not reliable_events and latest_frame is None:
                if producer_done.is_set():
                    break
                new_event.clear()
                await new_event.wait()
                continue

            while reliable_events and not disconnect_event.is_set() and not cancel_event.is_set():
                await send_event(reliable_events.popleft())
                sent += 1
            if latest_frame is not None and not disconnect_event.is_set() and not cancel_event.is_set():
                event, latest_frame = latest_frame, None
                await send_event(event)
                sent += 1
            # Force a yield to the event loop. send_text() often completes without
            # suspending (send buffer has room), which would starve the settings
            # listener task and prevent it from ever reading the client's close
            # frame — leaving this infinite stream running forever.
            await asyncio.sleep(0)
            if sent % 120 == 0:
                logger.debug(
                    f"[conn {conn_id}] sent {sent} events, dropped {dropped_frames} stale frame(s) "
                    f"(client_state={websocket.client_state.name}, disconnect_event={disconnect_event.is_set()})"
                )
        if producer_exc:
            raise producer_exc[0]
    except WebSocketDisconnect:
        logger.info(f"[conn {conn_id}] WebSocketDisconnect raised in send loop after {sent} events")
    except Exception as e:
        logger.error(f"[conn {conn_id}] Error in streaming: {e}", exc_info=True)
        try:
            await websocket.send_json({"event": "error", "data": {"message": str(e)}})
        except Exception:
            pass
    finally:
        logger.info(f"[conn {conn_id}] entering cleanup (closing generator + releasing lock)")
        # Only clear the global if we're still the active stream — a newer
        # connection may have already replaced it.
        if _current_cancels.get(video_name) is cancel_event:
            _current_cancels.pop(video_name, None)
        settings_task.cancel()
        # Make sure produce() stops even if we got here via an exception path
        # where neither flag was set yet, then wait for it to actually finish
        # (it closes stream_gen itself in its own finally — see produce()).
        disconnect_event.set()
        try:
            await asyncio.wait_for(producer_task, timeout=8.0)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[conn {conn_id}] producer task did not finish cleanly: {e}")
        logger.info(f"[conn {conn_id}] sent {sent} event(s), dropped {dropped_frames} stale frame(s) total")
        stream_lock.release()
        logger.info(f"[conn {conn_id}] released stream lock for {video_name}")
        try:
            if superseded:
                await websocket.close(code=4001, reason="superseded")
            else:
                await websocket.close()
        except Exception:
            pass
