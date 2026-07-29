from typing import Annotated

from fastapi import Depends
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from app.db.session import get_session
from app.models.report_schedule import ReportSchedule
from app.repositories import RepositoryError

_SINGLETON_ID = 1


class ReportScheduleRepository:
    def __init__(
        self,
        session: Annotated[Session, Depends(get_session)],
    ) -> None:
        self.session = session

    def get_or_create(self) -> ReportSchedule:
        existing = self._get()
        if existing is not None:
            return existing

        schedule = ReportSchedule(id=_SINGLETON_ID)
        self.session.add(schedule)
        try:
            self.session.commit()
            self.session.refresh(schedule)
            return schedule
        except IntegrityError:
            self.session.rollback()
            existing = self._get()
            if existing is None:
                raise RepositoryError("Could not create default report schedule.")
            return existing
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not create default report schedule.") from exc

    def update(self, schedule: ReportSchedule) -> ReportSchedule:
        self.session.add(schedule)
        try:
            self.session.commit()
            self.session.refresh(schedule)
            return schedule
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not update report schedule.") from exc

    def _get(self) -> ReportSchedule | None:
        try:
            statement = select(ReportSchedule).where(ReportSchedule.id == _SINGLETON_ID)
            return self.session.exec(statement).first()
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not read report schedule.") from exc
