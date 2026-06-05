from typing import Annotated

from fastapi import Depends
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, delete, select

from app.db.session import get_session
from app.models.camera import Camera
from app.models.zone import Zone
from app.repositories import RepositoryError


class ZoneRepository:
    def __init__(
        self,
        session: Annotated[Session, Depends(get_session)],
    ) -> None:
        self.session = session

    def create(self, zone: Zone) -> Zone:
        self.session.add(zone)
        return self._commit_and_refresh(zone, "create zone")

    def get_by_id(self, zone_id: int) -> Zone | None:
        try:
            return self.session.get(Zone, zone_id)
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not read zone.") from exc

    def get_by_camera(self, camera_id: int) -> list[Zone]:
        try:
            statement = (
                select(Zone)
                .where(Zone.camera_id == camera_id)
                .order_by(Zone.id)
            )
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list zones for camera.") from exc

    def get_by_source_key(self, source_key: str) -> list[Zone]:
        try:
            statement = (
                select(Zone)
                .join(Camera, Zone.camera_id == Camera.id)
                .where(Camera.source_key == source_key)
                .order_by(Zone.id)
            )
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list zones for source key.") from exc

    def list_all(self) -> list[Zone]:
        try:
            statement = select(Zone).order_by(Zone.id)
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list zones.") from exc

    def update(self, zone: Zone) -> Zone:
        try:
            persisted = self.session.merge(zone)
            self.session.commit()
            self.session.refresh(persisted)
            return persisted
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not update zone.") from exc

    def delete(self, zone_id: int) -> bool:
        zone = self.get_by_id(zone_id)
        if zone is None:
            return False

        try:
            self.session.delete(zone)
            self.session.commit()
            return True
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not delete zone.") from exc

    def delete_by_camera(self, camera_id: int) -> int:
        try:
            result = self.session.exec(
                delete(Zone).where(Zone.camera_id == camera_id)
            )
            self.session.commit()
            return result.rowcount or 0
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not delete zones for camera.") from exc

    def _commit_and_refresh(self, zone: Zone, operation: str) -> Zone:
        try:
            self.session.commit()
            self.session.refresh(zone)
            return zone
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError(f"Could not {operation}.") from exc
