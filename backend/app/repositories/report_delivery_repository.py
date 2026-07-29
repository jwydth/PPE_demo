from typing import Annotated

from fastapi import Depends
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.db.session import get_session
from app.models.report_delivery import ReportDelivery
from app.repositories import RepositoryError


class ReportDeliveryRepository:
    def __init__(
        self,
        session: Annotated[Session, Depends(get_session)],
    ) -> None:
        self.session = session

    def create(self, delivery: ReportDelivery) -> ReportDelivery:
        self.session.add(delivery)
        return self._commit_and_refresh(delivery, "create report delivery")

    def _commit_and_refresh(self, delivery: ReportDelivery, operation: str) -> ReportDelivery:
        try:
            self.session.commit()
            self.session.refresh(delivery)
            return delivery
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError(f"Could not {operation}.") from exc
