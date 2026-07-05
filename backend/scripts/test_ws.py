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
            msg = await ws.recv()
            data = json.loads(msg)
            print("msg 2 keys:", data.keys())
            print("image_base64 present?", "image_base64" in data)
            if "image_base64" in data:
                print("image_base64 type:", type(data["image_base64"]))
                if isinstance(data["image_base64"], str):
                    print("image_base64 length:", len(data["image_base64"]))
    except Exception as e:
        print("Error:", e)

asyncio.run(test())
