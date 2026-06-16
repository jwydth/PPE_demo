import logging
from pathlib import Path
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse
from app.services.ppe_detector import PPEDetector
from app.storage.local_paths import UPLOAD_DIR, ensure_upload_dir

router = APIRouter(tags=["streaming"])
logger = logging.getLogger(__name__)

_detector = PPEDetector()

@router.websocket("/ws/stream")
async def stream_video_ws(
    websocket: WebSocket,
    video_name: str = Query(...),
    enable_ppe: bool = Query(True),
    enable_zone: bool = Query(True),
):
    await websocket.accept()
    ensure_upload_dir()
    
    # Dynamic settings state
    settings_state = {
        "enable_ppe": enable_ppe,
        "enable_zone": enable_zone,
        "dismissed_signatures": [],
        "dismissed_ppe_signatures": [],
        "reload_zones": False,
    }
    
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

    logger.info(f"Starting {'live' if is_url else 'simulated'} stream for {video_name} (initial ppe={enable_ppe}, zone={enable_zone})")
    
    # Task to handle incoming setting updates
    async def listen_for_settings():
        logger.info(" [SIGNAL] Settings listener task started")
        try:
            while True:
                try:
                    data = await websocket.receive_json()
                    if data.get("event") == "update_settings":
                        new_settings = data.get("data", {})
                        if "enable_ppe" in new_settings:
                            settings_state["enable_ppe"] = bool(new_settings["enable_ppe"])
                        if "enable_zone" in new_settings:
                            settings_state["enable_zone"] = bool(new_settings["enable_zone"])
                        logger.info(f" [SIGNAL] Received dynamic settings update: {settings_state}")
                    elif data.get("event") == "dismiss_suggestion":
                        sig = data.get("data", {}).get("suggestion_id")
                        if sig:
                            settings_state["dismissed_signatures"].append(sig)
                            logger.info(f" [SIGNAL] Queued dismissal for suggestion: {sig}")
                    elif data.get("event") == "dismiss_ppe_suggestion":
                        sig = data.get("data", {}).get("suggestion_id")
                        if sig:
                            settings_state["dismissed_ppe_signatures"].append(sig)
                            logger.info(f" [SIGNAL] Queued PPE suggestion dismissal: {sig}")
                    elif data.get("event") == "reload_zones":
                        settings_state["reload_zones"] = True
                        logger.info(" [SIGNAL] Zone reload requested by client")
                except Exception as e:
                    # Could be parse error or websocket issues
                    logger.warning(f" [SIGNAL] Error receiving settings: {e}")
                    break
        except Exception as e:
            logger.error(f" [SIGNAL] Settings listener task failed: {e}")

    import asyncio
    settings_task = asyncio.create_task(listen_for_settings())

    try:
        async for event in _detector.stream_video(
            video_path,
            video_name,
            settings_state=settings_state, # Pass shared state
        ):
            await websocket.send_text(event.model_dump_json())
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"Error in streaming: {e}", exc_info=True)
        try:
            await websocket.send_json({"event": "error", "data": {"message": str(e)}})
        except:
            pass
    finally:
        settings_task.cancel()
        try:
            await websocket.close()
        except:
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