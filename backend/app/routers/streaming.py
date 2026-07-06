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
# Ensures only one model.track() session runs at a time.
# Page reloads would otherwise start a second concurrent tracker before the first is torn down.
_stream_lock = asyncio.Lock()
# Monotonic counter so each WebSocket connection has a stable id in the logs.
_conn_counter = itertools.count(1)
# Cancel event of the currently active stream. A new connection sets this to tell
# the previous stream to stop, so a new stream always supersedes the old one
# instead of waiting (and timing out) on an orphaned WebSocket the browser never
# closed — which happens on reloads / React StrictMode double-mounts.
_current_cancel: "asyncio.Event | None" = None

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
    }

    # Set when the client disconnects. The settings listener (which calls
    # receive_json) is the only place a disconnect is reliably detected, so it
    # signals the main streaming loop to stop. Otherwise an infinite RTSP stream
    # keeps running forever and never releases _stream_lock.
    disconnect_event = asyncio.Event()

    # Set when a newer connection wants to take over. Allows this stream to be
    # superseded even if the browser never closed it (orphaned WebSocket).
    cancel_event = asyncio.Event()
    previous_cancel = _current_cancel
    _current_cancel = cancel_event
    if previous_cancel is not None:
        previous_cancel.set()
        logger.info(f"[conn {conn_id}] superseding previous stream — signalled it to stop")
    
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
    logger.info(f"[conn {conn_id}] acquiring stream lock (locked={_stream_lock.locked()})")
    try:
        await asyncio.wait_for(_stream_lock.acquire(), timeout=12.0)
        logger.info(f"[conn {conn_id}] acquired stream lock")
    except asyncio.TimeoutError:
        logger.warning(f"[conn {conn_id}] Stream lock timeout — previous stream did not release in time")
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
        if _current_cancel is cancel_event:
            _current_cancel = None
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
        _stream_lock.release()
        logger.info(f"[conn {conn_id}] released stream lock")
        try:
            await websocket.close()
        except Exception:
            pass

# for testing /ws/stream
@router.get("/test-stream", response_class=HTMLResponse)
async def get_test_page():
    return """
    <!DOCTYPE html>
    <html>
        <head>
            <title>FastAPI CV Pipeline Test</title>
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 40px; background: #f8f9fa; color: #333; }
                .container { max-width: 650px; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }
                .section { margin-bottom: 25px; padding-bottom: 20px; border-bottom: 1px solid #eee; }
                #messages { border: 1px solid #e0e0e0; height: 250px; overflow-y: auto; background: #282c34; color: #abb2bf; padding: 15px; border-radius: 6px; font-family: monospace; font-size: 0.9em; }
                button { background: #007bff; color: white; border: none; padding: 10px 15px; border-radius: 4px; cursor: pointer; font-weight: bold; }
                button:disabled { background: #6c757d; cursor: not-allowed; }
                input[type="file"] { margin-bottom: 10px; display: block; }
                .status-badge { display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 0.85em; font-weight: bold; background: #6c757d; color: white; }
            </style>
        </head>
        <body>
            <div class="container">
                <h2>Continuous Processing Pipeline Test</h2>
                
                <div class="section">
                    <h3>Step 1: Upload Video Template</h3>
                    <form id="uploadForm">
                        <input type="file" id="videoFile" accept="video/mp4" required />
                        <button type="submit" id="uploadBtn">Upload Video</button>
                    </form>
                    <p id="uploadStatus"></p>
                </div>

                <div class="section">
                    <h3>Step 2: Live Inference Telemetry</h3>
                    <div style="margin-bottom: 10px;">
                        Connection: <span id="wsStatus" class="status-badge">Disconnected</span>
                    </div>
                    <div id="messages">Waiting for video upload to initiate stream...</div>
                </div>
            </div>

            <script>
                let ws;

                document.getElementById('uploadForm').addEventListener('submit', async (e) => {
                    e.preventDefault();
                    const fileInput = document.getElementById('videoFile');
                    const uploadBtn = document.getElementById('uploadBtn');
                    const uploadStatus = document.getElementById('uploadStatus');

                    if (!fileInput.files[0]) return;

                    const formData = new FormData();
                    formData.append('file', fileInput.files[0]);

                    try {
                        uploadBtn.disabled = true;
                        uploadStatus.innerText = "Uploading file and initializing feed...";
                        
                        // Hit your POST endpoint
                        const response = await fetch('/upload-video', {
                            method: 'POST',
                            body: formData
                        });
                        
                        const result = await response.json();
                        
                        if (result.message === 'Video uploaded successfully' || result.filename) {
                            uploadStatus.innerHTML = `✅ Uploaded successfully as <strong>${result.filename}</strong>`;
                            // Trigger the WebSocket streaming handshake automatically using the returned parameter
                            connectToStream(result.filename);
                        } else {
                            uploadStatus.innerText = "❌ Upload failed unexpected schema.";
                            uploadBtn.disabled = false;
                        }
                    } catch (error) {
                        console.error(error);
                        uploadStatus.innerText = "❌ Network error during upload.";
                        uploadBtn.disabled = false;
                    }
                });

                function connectToStream(videoName) {
                    const wsStatus = document.getElementById('wsStatus');
                    const messagesDiv = document.getElementById('messages');
                    
                    // Clear previous log lines
                    messagesDiv.innerText = '';

                    // Construct target URL string dynamically
                    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                    const wsUrl = `${protocol}//${window.location.host}/ws/stream?video_name=${encodeURIComponent(videoName)}`;
                    ws = new WebSocket(wsUrl);

                    ws.onopen = () => {
                        wsStatus.innerText = "Connected";
                        wsStatus.style.background = "#28a745";
                    };

                    ws.onmessage = (event) => {
                        const messageLine = document.createElement('div');
                        messageLine.style.marginBottom = "6px";
                        messageLine.innerText = `[${new Date().toLocaleTimeString()}] ${event.data}`;
                        messagesDiv.appendChild(messageLine);
                        messagesDiv.scrollTop = messagesDiv.scrollHeight; // Keep view pinned to bottom
                    };

                    ws.onclose = () => {
                        wsStatus.innerText = "Disconnected";
                        wsStatus.style.background = "#dc3545";
                        document.getElementById('uploadBtn').disabled = false;
                    };
                }
            </script>
        </body>
    </html>
    """
