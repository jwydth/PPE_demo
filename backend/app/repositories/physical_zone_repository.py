from typing import Annotated

from fastapi import Depends
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.db.session import get_session
from app.models.physical_zone import PhysicalZone
from app.repositories import RepositoryError


class PhysicalZoneRepository:
    def __init__(
        self,
        session: Annotated[Session, Depends(get_session)],
    ) -> None:
        self.session = session

    def create(self, zone: PhysicalZone) -> PhysicalZone:
        self.session.add(zone)
        return self._commit_and_refresh(zone, "create physical zone")

    def get_by_id(self, zone_id: int) -> PhysicalZone | None:
        try:
            return self.session.get(PhysicalZone, zone_id)
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not read physical zone.") from exc

    def get_by_factory(self, factory_id: int) -> list[PhysicalZone]:
        try:
            statement = (
                select(PhysicalZone)
                .where(PhysicalZone.factory_id == factory_id)
                .order_by(PhysicalZone.id)
            )
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list physical zones for factory.") from exc

    def get_by_factory_and_name(
        self,
        factory_id: int,
        name: str,
    ) -> PhysicalZone | None:
        try:
            statement = (
                select(PhysicalZone)
                .where(
                    PhysicalZone.factory_id == factory_id,
                    PhysicalZone.name == name,
                )
                .order_by(PhysicalZone.id)
            )
            return self.session.exec(statement).first()
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not read physical zone by name.") from exc

    def update(self, zone: PhysicalZone) -> PhysicalZone:
        try:
            persisted = self.session.merge(zone)
            self.session.commit()
            self.session.refresh(persisted)
            return persisted
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not update physical zone.") from exc

    def deactivate(self, zone_id: int) -> PhysicalZone | None:
        zone = self.get_by_id(zone_id)
        if zone is None:
            return None

        zone.is_active = False
        return self._commit_and_refresh(zone, "deactivate physical zone")

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
            raise RepositoryError("Could not delete physical zone.") from exc

    def _commit_and_refresh(
        self,
        zone: PhysicalZone,
        operation: str,
    ) -> PhysicalZone:
        try:
            self.session.commit()
            self.session.refresh(zone)
            return zone
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError(f"Could not {operation}.") from exc
