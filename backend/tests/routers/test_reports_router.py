from datetime import datetime, timezone
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.models.camera import Camera
from app.models.physical_zone import PhysicalZone
from app.models.ppe_violation import PPEViolation
from app.models.zone_violation import ZoneViolation
from app.repositories.camera_repository import CameraRepository
from app.repositories.factory_repository import FactoryRepository
from app.repositories.physical_zone_repository import PhysicalZoneRepository
from app.repositories.ppe_violation_repository import PPEViolationRepository
from app.repositories.report_delivery_repository import ReportDeliveryRepository
from app.repositories.zone_violation_repository import ZoneViolationRepository
from app.repositories.report_schedule_repository import ReportScheduleRepository
from app.routers import reports
from app.services.reporting import ReportEmailError
from app.services.reporting.report_data import get_report_data_builder
from app.services.reporting.report_service import ReportService, get_report_service
from app.services.reporting.schedule_service import (
    ReportScheduleService,
    get_report_schedule_service,
)


def _seed(session) -> tuple[int, int]:
    factory = FactoryRepository(session).get_or_create_default_factory()
    zone = PhysicalZoneRepository(session).create(
        PhysicalZone(factory_id=factory.id, name="Zone A", zone_type="AREA")
    )
    camera = CameraRepository(session).create(
        Camera(
            factory_id=factory.id,
            name="Cam A",
            source_key="cam-a.mp4",
            home_zone_id=zone.id,
            is_active=True,
        )
    )
    now = datetime.now(timezone.utc)
    PPEViolationRepository(session).create(
        PPEViolation(
            camera_id=camera.id,
            source_key="cam-a.mp4",
            occurred_at=now,
            violation_type="missing_helmet_and_vest",
            details="two people missing PPE",
        )
    )
    ZoneViolationRepository(session).create(
        ZoneViolation(
            camera_id=camera.id,
            zone_name="Zone A",
            zone_type="RESTRICTED",
            source_key="cam-a.mp4",
            occurred_at=now,
            frame_index=1,
            severity="critical",
        )
    )
    return zone.id, camera.id


def _client(session, *, sender=None) -> TestClient:
    app = FastAPI()
    app.include_router(reports.router)
    fake_sender = sender or Mock()

    def _override_builder():
        return get_report_data_builder(session, Mock())

    def _override_service():
        builder = get_report_data_builder(session, Mock())
        storage = Mock()
        storage.upload_report.return_value = Mock(object_key="reports/test/mock.pdf")
        return ReportService(builder, storage, fake_sender, ReportDeliveryRepository(session))

    def _override_schedule_service():
        builder = get_report_data_builder(session, Mock())
        storage = Mock()
        storage.upload_report.return_value = Mock(object_key="reports/test/mock.pdf")
        report_service = ReportService(
            builder, storage, fake_sender, ReportDeliveryRepository(session)
        )
        return ReportScheduleService(ReportScheduleRepository(session), report_service)

    app.dependency_overrides[get_report_data_builder] = _override_builder
    app.dependency_overrides[get_report_service] = _override_service
    app.dependency_overrides[get_report_schedule_service] = _override_schedule_service
    return TestClient(app)


def test_download_pdf_returns_valid_pdf_response(session):
    _seed(session)
    client = _client(session)

    res = client.get("/reports/incidents.pdf", params={"range": "7D"})

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF-")
    assert "attachment" in res.headers["content-disposition"]
    assert "safety-report_" in res.headers["content-disposition"]


def test_download_pdf_rejects_invalid_range(session):
    client = _client(session)

    res = client.get("/reports/incidents.pdf", params={"range": "1Y"})

    assert res.status_code == 422


def test_preview_returns_insight_strings(session):
    _seed(session)
    client = _client(session)

    res = client.get("/reports/incidents/preview", params={"range": "7D"})

    assert res.status_code == 200
    body = res.json()
    assert body["grand_total"] == 2
    assert isinstance(body["insights"], list)
    assert len(body["insights"]) > 0
    assert all(isinstance(i, str) for i in body["insights"])


def test_preview_returns_vietnamese_zone_scope_label(session):
    client = _client(session)

    res = client.get("/reports/incidents/preview", params={"range": "7D", "language": "vi"})

    assert res.status_code == 200
    assert res.json()["zone_scope_label"] == "Tất cả khu vực"


def test_email_report_returns_503_when_disabled(session, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_EMAIL_ENABLED", False)
    client = _client(session)

    res = client.post("/reports/incidents/email", json={"recipients": ["a@example.com"]})

    assert res.status_code == 503


def test_email_report_succeeds_with_stubbed_sender(session, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_EMAIL_ENABLED", True)
    monkeypatch.setattr(settings, "REPORT_RECIPIENT_ALLOWLIST", [])
    _seed(session)
    sender = Mock()
    sender.validate_configuration.return_value = None
    client = _client(session, sender=sender)

    res = client.post(
        "/reports/incidents/email",
        json={"recipients": ["a@example.com", "b@example.com"], "range": "7D"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "sent"
    assert body["recipients"] == ["a@example.com", "b@example.com"]
    sender.send.assert_called_once()
    call_kwargs = sender.send.call_args.kwargs
    assert call_kwargs["recipients"] == ["a@example.com", "b@example.com"]


def test_email_report_rejects_recipient_not_on_allowlist(session, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_EMAIL_ENABLED", True)
    monkeypatch.setattr(settings, "REPORT_RECIPIENT_ALLOWLIST", ["@deheus.com"])
    sender = Mock()
    sender.validate_configuration.return_value = None
    client = _client(session, sender=sender)

    res = client.post(
        "/reports/incidents/email",
        json={"recipients": ["someone@gmail.com"]},
    )

    assert res.status_code == 422
    sender.send.assert_not_called()


def test_email_report_rejects_over_recipient_cap(session, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_EMAIL_ENABLED", True)
    monkeypatch.setattr(settings, "REPORT_RECIPIENT_ALLOWLIST", [])
    monkeypatch.setattr(settings, "REPORT_MAX_RECIPIENTS", 10)
    sender = Mock()
    sender.validate_configuration.return_value = None
    client = _client(session, sender=sender)
    recipients = [f"user{i}@example.com" for i in range(11)]

    res = client.post("/reports/incidents/email", json={"recipients": recipients})

    assert res.status_code == 422
    sender.send.assert_not_called()


def test_email_report_maps_send_failure_to_502(session, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_EMAIL_ENABLED", True)
    monkeypatch.setattr(settings, "REPORT_RECIPIENT_ALLOWLIST", [])
    sender = Mock()
    sender.validate_configuration.return_value = None
    sender.send.side_effect = ReportEmailError("All recipients were rejected by the mail server.")
    client = _client(session, sender=sender)

    res = client.post("/reports/incidents/email", json={"recipients": ["a@example.com"]})

    assert res.status_code == 502


def test_get_schedule_defaults_to_off(session):
    client = _client(session)

    res = client.get("/reports/schedule")

    assert res.status_code == 200
    body = res.json()
    assert body["frequency"] == "off"
    assert body["recipients"] == []
    assert body["next_run_at"] is None


def test_put_schedule_persists_weekly_config(session, monkeypatch):
    monkeypatch.setattr(settings, "REPORT_RECIPIENT_ALLOWLIST", [])
    client = _client(session)

    res = client.put(
        "/reports/schedule",
        json={
            "frequency": "weekly",
            "day_of_week": 0,
            "hour": 8,
            "minute": 30,
            "recipients": ["safety@example.com"],
            "include_snapshots": True,
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["frequency"] == "weekly"
    assert body["day_of_week"] == 0
    assert body["hour"] == 8
    assert body["minute"] == 30
    assert body["recipients"] == ["safety@example.com"]
    assert body["next_run_at"] is not None

    # Persisted — a fresh GET on a new client (same session/DB) sees it.
    res2 = _client(session).get("/reports/schedule")
    assert res2.json()["frequency"] == "weekly"


def test_put_schedule_rejects_weekly_without_day_of_week(session):
    client = _client(session)

    res = client.put(
        "/reports/schedule",
        json={"frequency": "weekly", "recipients": ["a@example.com"]},
    )

    assert res.status_code == 422


def test_put_schedule_rejects_enabling_without_recipients(session):
    client = _client(session)

    res = client.put(
        "/reports/schedule",
        json={"frequency": "monthly", "day_of_month": 1, "recipients": []},
    )

    assert res.status_code == 422


def test_put_schedule_off_does_not_require_recipients(session):
    client = _client(session)

    res = client.put("/reports/schedule", json={"frequency": "off", "recipients": []})

    assert res.status_code == 200
    assert res.json()["frequency"] == "off"
