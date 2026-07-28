from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException

from app.schemas.feature import FeatureRead, CameraFeatureConfigRead, CameraFeatureConfigUpdate
from app.services import ServiceNotFoundError, ServiceValidationError
from app.services.feature_service import FeatureService, get_feature_service

router = APIRouter(tags=["features"])


@router.get("/features", response_model=list[FeatureRead])
async def list_features(
    service: Annotated[FeatureService, Depends(get_feature_service)],
) -> list[FeatureRead]:
    """List all registered system features."""
    features = service.list_features()
    return [
        FeatureRead(
            id=f.id,
            key=f.key,
            name=f.name,
            description=f.description,
            is_active=f.is_active,
        )
        for f in features
        if f.id is not None
    ]


@router.get("/cameras/{camera_id}/features", response_model=list[CameraFeatureConfigRead])
async def get_camera_feature_configs(
    camera_id: int,
    service: Annotated[FeatureService, Depends(get_feature_service)],
) -> list[CameraFeatureConfigRead]:
    """Get the configurations of all active features for a specific camera."""
    try:
        configs = service.ensure_camera_feature_configs(camera_id)
        return [
            CameraFeatureConfigRead(
                id=c.id,
                camera_id=c.camera_id,
                feature_key=c.feature.key,
                feature_name=c.feature.name,
                is_enabled=c.is_enabled,
                config_params=c.config_params,
            )
            for c in configs
            if c.id is not None
        ]
    except ServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/cameras/{camera_id}/features", response_model=list[CameraFeatureConfigRead])
async def update_camera_feature_configs(
    camera_id: int,
    updates: list[CameraFeatureConfigUpdate],
    service: Annotated[FeatureService, Depends(get_feature_service)],
) -> list[CameraFeatureConfigRead]:
    """Update configurations of one or more features for a camera."""
    try:
        for update in updates:
            service.update_camera_feature_config(
                camera_id=camera_id,
                feature_key=update.feature_key,
                is_enabled=update.is_enabled,
                config_params=update.config_params,
            )
        
        # Return the complete updated config list
        configs = service.ensure_camera_feature_configs(camera_id)
        return [
            CameraFeatureConfigRead(
                id=c.id,
                camera_id=c.camera_id,
                feature_key=c.feature.key,
                feature_name=c.feature.name,
                is_enabled=c.is_enabled,
                config_params=c.config_params,
            )
            for c in configs
            if c.id is not None
        ]
    except ServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ServiceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
