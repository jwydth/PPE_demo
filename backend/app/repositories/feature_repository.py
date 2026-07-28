from typing import Annotated
from fastapi import Depends
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select
from app.db.session import get_session
from app.models.feature import Feature
from app.repositories import RepositoryError


class FeatureRepository:
    def __init__(
        self,
        session: Annotated[Session, Depends(get_session)],
    ) -> None:
        self.session = session

    def create(self, feature: Feature) -> Feature:
        try:
            self.session.add(feature)
            self.session.commit()
            self.session.refresh(feature)
            return feature
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not create feature.") from exc

    def get_by_id(self, feature_id: int) -> Feature | None:
        try:
            return self.session.get(Feature, feature_id)
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not read feature by ID.") from exc

    def get_by_key(self, key: str) -> Feature | None:
        try:
            statement = select(Feature).where(Feature.key == key)
            return self.session.exec(statement).first()
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not read feature by key.") from exc

    def list_all(self) -> list[Feature]:
        try:
            statement = select(Feature).order_by(Feature.id)
            return list(self.session.exec(statement).all())
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not list features.") from exc
