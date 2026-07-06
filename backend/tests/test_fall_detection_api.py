from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.behavior_incident import BehaviorIncidentSeverity, BehaviorIncidentStatus, BehaviorType
from app.routers import fall_detection
from app.schemas.fall_detection import BehaviorIncidentRead
from app.services.behavior_incident_service import get_behavior_incident_service
from app.services.fall_detector import FallModelUnavailable


class _UnavailableDetector:
    def predict_image(self, *_args, **_kwargs):
        raise FallModelUnavailable(
            "Fall detection model is unavailable: weights not found at test.pt"
        )


class _FakeBehaviorIncidentService:
    def list_recent(self, **_kwargs):
        return [
            BehaviorIncidentRead(
                id=1,
                video_name="factory.mp4",
                behavior_type=BehaviorType.FALL_DETECTED,
                status=BehaviorIncidentStatus.NEW,
                severity=BehaviorIncidentSeverity.HIGH,
                confidence=0.91,
                track_id=3,
                frame_start=20,
                frame_end=20,
                timestamp="2026-07-06T08:00:00+00:00",
                details="Track 3 fall detected at frame 20",
                metadata={"model_name": "yolo26m-pose"},
                snapshot_url="http://storage/fall.jpg",
            )
        ]

    def get_incident(self, incident_id: int):
        return self.list_recent()[0]


def test_fall_predict_returns_503_when_model_missing(monkeypatch):
    app = FastAPI()
    app.include_router(fall_detection.router)
    monkeypatch.setattr(fall_detection, "_fall_detector", _UnavailableDetector())

    response = TestClient(app).post(
        "/fall-detection/predict",
        files={"file": ("frame.jpg", b"fake-image", "image/jpeg")},
    )

    assert response.status_code == 503
    assert "weights not found" in response.json()["detail"]


def test_behavior_incident_list_endpoint_shape():
    app = FastAPI()
    app.include_router(fall_detection.router)
    app.dependency_overrides[get_behavior_incident_service] = lambda: _FakeBehaviorIncidentService()

    response = TestClient(app).get("/behavior-incidents?limit=10")

    assert response.status_code == 200
    assert response.json()[0]["behavior_type"] == "FALL_DETECTED"
    assert response.json()[0]["snapshot_url"] == "http://storage/fall.jpg"
