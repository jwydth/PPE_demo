from app.models.factory import Factory
from app.models.physical_zone import PhysicalZone
from app.repositories.factory_repository import FactoryRepository
from app.repositories.physical_zone_repository import PhysicalZoneRepository


def _factory_id(session, name: str = "Default Factory") -> int:
    repository = FactoryRepository(session)
    factory = repository.get_by_name(name)
    if factory is None:
        factory = repository.create(Factory(name=name))
    assert factory.id is not None
    return factory.id


def _physical_zone(factory_id: int, name: str = "Restricted Area") -> PhysicalZone:
    return PhysicalZone(
        factory_id=factory_id,
        name=name,
        zone_type="RESTRICTED",
        floor_plan_polygon=[{"x": 0.1, "y": 0.2}],
        dwell_threshold_seconds=2,
    )


def test_physical_zone_repository_create_read_update_and_deactivate(session):
    factory_id = _factory_id(session)
    repository = PhysicalZoneRepository(session)

    zone = repository.create(_physical_zone(factory_id))

    assert zone.id is not None
    assert repository.get_by_id(zone.id) == zone
    assert repository.get_by_factory(factory_id) == [zone]
    assert repository.get_by_factory_and_name(factory_id, "Restricted Area") == zone
    assert repository.get_by_factory_and_name(factory_id, "Missing") is None

    zone.name = "Updated Area"
    zone.dwell_threshold_seconds = 5
    updated = repository.update(zone)
    assert updated.name == "Updated Area"
    assert updated.dwell_threshold_seconds == 5

    deactivated = repository.deactivate(zone.id)
    assert deactivated is not None
    assert deactivated.is_active is False
    assert repository.deactivate(999_999) is None


def test_physical_zone_repository_filters_by_factory_and_deletes(session):
    default_factory_id = _factory_id(session)
    other_factory_id = _factory_id(session, "Other Factory")
    repository = PhysicalZoneRepository(session)

    first = repository.create(_physical_zone(default_factory_id, "First Area"))
    second = repository.create(_physical_zone(default_factory_id, "Second Area"))
    other = repository.create(_physical_zone(other_factory_id, "Other Area"))

    assert repository.get_by_factory(default_factory_id) == [first, second]
    assert repository.get_by_factory(other_factory_id) == [other]
    assert first.id is not None
    assert repository.delete(first.id) is True
    assert repository.get_by_factory(default_factory_id) == [second]
    assert repository.delete(first.id) is False
