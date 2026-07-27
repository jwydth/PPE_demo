from typing import Annotated

from fastapi import Depends
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.db.session import get_session
from app.models.camera import Camera
from app.repositories import RepositoryError


class CameraRepository:
    def __init__(
        self,
        session: Annotated[Session, Depends(get_session)],
    ) -> None:
        self.session = session

    def create(self, camera: Camera) -> Camera:
        self.session.add(camera)
        return self._commit_and_refresh(camera, "create camera")

    def get_by_id(self, camera_id: int) -> Camera | None:
        try:
            return self.session.get(Camera, camera_id)
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not read camera.") from exc

    def get_by_source_key(self, source_key: str) -> Camera | None:
        try:
            statement = select(Camera).where(Camera.source_key == source_key)
            return self.session.exec(statement).first()
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not read camera.") from exc

    def list_all(self) -> list[Camera]:
        try:
            statement = select(Camera).order_by(Camera.id)
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list cameras.") from exc

    def update(self, camera: Camera) -> Camera:
        try:
            persisted = self.session.merge(camera)
            self.session.commit()
            self.session.refresh(persisted)
            return persisted
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not update camera.") from exc

    def deactivate(self, camera_id: int) -> Camera | None:
        camera = self.get_by_id(camera_id)
        if camera is None:
            return None

        camera.is_active = False
        return self._commit_and_refresh(camera, "deactivate camera")

    def set_home_zone(self, camera_id: int, zone_id: int | None) -> Camera | None:
        camera = self.get_by_id(camera_id)
        if camera is None:
            return None

        camera.home_zone_id = zone_id
        return self._commit_and_refresh(camera, "set camera home zone")

    def delete(self, camera_id: int) -> bool:
        camera = self.get_by_id(camera_id)
        if camera is None:
            return False
        try:
            self.session.delete(camera)
            self.session.commit()
            return True
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not delete camera.") from exc

    def _commit_and_refresh(self, camera: Camera, operation: str) -> Camera:
        try:
            self.session.commit()
            self.session.refresh(camera)
            return camera
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError(f"Could not {operation}.") from exc
