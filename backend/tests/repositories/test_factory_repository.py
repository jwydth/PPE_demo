from app.models.factory import Factory
from app.repositories.factory_repository import (
    DEFAULT_FACTORY_NAME,
    FactoryRepository,
)


def test_factory_repository_create_read_and_default_bootstrap(session):
    repository = FactoryRepository(session)

    default_factory = repository.get_or_create_default_factory()
    same_default = repository.get_or_create_default_factory()

    assert default_factory.id is not None
    assert same_default.id == default_factory.id
    assert default_factory.name == DEFAULT_FACTORY_NAME
    assert repository.get_by_name(DEFAULT_FACTORY_NAME) == default_factory

    created = repository.create(
        Factory(
            name="Second Factory",
            location="Building B",
        )
    )
    assert created.id is not None
    assert repository.get_by_name("Second Factory") == created
    assert repository.get_by_name("Missing Factory") is None
