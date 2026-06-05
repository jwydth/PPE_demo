import io
import logging
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from PIL import Image

from app.schemas.detection import DetectionResponse, VideoProcessingResponse
from app.schemas.violation import ViolationReport
from app.services.ppe_detector import PPEDetector
from app.services.ppe_violation_service import (
    PPEViolationService,
    get_ppe_violation_service,
)
from app.services.zone_violation_service import (
    ZoneViolationService,
    get_zone_violation_service,
)

router = APIRouter(tags=["detection"])
logger = logging.getLogger(__name__)

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
        raise HTTPException(
            status_code=400, detail="Could not decode the uploaded image."
        )

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
        _cleanup_temp_video(tmp_path)


def _cleanup_temp_video(tmp_path: Path | None) -> None:
    if tmp_path is None:
        return
    try:
        tmp_path.unlink(missing_ok=True)
    except PermissionError:
        logger.warning(
            "Could not delete temporary video file because it is still in use: %s",
            tmp_path,
        )


@router.get("/violations", response_model=list[ViolationReport])
async def violations(
    service: Annotated[
        PPEViolationService,
        Depends(get_ppe_violation_service),
    ],
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ViolationReport]:
    return service.get_recent_violations(limit=limit)

@router.delete("/violations")
async def delete_all_incidents(
    service: Annotated[
        PPEViolationService,
        Depends(get_ppe_violation_service),
    ],
    zone_service: Annotated[
        ZoneViolationService,
        Depends(get_zone_violation_service),
    ],
) -> dict[str, int]:
    """Delete all PPE and zone violations from the database."""
    ppe_count = service.delete_all_violations()
    zone_count = zone_service.delete_all_zone_violations()
    return {
        "ppe_violations_deleted": ppe_count,
        "zone_violations_deleted": zone_count,
        "total_deleted": ppe_count + zone_count,
    }


@router.delete("/violations/{violation_id}")
async def delete_single_violation(
    violation_id: int,
    service: Annotated[
        PPEViolationService,
        Depends(get_ppe_violation_service),
    ],
) -> dict[str, bool]:
    """Delete a single PPE violation by ID."""
    success = service.delete_violation(violation_id)
    if not success:
        raise HTTPException(status_code=404, detail="Violation not found")
    return {"success": success}


@router.delete("/zone-violations/{zone_violation_id}")
async def delete_single_zone_violation(
    zone_violation_id: int,
    service: Annotated[
        ZoneViolationService,
        Depends(get_zone_violation_service),
    ],
) -> dict[str, bool]:
    """Delete a single zone violation by ID."""
    success = service.delete_zone_violation(zone_violation_id)
    if not success:
        raise HTTPException(status_code=404, detail="Zone violation not found")
    return {"success": success}
