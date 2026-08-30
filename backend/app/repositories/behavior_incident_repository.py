from datetime import datetime
from typing import Annotated, TypeVar

from fastapi import Depends
from sqlalchemy import delete, func
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.db.session import get_session
from app.models.behavior_incident import (
    BehaviorEvidence,
    BehaviorIncident,
    BehaviorIncidentSubject,
)
from app.repositories import RepositoryError
from app.repositories.incident_filters import area_zone_predicate

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

    def get_evidence_for_incidents(
        self, incident_ids: list[int]
    ) -> dict[int, list[BehaviorEvidence]]:
        """Batched form of get_evidence() for multiple incidents in one query —
        avoids an N+1 when building a unified incident list (PERF_PLAN.md
        Tier 3.3). Each incident's evidence list is ordered newest-first, same
        as get_evidence()."""
        if not incident_ids:
            return {}
        try:
            statement = (
                select(BehaviorEvidence)
                .where(BehaviorEvidence.behavior_incident_id.in_(incident_ids))
                .order_by(BehaviorEvidence.occurred_at.desc(), BehaviorEvidence.id.desc())
            )
            by_incident: dict[int, list[BehaviorEvidence]] = {}
            for evidence in self.session.exec(statement).all():
                by_incident.setdefault(evidence.behavior_incident_id, []).append(evidence)
            return by_incident
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list behavior incident evidence.") from exc

    def get_by_ids(self, ids: list[int]) -> list[BehaviorIncident]:
        """Fetch a specific set of rows in one query. Used by the paginated
        incident feed, which decides *which* rows a page holds from a merged
        cross-table index query and then hydrates only those."""
        if not ids:
            return []
        try:
            statement = select(BehaviorIncident).where(BehaviorIncident.id.in_(ids))  # type: ignore[union-attr]
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not read behavior incidents.") from exc

    def count_all(self) -> int:
        try:
            return int(
                self.session.exec(select(func.count()).select_from(BehaviorIncident)).one()
            )
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not count behavior incidents.") from exc

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

    def list_between(
        self,
        *,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int,
        zone_id: int | None = None,
        camera_id: int | None = None,
    ) -> list[BehaviorIncident]:
        try:
            statement = select(BehaviorIncident)
            if date_from is not None:
                statement = statement.where(BehaviorIncident.started_at >= date_from)
            if date_to is not None:
                statement = statement.where(BehaviorIncident.started_at <= date_to)
            # Filtered in SQL, not after the fetch: applying these to an
            # already-LIMITed page returns only the matches that happen to fall
            # inside the newest `limit` rows overall, which reads as "this zone
            # has no incidents" whenever a busier zone fills that window.
            if zone_id is not None:
                statement = statement.where(area_zone_predicate(BehaviorIncident, zone_id))
            if camera_id is not None:
                statement = statement.where(BehaviorIncident.camera_id == camera_id)
            statement = statement.order_by(
                BehaviorIncident.started_at.desc(),
                BehaviorIncident.id.desc(),
            ).limit(limit)
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError(
                "Could not list behavior incidents by date range."
            ) from exc

    def delete(self, incident_id: int) -> bool:
        incident = self.get_by_id(incident_id)
        if incident is None:
            return False
        try:
            # subjects/evidence cascade via ondelete="CASCADE" (passive_deletes=True
            # on the model relationships, so this is a DB-level cascade, not ORM-issued
            # per-child DELETEs).
            self.session.delete(incident)
            self.session.commit()
            return True
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not delete behavior incident.") from exc

    def list_all_evidence(self) -> list[BehaviorEvidence]:
        try:
            statement = select(BehaviorEvidence).order_by(BehaviorEvidence.id)
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list behavior evidence.") from exc

    def delete_all(self) -> int:
        try:
            count = self.session.exec(select(func.count()).select_from(BehaviorIncident)).one()
            self.session.exec(delete(BehaviorIncidentSubject))
            self.session.exec(delete(BehaviorEvidence))
            self.session.exec(delete(BehaviorIncident))
            self.session.commit()
            return int(count or 0)
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not delete behavior incidents.") from exc

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
