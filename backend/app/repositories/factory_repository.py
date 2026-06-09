from typing import Annotated

from fastapi import Depends
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from app.db.session import get_session
from app.models.factory import Factory
from app.repositories import RepositoryError

DEFAULT_FACTORY_NAME = "Default Factory"


class FactoryRepository:
    def __init__(
        self,
        session: Annotated[Session, Depends(get_session)],
    ) -> None:
        self.session = session

    def create(self, factory: Factory) -> Factory:
        self.session.add(factory)
        return self._commit_and_refresh(factory, "create factory")

    def get_by_name(self, name: str) -> Factory | None:
        try:
            statement = select(Factory).where(Factory.name == name)
            return self.session.exec(statement).first()
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not read factory.") from exc

    def get_or_create_default_factory(self) -> Factory:
        existing = self.get_by_name(DEFAULT_FACTORY_NAME)
        if existing is not None:
            return existing

        factory = Factory(name=DEFAULT_FACTORY_NAME)
        self.session.add(factory)
        try:
            self.session.commit()
            self.session.refresh(factory)
            return factory
        except IntegrityError:
            self.session.rollback()
            existing = self.get_by_name(DEFAULT_FACTORY_NAME)
            if existing is None:
                raise RepositoryError("Could not create default factory.")
            return existing
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError("Could not create default factory.") from exc

    def _commit_and_refresh(self, factory: Factory, operation: str) -> Factory:
        try:
            self.session.commit()
            self.session.refresh(factory)
            return factory
        except SQLAlchemyError as exc:
            self.session.rollback()
            raise RepositoryError(f"Could not {operation}.") from exc
