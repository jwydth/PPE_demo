from typing import Annotated

from fastapi import Depends
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, delete, select

from app.db.session import get_session
from app.models.camera import Camera
from app.models.camera_zone_view import CameraZoneView
from app.models.physical_zone import PhysicalZone
from app.repositories import RepositoryError


class CameraZoneViewRepository:
    def __init__(
        self,
        session: Annotated[Session, Depends(get_session)],
    ) -> None:
        self.session = session

    def create(self, view: CameraZoneView) -> CameraZoneView:
        self.session.add(view)
        return self._commit_and_refresh(view, "create camera zone view")

    def get_by_id(self, view_id: int) -> CameraZoneView | None:
        try:
            return self.session.get(CameraZoneView, view_id)
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not read camera zone view.") from exc

    def get_by_camera(self, camera_id: int) -> list[CameraZoneView]:
        try:
            statement = (
                select(CameraZoneView)
                .where(CameraZoneView.camera_id == camera_id)
                .order_by(CameraZoneView.id)
            )
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list zone views for camera.") from exc

    def get_by_physical_zone(self, physical_zone_id: int) -> list[CameraZoneView]:
        try:
            statement = (
                select(CameraZoneView)
                .where(CameraZoneView.physical_zone_id == physical_zone_id)
                .order_by(CameraZoneView.id)
            )
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError(
                "Could not list camera views for physical zone."
            ) from exc

    def get_by_camera_and_physical_zone(
        self,
        camera_id: int,
        physical_zone_id: int,
    ) -> CameraZoneView | None:
        try:
            statement = select(CameraZoneView).where(
                CameraZoneView.camera_id == camera_id,
                CameraZoneView.physical_zone_id == physical_zone_id,
            )
            return self.session.exec(statement).first()
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not read camera zone view.") from exc

    def get_active_by_camera_source_key(self, source_key: str) -> list[CameraZoneView]:
        try:
            statement = (
                select(CameraZoneView)
                .join(Camera, CameraZoneView.camera_id == Camera.id)
                .join(
                    PhysicalZone,
                    CameraZoneView.physical_zone_id == PhysicalZone.id,
                )
                .where(
                    Camera.source_key == source_key,
                    Camera.is_active.is_(True),
                    CameraZoneView.is_active.is_(True),
                    PhysicalZone.is_active.is_(True),
                )
                .order_by(CameraZoneView.id)
            )
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list active zone views.") from exc

    def update(self, view: CameraZoneView) -> CameraZoneView:
        try:
            persisted = self.session.merge(view)
            self.session.commit()
            self.session.refresh(persisted)
            return persisted
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not update camera zone view.") from exc

    def deactivate(self, view_id: int) -> CameraZoneView | None:
        view = self.get_by_id(view_id)
        if view is None:
            return None

        view.is_active = False
        return self._commit_and_refresh(view, "deactivate camera zone view")

    def deactivate_by_camera(self, camera_id: int) -> int:
        try:
            views = self.get_by_camera(camera_id)
            count = 0
            for view in views:
                if view.is_active:
                    view.is_active = False
                    count += 1
            self.session.commit()
            return count
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError(
                "Could not deactivate zone views for camera."
            ) from exc

    def deactivate_by_camera_source_key(self, source_key: str) -> int:
        try:
            statement = (
                select(CameraZoneView)
                .join(Camera, CameraZoneView.camera_id == Camera.id)
                .where(
                    Camera.source_key == source_key,
                    CameraZoneView.is_active.is_(True),
                )
            )
            views = list(self.session.exec(statement).all())
            for view in views:
                view.is_active = False
            self.session.commit()
            return len(views)
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError(
                "Could not deactivate zone views for camera source key."
            ) from exc

    def delete(self, view_id: int) -> bool:
        view = self.get_by_id(view_id)
        if view is None:
            return False

        try:
            self.session.delete(view)
            self.session.commit()
            return True
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not delete camera zone view.") from exc

    def delete_by_camera(self, camera_id: int) -> int:
        try:
            result = self.session.exec(
                delete(CameraZoneView).where(CameraZoneView.camera_id == camera_id)
            )
            self.session.commit()
            return result.rowcount or 0
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not delete zone views for camera.") from exc

    def delete_by_camera_source_key(self, source_key: str) -> int:
        try:
            camera = self.session.exec(
                select(Camera).where(Camera.source_key == source_key)
            ).first()
            if camera is None or camera.id is None:
                return 0
            return self.delete_by_camera(camera.id)
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError(
                "Could not delete zone views for camera source key."
            ) from exc

    def _commit_and_refresh(
        self,
        view: CameraZoneView,
        operation: str,
    ) -> CameraZoneView:
        try:
            self.session.commit()
            self.session.refresh(view)
            return view
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError(f"Could not {operation}.") from exc
