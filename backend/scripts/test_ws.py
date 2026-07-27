import asyncio
import websockets
import json

async def test():
    url = "ws://localhost:8000/ws/stream?video_name=rtsp://localhost:8554/mystream"
    try:
        async with websockets.connect(url) as ws:
            print("Connected")
            msg = await ws.recv()
            print("msg 1 keys:", json.loads(msg).keys())
            # The server now sends the JPEG as a separate binary WS message
            # immediately before the "frame" JSON envelope (has_image=True),
            # instead of embedding it as base64 inside the JSON.
            msg = await ws.recv()
            if isinstance(msg, (bytes, bytearray)):
                print("msg 2 is binary, length:", len(msg))
                msg = await ws.recv()
            data = json.loads(msg)
            print("msg keys:", data.keys())
            print("has_image:", data.get("has_image"))
    except Exception as e:
        print("Error:", e)

asyncio.run(test())
