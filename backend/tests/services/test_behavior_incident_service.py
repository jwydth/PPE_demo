from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from collections.abc import Generator

import pytest
from sqlalchemy import BigInteger, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import app.models  # noqa: F401
from app.repositories.behavior_incident_repository import BehaviorIncidentRepository
from app.repositories.camera_repository import CameraRepository
from app.repositories.factory_repository import FactoryRepository
from app.services.behavior_incident_service import BehaviorIncidentService


NOW = datetime(2026, 7, 6, 8, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class _StoredObject:
    object_key: str
    object_url: str
    bucket_name: str = "test-bucket"


class _FakeStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def upload_behavior_snapshot(self, local_file_path: str | Path) -> _StoredObject:
        assert Path(local_file_path).is_file()
        return _StoredObject(
            object_key="behavior-incidents/2026/07/06/fall.jpg",
            object_url="http://storage.local/behavior-incidents/2026/07/06/fall.jpg",
        )

    def get_object_url(self, object_key: str) -> str:
        return f"http://storage.local/{object_key}"

    def delete_object(self, object_key: str) -> None:
        self.deleted.append(object_key)


@compiles(BigInteger, "sqlite")
def _compile_big_integer_for_sqlite(_type, _compiler, **_kwargs) -> str:
    return "INTEGER"


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs) -> str:
    return "JSON"


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(
        dbapi_connection: sqlite3.Connection,
        _connection_record,
    ) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)
    with Session(engine) as test_session:
        yield test_session
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


def test_behavior_service_persists_fall_bundle_and_resolves_camera(session, tmp_path):
    snapshot = tmp_path / "fall.jpg"
    snapshot.write_bytes(b"fake jpeg")
    storage = _FakeStorage()
    service = BehaviorIncidentService(
        BehaviorIncidentRepository(session),
        storage,
        CameraRepository(session),
        FactoryRepository(session),
    )

    bundle = service.persist_fall_incident(
        timestamp=NOW,
        details="Track 42 fall detected at frame 20",
        local_snapshot_path=snapshot,
        video_name="factory-line.mp4",
        frame_start=20,
        frame_end=20,
        track_id=42,
        person_index=1,
        bounding_box={"x1": 10, "y1": 20, "x2": 100, "y2": 200},
        confidence=0.88,
        keypoints=[[1.0, 2.0, 0.9]],
        features={"wide_box": 0.74},
        metadata={"model_name": "yolo26m-pose"},
    )

    incident = bundle.incident
    assert incident.id > 0
    assert incident.camera_id is not None
    assert incident.video_name == "factory-line.mp4"
    assert incident.behavior_type.value == "FALL_DETECTED"
    assert incident.status.value == "NEW"
    assert incident.severity and incident.severity.value == "HIGH"
    assert incident.confidence == 0.88
    assert incident.track_id == 42
    assert incident.frame_start == 20
    assert incident.snapshot_url == "http://storage.local/behavior-incidents/2026/07/06/fall.jpg"
    assert incident.subjects[0].features == {"wide_box": 0.74}
    assert incident.evidence[0].object_key == "behavior-incidents/2026/07/06/fall.jpg"
    assert not snapshot.exists()

    recent = service.list_recent(limit=10)
    assert [item.id for item in recent] == [incident.id]
    assert service.get_incident(incident.id).id == incident.id

    assert service.delete_all_behavior_incidents() == 1
    assert storage.deleted == ["behavior-incidents/2026/07/06/fall.jpg"]
    assert service.list_recent(limit=10) == []
