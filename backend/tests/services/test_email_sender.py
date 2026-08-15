import smtplib
import ssl
from types import SimpleNamespace

import pytest

from app.schemas.analytics import AnalyticsCompare, AnalyticsSummary, AnalyticsTrend, SeverityCounts
from app.services.reporting import ReportEmailError
from app.services.reporting.email_sender import EmailAttachment, SmtpEmailSender, build_subject
from app.services.reporting.report_data import ReportData


class _FakeSMTP:
    """Stand-in for smtplib.SMTP — no real network connection is ever made."""

    last_sent = None

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        if _FakeSMTP.raise_at == "connect" and _FakeSMTP.raise_exc:
            raise _FakeSMTP.raise_exc

    def starttls(self, context=None):
        if _FakeSMTP.raise_at == "starttls" and _FakeSMTP.raise_exc:
            raise _FakeSMTP.raise_exc

    def login(self, username, password):
        if _FakeSMTP.raise_at == "login" and _FakeSMTP.raise_exc:
            raise _FakeSMTP.raise_exc

    def send_message(self, msg):
        if _FakeSMTP.raise_at == "send_message" and _FakeSMTP.raise_exc:
            raise _FakeSMTP.raise_exc
        _FakeSMTP.last_sent = msg

    def noop(self):
        return (250, b"OK")

    def quit(self):
        pass


def _configure_fake(monkeypatch, *, raise_exc=None, raise_at=None):
    _FakeSMTP.raise_exc = raise_exc
    _FakeSMTP.raise_at = raise_at
    _FakeSMTP.last_sent = None
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)


def _sender(**overrides) -> SmtpEmailSender:
    defaults = dict(
        REPORT_EMAIL_ENABLED=True,
        SMTP_HOST="smtp.example.com",
        SMTP_PORT=587,
        SMTP_USERNAME=None,
        SMTP_PASSWORD=None,
        SMTP_USE_STARTTLS=True,
        SMTP_USE_SSL=False,
        SMTP_TIMEOUT_SECONDS=5.0,
        SMTP_FROM_EMAIL="noreply@example.com",
        SMTP_FROM_NAME="Safety Bot",
    )
    defaults.update(overrides)
    return SmtpEmailSender(settings_obj=SimpleNamespace(**defaults))


def _send(sender: SmtpEmailSender, **overrides):
    payload = dict(
        recipients=["a@example.com", "b@example.com"],
        subject="Test Subject",
        html_body="<p>hi</p>",
        text_body="hi",
        attachments=[
            EmailAttachment(filename="safety-report.pdf", content=b"%PDF-1.4 fake", mime_type="application/pdf")
        ],
    )
    payload.update(overrides)
    sender.send(**payload)


def _report_data(*, language: str = "en") -> ReportData:
    return ReportData(
        company_name="De Heus LLC",
        factory_name="Test Factory",
        factory_location=None,
        range_label="Last 7 days",
        range_param="7D",
        zone_id=None,
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
        language=language,
    )


def test_build_subject_uses_vietnamese_template():
    data = _report_data(language="vi")

    subject = build_subject(data)

    assert subject.startswith("[Báo Cáo An Toàn]")


def test_validate_configuration_raises_when_disabled():
    sender = _sender(REPORT_EMAIL_ENABLED=False)

    with pytest.raises(ReportEmailError):
        sender.validate_configuration()


def test_validate_configuration_raises_when_host_missing():
    sender = _sender(SMTP_HOST=None)

    with pytest.raises(ReportEmailError):
        sender.validate_configuration()


def test_send_builds_multipart_mixed_over_alternative_with_attachment(monkeypatch):
    _configure_fake(monkeypatch)
    sender = _sender()

    _send(sender)

    msg = _FakeSMTP.last_sent
    assert msg is not None
    assert msg["Subject"] == "Test Subject"
    assert msg["To"] == "a@example.com, b@example.com"
    assert msg.get_content_type() == "multipart/mixed"

    alt_part = next(p for p in msg.iter_parts() if p.get_content_type() == "multipart/alternative")
    subtypes = {p.get_content_type() for p in alt_part.iter_parts()}
    assert subtypes == {"text/plain", "text/html"}

    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "safety-report.pdf"
    assert attachments[0].get_content_type() == "application/pdf"


def test_send_does_not_touch_the_network(monkeypatch):
    # If SmtpEmailSender ever tried a real connection this would hang/raise
    # (smtp.invalid is unroutable) instead of using the fake transport.
    _configure_fake(monkeypatch)
    sender = _sender(SMTP_HOST="smtp.invalid.example")

    _send(sender)

    assert _FakeSMTP.last_sent is not None


@pytest.mark.parametrize(
    "raise_exc, raise_at, expected_substring",
    [
        (smtplib.SMTPAuthenticationError(535, b"bad credentials"), "login", "authentication failed"),
        (
            smtplib.SMTPRecipientsRefused({"a@example.com": (550, b"no")}),
            "send_message",
            "recipients were rejected",
        ),
        (
            smtplib.SMTPSenderRefused(550, b"no", "noreply@example.com"),
            "send_message",
            "rejected the sender address",
        ),
        (TimeoutError(), "connect", "timed out"),
        (ssl.SSLError(), "connect", "TLS negotiation"),
        (ConnectionRefusedError(), "connect", "Could not connect"),
    ],
)
def test_send_maps_smtp_exceptions_to_report_email_error(
    monkeypatch, raise_exc, raise_at, expected_substring
):
    _configure_fake(monkeypatch, raise_exc=raise_exc, raise_at=raise_at)
    sender = _sender(SMTP_USERNAME="bot" if raise_at == "login" else None)

    with pytest.raises(ReportEmailError) as exc_info:
        _send(sender)

    assert expected_substring in str(exc_info.value)


def test_authentication_error_message_never_echoes_exception_detail(monkeypatch):
    secret_leak = smtplib.SMTPAuthenticationError(535, b"bad password: hunter2")
    _configure_fake(monkeypatch, raise_exc=secret_leak, raise_at="login")
    sender = _sender(SMTP_USERNAME="bot")

    with pytest.raises(ReportEmailError) as exc_info:
        _send(sender)

    assert "hunter2" not in str(exc_info.value)
