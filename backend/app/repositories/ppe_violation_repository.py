from datetime import datetime
from typing import Annotated, TypeVar

from fastapi import Depends
from sqlalchemy import delete, func
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.db.session import get_session
from app.models.ppe_violation import PPEViolation, PPEViolationSubject
from app.repositories import RepositoryError

ModelT = TypeVar("ModelT", PPEViolation, PPEViolationSubject)


class PPEViolationRepository:
    def __init__(
        self,
        session: Annotated[Session, Depends(get_session)],
    ) -> None:
        self.session = session

    def create(self, violation: PPEViolation) -> PPEViolation:
        self.session.add(violation)
        return self._commit_and_refresh(violation, "create PPE violation")

    def get_by_id(self, violation_id: int) -> PPEViolation | None:
        try:
            return self.session.get(PPEViolation, violation_id)
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not read PPE violation.") from exc

    def list_all(self) -> list[PPEViolation]:
        try:
            statement = select(PPEViolation).order_by(
                PPEViolation.occurred_at.desc(),
                PPEViolation.id.desc(),
            )
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list PPE violations.") from exc

    def get_by_ids(self, ids: list[int]) -> list[PPEViolation]:
        """Fetch a specific set of rows in one query. Used by the paginated
        incident feed, which decides *which* rows a page holds from a merged
        cross-table index query and then hydrates only those."""
        if not ids:
            return []
        try:
            statement = select(PPEViolation).where(PPEViolation.id.in_(ids))  # type: ignore[union-attr]
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not read ppe violations.") from exc

    def count_all(self) -> int:
        try:
            return int(
                self.session.exec(select(func.count()).select_from(PPEViolation)).one()
            )
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not count ppe violations.") from exc

    def get_recent(self, limit: int) -> list[PPEViolation]:
        try:
            statement = (
                select(PPEViolation)
                .order_by(
                    PPEViolation.occurred_at.desc(),
                    PPEViolation.id.desc(),
                )
                .limit(limit)
            )
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list recent PPE violations.") from exc

    def list_between(
        self,
        *,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int,
    ) -> list[PPEViolation]:
        try:
            statement = select(PPEViolation)
            if date_from is not None:
                statement = statement.where(PPEViolation.occurred_at >= date_from)
            if date_to is not None:
                statement = statement.where(PPEViolation.occurred_at <= date_to)
            statement = statement.order_by(
                PPEViolation.occurred_at.desc(),
                PPEViolation.id.desc(),
            ).limit(limit)
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list PPE violations by date range.") from exc

    def create_subject(self, subject: PPEViolationSubject) -> PPEViolationSubject:
        self.session.add(subject)
        return self._commit_and_refresh(subject, "create PPE violation subject")

    def get_subjects(self, violation_id: int) -> list[PPEViolationSubject]:
        try:
            statement = (
                select(PPEViolationSubject)
                .where(PPEViolationSubject.ppe_violation_id == violation_id)
                .order_by(PPEViolationSubject.id)
            )
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list PPE violation subjects.") from exc

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
            raise RepositoryError("Could not delete PPE violation.") from exc

    def delete_all(self) -> int:
        try:
            result = self.session.exec(delete(PPEViolation))
            self.session.commit()
            return result.rowcount or 0
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not delete PPE violations.") from exc

    def _commit_and_refresh(
        self,
        model: ModelT,
        operation: str,
    ) -> ModelT:
        try:
            self.session.commit()
            self.session.refresh(model)
            return model
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError(f"Could not {operation}.") from exc
