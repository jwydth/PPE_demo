import logging
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from app.models.behavior_incident import BehaviorIncidentStatus, BehaviorType
from app.schemas.fall_detection import (
    BehaviorIncidentRead,
    FallImagePredictionResponse,
    FallVideoPredictionResponse,
)
from app.services import ServiceNotFoundError, ServiceValidationError
from app.services.behavior_incident_service import (
    BehaviorIncidentService,
    get_behavior_incident_service,
)
from app.services.fall_detector import FallDetector, FallModelUnavailable

router = APIRouter(tags=["fall-detection"])
logger = logging.getLogger(__name__)

_fall_detector = FallDetector()

_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/bmp"}
_ALLOWED_VIDEO_TYPES = {
    "video/mp4",
    "video/mpeg",
    "video/quicktime",
    "video/x-msvideo",
    "video/x-matroska",
    "video/webm",
}


@router.post("/fall-detection/predict", response_model=FallImagePredictionResponse)
async def predict_fall_image(file: UploadFile = File(...)) -> FallImagePredictionResponse:
    if file.content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported content type '{file.content_type}'. "
                "Accepted: image/jpeg, image/png, image/webp, image/bmp."
            ),
        )

    suffix = Path(file.filename or "upload.jpg").suffix or ".jpg"
    tmp_path: Path | None = None
    try:
        tmp_path = await _write_upload_to_temp(file, suffix)
        return _fall_detector.predict_image(
            tmp_path,
            source_name=file.filename or tmp_path.name,
        )
    except FallModelUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _cleanup_temp_file(tmp_path)


@router.post("/fall-detection/predict-video", response_model=FallVideoPredictionResponse)
async def predict_fall_video(file: UploadFile = File(...)) -> FallVideoPredictionResponse:
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
        tmp_path = await _write_upload_to_temp(file, suffix)
        return _fall_detector.predict_video(
            tmp_path,
            source_name=file.filename or tmp_path.name,
        )
    except FallModelUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _cleanup_temp_file(tmp_path)


@router.get("/behavior-incidents", response_model=list[BehaviorIncidentRead])
async def behavior_incidents(
    service: Annotated[
        BehaviorIncidentService,
        Depends(get_behavior_incident_service),
    ],
    limit: int = Query(default=100, ge=1, le=500),
    behavior_type: BehaviorType | None = Query(default=None),
    status_filter: BehaviorIncidentStatus | None = Query(default=None, alias="status"),
    camera_id: int | None = Query(default=None, ge=1),
) -> list[BehaviorIncidentRead]:
    return service.list_recent(
        limit=limit,
        behavior_type=behavior_type,
        status=status_filter,
        camera_id=camera_id,
    )


@router.get("/behavior-incidents/{incident_id}", response_model=BehaviorIncidentRead)
async def behavior_incident(
    incident_id: int,
    service: Annotated[
        BehaviorIncidentService,
        Depends(get_behavior_incident_service),
    ],
) -> BehaviorIncidentRead:
    try:
        return service.get_incident(incident_id)
    except ServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ServiceValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/behavior-incidents/{incident_id}")
async def delete_behavior_incident(
    incident_id: int,
    service: Annotated[
        BehaviorIncidentService,
        Depends(get_behavior_incident_service),
    ],
) -> dict[str, bool]:
    """Delete a single behavior incident by ID (hard delete; permanent)."""
    success = service.delete_incident(incident_id)
    if not success:
        raise HTTPException(status_code=404, detail="Behavior incident not found")
    return {"success": success}


async def _write_upload_to_temp(file: UploadFile, suffix: str) -> Path:
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = Path(tmp.name)
        while chunk := await file.read(1024 * 1024):
            tmp.write(chunk)
    return tmp_path


def _cleanup_temp_file(tmp_path: Path | None) -> None:
    if tmp_path is None:
        return
    try:
        tmp_path.unlink(missing_ok=True)
    except PermissionError:
        logger.warning("Could not delete temporary fall-detection upload: %s", tmp_path)
