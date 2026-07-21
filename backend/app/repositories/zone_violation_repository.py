from datetime import datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.db.session import get_session
from app.models.zone_violation import ZoneViolation
from app.repositories import RepositoryError


class ZoneViolationRepository:
    def __init__(
        self,
        session: Annotated[Session, Depends(get_session)],
    ) -> None:
        self.session = session

    def create(self, violation: ZoneViolation) -> ZoneViolation:
        self.session.add(violation)
        return self._commit_and_refresh(violation)

    def get_by_id(self, violation_id: int) -> ZoneViolation | None:
        try:
            return self.session.get(ZoneViolation, violation_id)
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not read zone violation.") from exc

    def list_all(self) -> list[ZoneViolation]:
        try:
            statement = select(ZoneViolation).order_by(
                ZoneViolation.occurred_at.desc(),
                ZoneViolation.id.desc(),
            )
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list zone violations.") from exc

    def get_recent(self, limit: int) -> list[ZoneViolation]:
        try:
            statement = (
                select(ZoneViolation)
                .order_by(
                    ZoneViolation.occurred_at.desc(),
                    ZoneViolation.id.desc(),
                )
                .limit(limit)
            )
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list recent zone violations.") from exc

    def list_between(
        self,
        *,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int,
    ) -> list[ZoneViolation]:
        try:
            statement = select(ZoneViolation)
            if date_from is not None:
                statement = statement.where(ZoneViolation.occurred_at >= date_from)
            if date_to is not None:
                statement = statement.where(ZoneViolation.occurred_at <= date_to)
            statement = statement.order_by(
                ZoneViolation.occurred_at.desc(),
                ZoneViolation.id.desc(),
            ).limit(limit)
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list zone violations by date range.") from exc

    def delete(self, violation_id: int) -> bool:
        violation = self.get_by_id(violation_id)
        if violation is None:
            return False
        try:
            self.session.delete(violation)
            self.session.commit()
            return True
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not delete zone violation.") from exc

    def delete_all(self) -> int:
        try:
            result = self.session.exec(delete(ZoneViolation))
            self.session.commit()
            return result.rowcount or 0
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not delete zone violations.") from exc

    def _commit_and_refresh(self, violation: ZoneViolation) -> ZoneViolation:
        try:
            self.session.commit()
            self.session.refresh(violation)
            return violation
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not create zone violation.") from exc
