from typing import Annotated
from fastapi import Depends
from app.models.camera_feature_config import CameraFeatureConfig
from app.models.feature import Feature
from app.repositories.camera_repository import CameraRepository
from app.repositories.feature_repository import FeatureRepository
from app.repositories.camera_feature_config_repository import CameraFeatureConfigRepository
from app.services import ServiceNotFoundError, ServiceValidationError
from sqlmodel import Session
from app.db.session import get_session


class FeatureService:
    def __init__(
        self,
        feature_repository: Annotated[FeatureRepository, Depends(FeatureRepository)],
        camera_feature_config_repository: Annotated[
            CameraFeatureConfigRepository, Depends(CameraFeatureConfigRepository)
        ],
        camera_repository: Annotated[CameraRepository, Depends(CameraRepository)],
    ) -> None:
        self.feature_repository = feature_repository
        self.camera_feature_config_repository = camera_feature_config_repository
        self.camera_repository = camera_repository

    def list_features(self) -> list[Feature]:
        return self.feature_repository.list_all()

    def ensure_camera_feature_configs(self, camera_id: int) -> list[CameraFeatureConfig]:
        camera = self.camera_repository.get_by_id(camera_id)
        if camera is None:
            raise ServiceNotFoundError(f"Camera {camera_id} was not found.")

        all_features = self.feature_repository.list_all()
        configs = []

        for feature in all_features:
            if not feature.is_active or feature.id is None:
                continue
            
            config = self.camera_feature_config_repository.get_by_camera_and_feature(
                camera_id, feature.id
            )
            if config is None:
                # Determine default is_enabled state based on the feature key
                default_enabled = False
                if feature.key == "ppe_detection":
                    default_enabled = True
                
                config = self.camera_feature_config_repository.create(
                    CameraFeatureConfig(
                        camera_id=camera_id,
                        feature_id=feature.id,
                        is_enabled=default_enabled,
                        config_params=None,
                    )
                )
            
            # Populate transient/reference field to get feature key and name
            config.feature = feature
            configs.append(config)

        return configs

    def update_camera_feature_config(
        self, camera_id: int, feature_key: str, is_enabled: bool, config_params: dict | None = None
    ) -> CameraFeatureConfig:
        camera = self.camera_repository.get_by_id(camera_id)
        if camera is None:
            raise ServiceNotFoundError(f"Camera {camera_id} was not found.")

        feature = self.feature_repository.get_by_key(feature_key)
        # Existing databases may still have the pre-migration key. Keep their
        # camera configuration usable while init_db renames it in-place.
        if feature is None and feature_key == "behavior_detection":
            feature = self.feature_repository.get_by_key("fall_detection")
        if feature is None or feature.id is None:
            raise ServiceNotFoundError(f"Feature '{feature_key}' was not found.")

        config = self.camera_feature_config_repository.get_by_camera_and_feature(
            camera_id, feature.id
        )
        if config is None:
            config = CameraFeatureConfig(
                camera_id=camera_id,
                feature_id=feature.id,
                is_enabled=is_enabled,
                config_params=config_params,
            )
            config = self.camera_feature_config_repository.create(config)
        else:
            config.is_enabled = is_enabled
            if config_params is not None:
                config.config_params = config_params
            config = self.camera_feature_config_repository.update(config)

        config.feature = feature
        return config


def get_feature_service(
    session: Annotated[Session, Depends(get_session)],
) -> FeatureService:
    return FeatureService(
        FeatureRepository(session),
        CameraFeatureConfigRepository(session),
        CameraRepository(session),
    )
