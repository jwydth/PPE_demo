from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.camera_zone_view import CameraZoneView
from app.models.physical_zone import PhysicalZone
from app.repositories.camera_repository import CameraRepository
from app.repositories.camera_zone_view_repository import CameraZoneViewRepository
from app.repositories.factory_repository import FactoryRepository
from app.repositories.physical_zone_repository import PhysicalZoneRepository
from app.routers import zones
from app.services import zone_service as zone_service_module
from app.services.zone_service import ZoneService, get_zone_service


def _client(session) -> TestClient:
    app = FastAPI()
    app.include_router(zones.router)
    app.dependency_overrides[get_zone_service] = lambda: ZoneService(
        PhysicalZoneRepository(session),
        CameraZoneViewRepository(session),
        CameraRepository(session),
        FactoryRepository(session),
    )
    return TestClient(app)


def _zone_payload(name: str = "Restricted Area") -> dict:
    return {
        "video_name": "factory.mp4",
        "zone_name": name,
        "zone_type": "RESTRICTED",
        "dwell_threshold_seconds": 2,
        "is_active": True,
        "ui_shape_data": '{"type":"polygon"}',
        "flattened_coordinates": (
            '[{"x":0.1,"y":0.1},{"x":0.9,"y":0.1},{"x":0.9,"y":0.9}]'
        ),
    }


def test_zone_crud_uses_postgresql_models_and_preserves_api_shape(session):
    client = _client(session)

    created = client.post("/zones", json=_zone_payload())
    assert created.status_code == 200
    zone = created.json()
    assert zone["id"] == 1
    assert zone["video_name"] == "factory.mp4"
    assert zone["zone_name"] == "Restricted Area"
    assert isinstance(zone["ui_shape_data"], str)
    assert isinstance(zone["flattened_coordinates"], str)

    camera = CameraRepository(session).get_by_source_key("factory.mp4")
    assert camera is not None
    assert camera.name == "factory.mp4"
    assert camera.factory_id is not None
    assert camera.source_uri is None
    assert camera.is_active is True
    views = CameraZoneViewRepository(session).get_by_camera(camera.id)
    assert len(views) == 1
    physical_zone = PhysicalZoneRepository(session).get_by_id(
        views[0].physical_zone_id
    )
    assert physical_zone is not None
    assert physical_zone.name == "Restricted Area"

    listed = client.get("/zones/factory.mp4")
    assert listed.status_code == 200
    assert listed.json() == [zone]
    assert client.get("/zones/missing.mp4").json() == []

    payload = _zone_payload("Updated Area")
    payload["id"] = 999
    payload["dwell_threshold_seconds"] = 5
    updated = client.put(f"/zones/{zone['id']}", json=payload)
    assert updated.status_code == 200
    assert updated.json()["id"] == zone["id"]
    assert updated.json()["zone_name"] == "Updated Area"
    assert updated.json()["dwell_threshold_seconds"] == 5

    client.post("/zones", json=_zone_payload("Second Area"))
    deleted = client.delete("/zones/video/factory.mp4")
    assert deleted.status_code == 200
    assert deleted.json() == {"status": "success", "deleted": 2}
    assert client.get("/zones/factory.mp4").json() == []


def test_delete_single_zone_preserves_response_and_404(session):
    client = _client(session)
    zone_id = client.post("/zones", json=_zone_payload()).json()["id"]

    assert client.delete(f"/zones/{zone_id}").json() == {"status": "success"}
    missing = client.delete(f"/zones/{zone_id}")
    assert missing.status_code == 404


def test_load_zones_reads_active_camera_zone_views(session, monkeypatch):
    client = _client(session)
    created = client.post("/zones", json=_zone_payload()).json()
    camera = CameraRepository(session).get_by_source_key("factory.mp4")
    assert camera is not None
    assert camera.id is not None
    physical_zone_repository = PhysicalZoneRepository(session)
    inactive_zone = physical_zone_repository.create(
        PhysicalZone(
            factory_id=camera.factory_id,
            name="Inactive Area",
            zone_type="RESTRICTED",
            is_active=False,
        )
    )
    assert inactive_zone.id is not None
    CameraZoneViewRepository(session).create(
        CameraZoneView(
            camera_id=camera.id,
            physical_zone_id=inactive_zone.id,
            ui_shape_data={"type": "polygon"},
            normalized_coordinates=[{"x": 0.2, "y": 0.2}],
        )
    )

    monkeypatch.setattr(
        zone_service_module,
        "get_engine",
        lambda: session.get_bind(),
    )

    runtime_zones = zone_service_module.load_zones("factory.mp4")

    assert len(runtime_zones) == 1
    zone = runtime_zones[0]
    assert zone.camera_zone_view_id == created["id"]
    assert zone.zone_name == "Restricted Area"
    assert zone.zone_type == "RESTRICTED"
    assert zone.threshold == 2
    assert zone.poly == [
        (100.0, 100.0),
        (900.0, 100.0),
        (900.0, 900.0),
    ]
