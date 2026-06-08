from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.repositories.camera_repository import CameraRepository
from app.repositories.factory_repository import FactoryRepository
from app.repositories.zone_repository import ZoneRepository
from app.routers import zones
from app.services.zone_service import ZoneService, get_zone_service


def _client(session) -> TestClient:
    app = FastAPI()
    app.include_router(zones.router)
    app.dependency_overrides[get_zone_service] = lambda: ZoneService(
        ZoneRepository(session),
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
