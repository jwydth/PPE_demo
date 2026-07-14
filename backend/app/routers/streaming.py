import asyncio
import itertools
import logging
from pathlib import Path
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse
from app.services.ppe_detector import PPEDetector
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

@router.websocket("/ws/stream")
async def stream_video_ws(
    websocket: WebSocket,
    video_name: str = Query(...),
    enable_ppe: bool = Query(True),
    enable_zone: bool = Query(True),
    enable_fall: bool = Query(False),
):
    global _current_cancel
    conn_id = next(_conn_counter)
    await websocket.accept()
    ensure_upload_dir()
    logger.info(f"[conn {conn_id}] WS accepted (ppe={enable_ppe}, zone={enable_zone}, fall={enable_fall})")
    
    # Dynamic settings state
    settings_state = {
        "enable_ppe": enable_ppe,
        "enable_zone": enable_zone,
        "enable_fall": enable_fall,
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
                data = await websocket.receive_json()
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
                if "enable_ppe" in new_settings:
                    settings_state["enable_ppe"] = bool(new_settings["enable_ppe"])
                if "enable_zone" in new_settings:
                    settings_state["enable_zone"] = bool(new_settings["enable_zone"])
                if "enable_fall" in new_settings:
                    settings_state["enable_fall"] = bool(new_settings["enable_fall"])
                if "viewing" in new_settings:
                    settings_state["viewing"] = bool(new_settings["viewing"])
                logger.info(f"[conn {conn_id}] [SIGNAL] Received dynamic settings update: {settings_state}")
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
    try:
        async for event in stream_gen:
            if disconnect_event.is_set():
                logger.info(f"[conn {conn_id}] disconnect_event set — stopping stream loop after {sent} events")
                break
            if cancel_event.is_set():
                logger.info(f"[conn {conn_id}] superseded by a newer stream — stopping after {sent} events")
                break
            await websocket.send_text(event.model_dump_json())
            sent += 1
            # Force a yield to the event loop. send_text() often completes without
            # suspending (send buffer has room), which would starve the settings
            # listener task and prevent it from ever reading the client's close
            # frame — leaving this infinite stream running forever.
            await asyncio.sleep(0)
            if sent % 120 == 0:
                logger.info(f"[conn {conn_id}] sent {sent} events (client_state={websocket.client_state.name}, disconnect_event={disconnect_event.is_set()})")
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
        # Close the generator (fully tears down this model.track() session) BEFORE
        # releasing the lock. PPEDetector is a singleton, so the next stream must
        # not start model.track() on the shared model while this one is still
        # tearing down — that corrupts the predictor and the new stream stalls.
        # The 3s RTSP read timeout caps how long aclose() can block here.
        try:
            await asyncio.wait_for(stream_gen.aclose(), timeout=6.0)
            logger.info(f"[conn {conn_id}] generator closed")
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[conn {conn_id}] generator aclose did not finish cleanly: {e}")
        stream_lock.release()
        logger.info(f"[conn {conn_id}] released stream lock for {video_name}")
        try:
            await websocket.close()
        except Exception:
            pass