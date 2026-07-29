from unittest.mock import Mock

import pytest

from app.core.config import settings
from app.schemas.analytics import (
    AnalyticsCompare,
    AnalyticsSummary,
    AnalyticsTrend,
    SeverityCounts,
)
from app.services import ServiceValidationError
from app.services.reporting import ReportEmailError
from app.services.reporting.report_data import ReportData
from app.services.reporting.report_service import ReportService, validate_recipients
from app.storage import StorageError


def _report_data(factory_name: str = "Khu vực sản xuất") -> ReportData:
    return ReportData(
        company_name="De Heus LLC",
        factory_name=factory_name,
        factory_location=None,
        range_label="Last 7 days",
        range_param="7D",
        zone_scope_label="All zones",
        generated_at_local="29 Jul 2026, 14:32",
        timezone_label="UTC+07:00 (Asia/Ho_Chi_Minh)",
        summary=AnalyticsSummary(
            range="7D",
            zone_id=None,
            grand_total=0,
            zone_totals=[],
            severity_counts=SeverityCounts(),
            type_counts=[],
            active_zone_ids=[],
            open_incidents=0,
            active_cameras=0,
            total_cameras=0,
        ),
        trend=AnalyticsTrend(range="7D", bucket="day", zones=[], points=[]),
        compare=AnalyticsCompare(
            mode="week",
            zone_id=None,
            current_total=0,
            prior_total=0,
            delta_pct=0.0,
            points=[],
            severity_breakdown=[],
        ),
        top_incidents=[],
        insights=["No incidents recorded in this period across any monitored zone."],
        data_caveats=[],
    )


def _service(
    *, report_data: ReportData | None = None, sender: Mock | None = None
) -> tuple[ReportService, Mock, Mock, Mock, Mock]:
    builder = Mock()
    builder.build.return_value = report_data or _report_data()
    storage = Mock()
    storage.upload_report.return_value = Mock(object_key="reports/2026/07/abc123.pdf")
    sender = sender or Mock()
    delivery_repository = Mock()
    service = ReportService(builder, storage, sender, delivery_repository)
    return service, builder, storage, sender, delivery_repository


# ---- validate_recipients ----


def test_validate_recipients_dedupes_and_lowercases():
    result = validate_recipients(["A@B.com", "a@b.com ", "c@d.com"])

    assert result == ["a@b.com", "c@d.com"]


def test_validate_recipients_rejects_empty_list():
    with pytest.raises(ServiceValidationError):
        validate_recipients([])


def test_validate_recipients_rejects_over_cap(monkeypatch):
    monkeypatch.setattr(settings, "REPORT_MAX_RECIPIENTS", 2)

    with pytest.raises(ServiceValidationError):
        validate_recipients(["a@x.com", "b@x.com", "c@x.com"])


def test_validate_recipients_rejects_header_injection():
    with pytest.raises(ServiceValidationError):
        validate_recipients(["a@b.com\nBcc: evil@x.com"])


def test_validate_recipients_rejects_malformed_address():
    with pytest.raises(ServiceValidationError):
        validate_recipients(["not-an-email"])


def test_validate_recipients_allowlist_accepts_domain_suffix(monkeypatch):
    monkeypatch.setattr(settings, "REPORT_RECIPIENT_ALLOWLIST", ["@deheus.com"])

    result = validate_recipients(["safety@deheus.com"])

    assert result == ["safety@deheus.com"]


def test_validate_recipients_allowlist_rejects_outside_domain(monkeypatch):
    monkeypatch.setattr(settings, "REPORT_RECIPIENT_ALLOWLIST", ["@deheus.com"])

    with pytest.raises(ServiceValidationError):
        validate_recipients(["someone@gmail.com"])


def test_validate_recipients_allowlist_accepts_exact_match(monkeypatch):
    monkeypatch.setattr(settings, "REPORT_RECIPIENT_ALLOWLIST", ["safety@partner.com"])

    result = validate_recipients(["safety@partner.com"])

    assert result == ["safety@partner.com"]


def test_validate_recipients_empty_allowlist_allows_any(monkeypatch):
    monkeypatch.setattr(settings, "REPORT_RECIPIENT_ALLOWLIST", [])

    result = validate_recipients(["anyone@anywhere.com"])

    assert result == ["anyone@anywhere.com"]


# ---- ReportService.build_pdf ----


def test_build_pdf_returns_bytes_and_filename():
    service, builder, *_ = _service()

    pdf_bytes, filename = service.build_pdf(range_="7D", zone_id=None)

    builder.build.assert_called_once_with(range_="7D", zone_id=None)
    assert pdf_bytes.startswith(b"%PDF-")
    assert filename.startswith("safety-report_khu-vuc-san-xuat_7D_")
    assert filename.endswith(".pdf")


# ---- ReportService.email_report ----


def test_email_report_validates_sender_config_before_building_pdf():
    sender = Mock()
    sender.validate_configuration.side_effect = ReportEmailError("Email delivery is not enabled.")
    service, builder, *_ = _service(sender=sender)

    with pytest.raises(ReportEmailError):
        service.email_report(
            range_="7D", zone_id=None, recipients=["a@b.com"], subject=None,
            message=None, include_snapshots=False,
        )

    builder.build.assert_not_called()


def test_email_report_validates_recipients_before_building_pdf():
    service, builder, *_ = _service()

    with pytest.raises(ServiceValidationError):
        service.email_report(
            range_="7D", zone_id=None, recipients=[], subject=None,
            message=None, include_snapshots=False,
        )

    builder.build.assert_not_called()


def test_email_report_sends_and_writes_sent_audit_row():
    service, _builder, storage, sender, delivery_repository = _service()

    result = service.email_report(
        range_="7D", zone_id=None, recipients=["a@b.com"], subject=None,
        message=None, include_snapshots=False,
    )

    sender.send.assert_called_once()
    assert result.recipients == ["a@b.com"]
    assert result.object_key == "reports/2026/07/abc123.pdf"
    storage.upload_report.assert_called_once()
    delivery_repository.create.assert_called_once()
    audit_row = delivery_repository.create.call_args[0][0]
    assert audit_row.status == "SENT"
    assert audit_row.object_key == "reports/2026/07/abc123.pdf"


def test_email_report_continues_when_archive_fails():
    service, _builder, storage, sender, delivery_repository = _service()
    storage.upload_report.side_effect = StorageError("MinIO is down")

    result = service.email_report(
        range_="7D", zone_id=None, recipients=["a@b.com"], subject=None,
        message=None, include_snapshots=False,
    )

    sender.send.assert_called_once()
    assert result.object_key is None
    audit_row = delivery_repository.create.call_args[0][0]
    assert audit_row.status == "SENT"
    assert audit_row.object_key is None


def test_email_report_writes_failed_audit_row_and_reraises_on_send_failure():
    sender = Mock()
    sender.send.side_effect = ReportEmailError("All recipients were rejected by the mail server.")
    service, _builder, _storage, _sender, delivery_repository = _service(sender=sender)

    with pytest.raises(ReportEmailError):
        service.email_report(
            range_="7D", zone_id=None, recipients=["a@b.com"], subject=None,
            message=None, include_snapshots=False,
        )

    delivery_repository.create.assert_called_once()
    audit_row = delivery_repository.create.call_args[0][0]
    assert audit_row.status == "FAILED"
    assert "rejected" in audit_row.error_message


def test_email_report_skips_archive_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "REPORT_ARCHIVE_TO_MINIO", False)
    service, _builder, storage, _sender, _delivery_repository = _service()

    result = service.email_report(
        range_="7D", zone_id=None, recipients=["a@b.com"], subject=None,
        message=None, include_snapshots=False,
    )

    storage.upload_report.assert_not_called()
    assert result.object_key is None
