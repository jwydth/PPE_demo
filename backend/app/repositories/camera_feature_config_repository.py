from typing import Annotated
from fastapi import Depends
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select
from app.db.session import get_session
from app.models.camera_feature_config import CameraFeatureConfig
from app.repositories import RepositoryError


class CameraFeatureConfigRepository:
    def __init__(
        self,
        session: Annotated[Session, Depends(get_session)],
    ) -> None:
        self.session = session

    def create(self, config: CameraFeatureConfig) -> CameraFeatureConfig:
        try:
            self.session.add(config)
            self.session.commit()
            self.session.refresh(config)
            return config
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not create camera feature config.") from exc

    def get_by_id(self, config_id: int) -> CameraFeatureConfig | None:
        try:
            return self.session.get(CameraFeatureConfig, config_id)
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not read camera feature config by ID.") from exc

    def get_by_camera_and_feature(self, camera_id: int, feature_id: int) -> CameraFeatureConfig | None:
        try:
            statement = select(CameraFeatureConfig).where(
                CameraFeatureConfig.camera_id == camera_id,
                CameraFeatureConfig.feature_id == feature_id,
            )
            return self.session.exec(statement).first()
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not read camera feature config by camera and feature.") from exc

    def get_by_camera(self, camera_id: int) -> list[CameraFeatureConfig]:
        try:
            statement = select(CameraFeatureConfig).where(
                CameraFeatureConfig.camera_id == camera_id
            )
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list camera feature configs for camera.") from exc

    def update(self, config: CameraFeatureConfig) -> CameraFeatureConfig:
        try:
            persisted = self.session.merge(config)
            self.session.commit()
            self.session.refresh(persisted)
            return persisted
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not update camera feature config.") from exc
