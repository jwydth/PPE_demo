import io
import json
import logging
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.db.session import get_session
from app.services.ppe_detector import PPEDetector
from app.services.spatial import (
    compute_homography_matrix,
    is_point_in_polygon,
    transform_points,
)
from app.storage import StorageError
from app.storage.evidence_storage import EvidenceStorage, get_evidence_storage

router = APIRouter(tags=["testing"])
_detector = PPEDetector()
logger = logging.getLogger(__name__)


@router.get("/health/db")
def database_health(
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, str]:
    try:
        session.execute(text("SELECT 1")).scalar_one()
    except SQLAlchemyError as exc:
        logger.exception("PostgreSQL health check failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL connection failed.",
        ) from exc

    return {"database": "connected"}


def _get_health_storage() -> EvidenceStorage:
    try:
        return get_evidence_storage()
    except StorageError as exc:
        logger.exception("MinIO storage configuration failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.get("/health/storage")
def storage_health(
    storage: Annotated[EvidenceStorage, Depends(_get_health_storage)],
) -> dict[str, str]:
    try:
        bucket_name = storage.ensure_ready()
    except StorageError as exc:
        logger.exception("MinIO health check failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return {
        "storage": "connected",
        "bucket": bucket_name,
    }


@router.post("/test-zone-image")
async def test_zone_image(
    file: UploadFile = File(...),
    calibration: str = Form(...),  # JSON string of normalized points
    zones: str = Form(...),  # JSON string of list of zones
):
    # 1. Load Image
    data = await file.read()
    img_pil = Image.open(io.BytesIO(data)).convert("RGB")
    width, height = img_pil.size

    # 2. Run Detection
    prediction = _detector.predict(img_pil)

    # 3. Parse Inputs
    calib_pts = json.loads(calibration)
    zones_data = json.loads(zones)

    bev_matrix = None
    if len(calib_pts) == 4:
        bev_matrix = compute_homography_matrix(calib_pts)

    report = []
    report.append("=== ZONE MONITORING TEST REPORT ===")
    report.append(f"Source Image: {file.filename} ({width}x{height})")
    report.append("-" * 40)

    # Calibration Points
    report.append("\n[CALIBRATION POINTS]")
    for i, p in enumerate(calib_pts):
        abs_x = p["x"] * width
        abs_y = p["y"] * height
        report.append(f"Point {i + 1}: Camera({abs_x:.1f}, {abs_y:.1f})")

    # Zones / Polygons
    report.append("\n[POLYGONS / ZONES]")
    for i, zone in enumerate(zones_data):
        name = zone.get("zone_name", "Unnamed")
        report.append(f"Zone {i + 1} ({name}):")
        coords = json.loads(zone["flattened_coordinates"])
        abs_coords = [
            (round(p["x"] * width, 1), round(p["y"] * height, 1)) for p in coords
        ]
        report.append(f"  Camera Coordinates: {abs_coords}")

        if bev_matrix is not None:
            bev_poly = transform_points(bev_matrix, [(p["x"], p["y"]) for p in coords])
            report.append(
                f"  BEV Coordinates: {[(round(p[0], 1), round(p[1], 1)) for p in bev_poly]}"
            )

    # Human Detections
    report.append("\n[HUMAN DETECTIONS]")
    for i, person in enumerate(prediction.persons):
        # Bounding Box
        bbox = person.bbox
        report.append(f"Person {i + 1} (Conf: {person.confidence:.2f}):")
        report.append(
            f"  Bounding Box: [x1={bbox.x1:.1f}, y1={bbox.y1:.1f}, x2={bbox.x2:.1f}, y2={bbox.y2:.1f}]"
        )

        # Foot Point (center-bottom)
        foot_norm_x = (bbox.x1 + bbox.x2) / 2 / width
        foot_norm_y = bbox.y2 / height
        abs_foot_x = foot_norm_x * width
        abs_foot_y = foot_norm_y * height
        report.append(f"  Foot Point: Camera({abs_foot_x:.1f}, {abs_foot_y:.1f})")

        if bev_matrix is not None:
            bev_foot = transform_points(bev_matrix, [(foot_norm_x, foot_norm_y)])[0]
            report.append(f"  Foot Point BEV: ({bev_foot[0]:.1f}, {bev_foot[1]:.1f})")

            # Check incursion for report
            for zone in zones_data:
                zone_coords = json.loads(zone["flattened_coordinates"])
                bev_poly = transform_points(
                    bev_matrix, [(p["x"], p["y"]) for p in zone_coords]
                )
                if is_point_in_polygon(bev_foot, bev_poly):
                    report.append(
                        f"  STATUS: Breach Detected in {zone.get('zone_name', 'Unnamed')}"
                    )
        else:
            report.append("  STATUS: BEV Calibration missing - Spatial check skipped")

    # Write to output.txt
    output_str = "\n".join(report)
    output_path = Path("output.txt")
    output_path.write_text(output_str)

    return {
        "status": "success",
        "file": str(output_path.absolute()),
        "humans_count": len(prediction.persons),
    }
