import io
import logging
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from PIL import Image

from app.schemas.detection import DetectionResponse, VideoProcessingResponse
from app.schemas.violation import ViolationDetail, ViolationReport
from app.services import ServiceNotFoundError
from app.services.ppe_detector import PPEDetector
from app.services.ppe_violation_service import (
    PPEViolationService,
    get_ppe_violation_service,
)
from app.services.behavior_incident_service import (
    BehaviorIncidentService,
    get_behavior_incident_service,
)
from app.services.zone_violation_service import (
    ZoneViolationService,
    get_zone_violation_service,
)
from app.storage.local_paths import UPLOAD_DIR, ensure_upload_dir

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
async def predict_video(
    file: UploadFile = File(...),
    enable_ppe: bool = Form(True),
    enable_zone: bool = Form(True),
) -> VideoProcessingResponse:
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

        return _detector.process_video(
            tmp_path,
            file.filename or tmp_path.name,
            enable_ppe=enable_ppe,
            enable_zone=enable_zone,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        _cleanup_temp_video(tmp_path)


@router.post("/upload-video")
async def upload_video(file: UploadFile = File(...)) -> dict:
    if file.content_type not in _ALLOWED_VIDEO_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported content type '{file.content_type}'. "
                "Accepted: mp4, mpeg, mov, avi, mkv, webm."
            ),
        )

    ensure_upload_dir()
    filename = file.filename or "upload.mp4"
    file_path = UPLOAD_DIR / filename

    try:
        with open(file_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save video: {e}")

    return {"filename": filename, "message": "Video uploaded successfully"}


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

@router.get("/violations/{violation_id}", response_model=ViolationDetail)
async def get_violation_detail(
    violation_id: int,
    service: Annotated[
        PPEViolationService,
        Depends(get_ppe_violation_service),
    ],
) -> ViolationDetail:
    """PPE violation detail, including per-subject bbox/missing_equipment/confidence."""
    try:
        return service.get_violation_detail(violation_id)
    except ServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
    behavior_service: Annotated[
        BehaviorIncidentService,
        Depends(get_behavior_incident_service),
    ],
) -> dict[str, int]:
    """Delete all visible PPE, zone, and behavior incidents."""
    ppe_count = service.delete_all_violations()
    zone_count = zone_service.delete_all_zone_violations()
    behavior_count = behavior_service.delete_all_behavior_incidents()
    return {
        "ppe_violations_deleted": ppe_count,
        "zone_violations_deleted": zone_count,
        "behavior_incidents_deleted": behavior_count,
        "total_deleted": ppe_count + zone_count + behavior_count,
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
