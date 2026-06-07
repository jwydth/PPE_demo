import io
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from PIL import Image

from app.models.schemas import DetectionResponse, VideoProcessingResponse, ViolationReport
from app.services.ppe_detector import PPEDetector
from app.services.violation_store import list_violations, delete_all_violations, delete_all_zone_violations, delete_violation, delete_zone_violation

router = APIRouter(tags=["detection"])

_detector = PPEDetector()

_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/bmp"}
_ALLOWED_VIDEO_TYPES = {
    "video/mp4",
    "video/mpeg",
    "video/quicktime",
    "video/x-msvideo",
    "video/x-matroska",
    "video/webm",
}


@router.post("/predict", response_model=DetectionResponse)
async def predict(file: UploadFile = File(...)) -> DetectionResponse:
    if file.content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported content type '{file.content_type}'. "
                "Accepted: image/jpeg, image/png, image/webp, image/bmp."
            ),
        )

    data = await file.read()

    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="Could not decode the uploaded image.")

    return _detector.predict(image)


@router.post("/predict-video", response_model=VideoProcessingResponse)
async def predict_video(file: UploadFile = File(...)) -> VideoProcessingResponse:
    if file.content_type not in _ALLOWED_VIDEO_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported content type '{file.content_type}'. "
                "Accepted: mp4, mpeg, mov, avi, mkv, webm."
            ),
        )

    suffix = Path(file.filename or "upload.mp4").suffix or ".mp4"
    tmp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = Path(tmp.name)
            while chunk := await file.read(1024 * 1024):
                tmp.write(chunk)

        return _detector.process_video(tmp_path, file.filename or tmp_path.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink(missing_ok=True)
            except PermissionError:
                # On Windows, file might still be locked by inference engine if an error occurred.
                pass


@router.get("/violations", response_model=list[ViolationReport])
async def violations(limit: int = Query(default=100, ge=1, le=500)) -> list[ViolationReport]:
    return list_violations(limit=limit)


@router.delete("/violations")
async def delete_all_incidents() -> dict[str, int]:
    """Delete all PPE and zone violations from the database."""
    ppe_count = delete_all_violations()
    zone_count = delete_all_zone_violations()
    return {"ppe_violations_deleted": ppe_count, "zone_violations_deleted": zone_count, "total_deleted": ppe_count + zone_count}


@router.delete("/violations/{violation_id}")
async def delete_single_violation(violation_id: int) -> dict[str, bool]:
    """Delete a single PPE violation by ID."""
    success = delete_violation(violation_id)
    if not success:
        raise HTTPException(status_code=404, detail="Violation not found")
    return {"success": success}


@router.delete("/zone-violations/{zone_violation_id}")
async def delete_single_zone_violation(zone_violation_id: int) -> dict[str, bool]:
    """Delete a single zone violation by ID."""
    success = delete_zone_violation(zone_violation_id)
    if not success:
        raise HTTPException(status_code=404, detail="Zone violation not found")
    return {"success": success}
