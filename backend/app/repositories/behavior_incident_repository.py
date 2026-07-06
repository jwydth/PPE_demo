from typing import Annotated, TypeVar

from fastapi import Depends
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.db.session import get_session
from app.models.behavior_incident import (
    BehaviorEvidence,
    BehaviorIncident,
    BehaviorIncidentSubject,
)
from app.repositories import RepositoryError

ModelT = TypeVar("ModelT", BehaviorIncident, BehaviorIncidentSubject, BehaviorEvidence)


class BehaviorIncidentRepository:
    def __init__(
        self,
        session: Annotated[Session, Depends(get_session)],
    ) -> None:
        self.session = session

    def create(self, incident: BehaviorIncident) -> BehaviorIncident:
        self.session.add(incident)
        return self._commit_and_refresh(incident, "create behavior incident")

    def create_subject(
        self,
        subject: BehaviorIncidentSubject,
    ) -> BehaviorIncidentSubject:
        self.session.add(subject)
        return self._commit_and_refresh(subject, "create behavior incident subject")

    def create_evidence(self, evidence: BehaviorEvidence) -> BehaviorEvidence:
        self.session.add(evidence)
        return self._commit_and_refresh(evidence, "create behavior evidence")

    def get_by_id(self, incident_id: int) -> BehaviorIncident | None:
        try:
            return self.session.get(BehaviorIncident, incident_id)
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not read behavior incident.") from exc

    def get_subjects(self, incident_id: int) -> list[BehaviorIncidentSubject]:
        try:
            statement = (
                select(BehaviorIncidentSubject)
                .where(BehaviorIncidentSubject.behavior_incident_id == incident_id)
                .order_by(BehaviorIncidentSubject.id)
            )
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list behavior incident subjects.") from exc

    def get_evidence(self, incident_id: int) -> list[BehaviorEvidence]:
        try:
            statement = (
                select(BehaviorEvidence)
                .where(BehaviorEvidence.behavior_incident_id == incident_id)
                .order_by(BehaviorEvidence.occurred_at.desc(), BehaviorEvidence.id.desc())
            )
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list behavior incident evidence.") from exc

    def list_recent(
        self,
        *,
        limit: int,
        behavior_type: str | None = None,
        status: str | None = None,
        camera_id: int | None = None,
    ) -> list[BehaviorIncident]:
        try:
            statement = select(BehaviorIncident)
            if behavior_type:
                statement = statement.where(BehaviorIncident.behavior_type == behavior_type)
            if status:
                statement = statement.where(BehaviorIncident.status == status)
            if camera_id is not None:
                statement = statement.where(BehaviorIncident.camera_id == camera_id)
            statement = (
                statement
                .order_by(BehaviorIncident.started_at.desc(), BehaviorIncident.id.desc())
                .limit(limit)
            )
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list behavior incidents.") from exc

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
