"""SMTP transport for the incident report email — stdlib smtplib +
email.message.EmailMessage only (no third-party email API/SDK)."""

import html
import logging
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from app.core.config import settings
from app.services.reporting import ReportEmailError, ReportEmailTimeoutError
from app.services.reporting.i18n import t
from app.services.reporting.report_data import ReportData, format_delta_label

logger = logging.getLogger(__name__)

_BOLD_MARKER = re.compile(r"\*\*(.+?)\*\*")

# Templates are keyed by language; body layout/markup is identical between
# the two, only the label text and word order differ (see plan §0.2 — this
# stays a template swap, not a full HTML rewrite per language).
_SUBJECT_TEMPLATES = {
    "en": "[Safety Report] {factory_name} — {range_label} — {total} incidents ({critical} critical)",
    "vi": "[Báo Cáo An Toàn] {factory_name} — {range_label} — {total} sự cố ({critical} nghiêm trọng)",
}

_HTML_BODY_TEMPLATE = """\
<div style="max-width:600px;margin:0 auto;font-family:Arial,Helvetica,sans-serif;color:#0f172a;">
  <div style="background:#0f172a;color:#d9f99d;padding:16px 20px;border-radius:6px 6px 0 0;">
    <div style="font-size:16px;font-weight:bold;">{company_name}</div>
    <div style="font-size:12px;">{report_title} — {range_label}</div>
  </div>
  <div style="border:1px solid #e2e8f0;border-top:none;padding:20px;border-radius:0 0 6px 6px;">
    <p style="margin:0 0 12px 0;">{factory_name} &middot; {zone_scope_label}</p>
    <table style="width:100%;border-collapse:collapse;margin-bottom:16px;">
      <tr>
        <td style="padding:8px;text-align:center;background:#f1f5f9;">
          <div style="font-size:11px;color:#64748b;">{total_label}</div>
          <div style="font-size:20px;font-weight:bold;">{total}</div>
        </td>
        <td style="padding:8px;text-align:center;background:#f1f5f9;">
          <div style="font-size:11px;color:#64748b;">{critical_label}</div>
          <div style="font-size:20px;font-weight:bold;color:#ef4444;">{critical}</div>
        </td>
        <td style="padding:8px;text-align:center;background:#f1f5f9;">
          <div style="font-size:11px;color:#64748b;">{high_label}</div>
          <div style="font-size:20px;font-weight:bold;color:#f97316;">{high}</div>
        </td>
        <td style="padding:8px;text-align:center;background:#f1f5f9;">
          <div style="font-size:11px;color:#64748b;">{vs_prior_label}</div>
          <div style="font-size:20px;font-weight:bold;">{delta_label}</div>
        </td>
      </tr>
    </table>
    {message_html}
    <p style="margin:0 0 8px 0;font-weight:bold;">{key_insights_label}</p>
    <ul style="margin:0 0 16px 0;padding-left:18px;">
      {insights_html}
    </ul>
    <p style="margin:0;color:#64748b;font-size:12px;">{pdf_attached_label}</p>
  </div>
</div>
"""

_TEXT_BODY_TEMPLATE = """\
{company_name} — {report_title}
{range_label} - {factory_name} - {zone_scope_label}

{total_label}: {total}
{critical_label}: {critical}
{high_label}: {high}
{vs_prior_label}: {delta_label}

{message_text}{key_insights_label}:
{insights_text}

{pdf_attached_label}
"""


@dataclass(frozen=True)
class EmailAttachment:
    filename: str
    content: bytes
    mime_type: str = "application/pdf"


def build_subject(data: ReportData) -> str:
    return _SUBJECT_TEMPLATES.get(data.language, _SUBJECT_TEMPLATES["en"]).format(
        factory_name=data.factory_name,
        range_label=data.range_label,
        total=data.summary.grand_total,
        critical=data.summary.severity_counts.Critical,
    )


def build_html_body(data: ReportData, custom_message: str | None) -> str:
    lang = data.language
    insights = data.insights[:3]
    insights_html = "".join(f"<li>{_bold_to_html(text)}</li>" for text in insights) or (
        f"<li>{html.escape(t('email_no_insights', lang))}</li>"
    )
    message_html = ""
    if custom_message:
        escaped = html.escape(custom_message).replace("\n", "<br/>")
        message_html = (
            '<p style="margin:0 0 16px 0;padding:10px;background:#f8fafc;'
            f'border-left:3px solid #94a3b8;">{escaped}</p>'
        )
    return _HTML_BODY_TEMPLATE.format(
        company_name=html.escape(data.company_name),
        report_title=html.escape(t("email_report_title", lang)),
        range_label=html.escape(data.range_label),
        factory_name=html.escape(data.factory_name),
        zone_scope_label=html.escape(data.zone_scope_label),
        total_label=html.escape(t("email_total", lang)),
        critical_label=html.escape(t("email_critical", lang)),
        high_label=html.escape(t("email_high", lang)),
        vs_prior_label=html.escape(t("email_vs_prior", lang)),
        key_insights_label=html.escape(t("email_key_insights", lang)),
        pdf_attached_label=html.escape(t("email_pdf_attached", lang)),
        total=data.summary.grand_total,
        critical=data.summary.severity_counts.Critical,
        high=data.summary.severity_counts.High,
        delta_label=format_delta_label(data.compare),
        message_html=message_html,
        insights_html=insights_html,
    )


def build_text_body(data: ReportData, custom_message: str | None) -> str:
    lang = data.language
    insights = data.insights[:3]
    insights_text = "\n".join(f"- {_strip_bold(text)}" for text in insights) or (
        f"- {t('email_no_insights', lang)}"
    )
    message_text = f"{custom_message}\n\n" if custom_message else ""
    return _TEXT_BODY_TEMPLATE.format(
        company_name=data.company_name,
        report_title=t("email_report_title", lang),
        range_label=data.range_label,
        factory_name=data.factory_name,
        zone_scope_label=data.zone_scope_label,
        total_label=t("email_total", lang),
        critical_label=t("email_critical", lang),
        high_label=t("email_high", lang),
        vs_prior_label=t("email_vs_prior", lang),
        key_insights_label=t("email_key_insights", lang),
        pdf_attached_label=t("email_pdf_attached", lang),
        total=data.summary.grand_total,
        critical=data.summary.severity_counts.Critical,
        high=data.summary.severity_counts.High,
        delta_label=format_delta_label(data.compare),
        message_text=message_text,
        insights_text=insights_text,
    )


def _bold_to_html(text: str) -> str:
    return _BOLD_MARKER.sub(r"<b>\1</b>", html.escape(text))


def _strip_bold(text: str) -> str:
    return _BOLD_MARKER.sub(r"\1", text)


class SmtpEmailSender:
    def __init__(self, settings_obj=settings) -> None:
        self.settings = settings_obj

    def validate_configuration(self) -> None:
        if not self.settings.REPORT_EMAIL_ENABLED:
            raise ReportEmailError("Email delivery is not enabled on this server.")
        if not self.settings.SMTP_HOST:
            raise ReportEmailError("SMTP is not configured on this server (SMTP_HOST missing).")
        if not self.settings.SMTP_FROM_EMAIL:
            raise ReportEmailError(
                "SMTP is not configured on this server (SMTP_FROM_EMAIL missing)."
            )

    def verify_connection(self) -> dict[str, str]:
        self.validate_configuration()
        try:
            server = self._connect()
            try:
                server.noop()
            finally:
                server.quit()
        except Exception as exc:
            raise self._map_exception(exc) from exc
        return {"host": self.settings.SMTP_HOST, "port": str(self.settings.SMTP_PORT)}

    def send(
        self,
        *,
        recipients: list[str],
        subject: str,
        html_body: str,
        text_body: str,
        attachments: list[EmailAttachment],
    ) -> None:
        self.validate_configuration()
        message = self._build_message(recipients, subject, html_body, text_body, attachments)
        try:
            server = self._connect()
            try:
                server.send_message(message)
            finally:
                server.quit()
        except Exception as exc:
            raise self._map_exception(exc) from exc
        logger.info(
            "Sent incident report email to %d recipient(s) via %s:%s.",
            len(recipients),
            self.settings.SMTP_HOST,
            self.settings.SMTP_PORT,
        )

    def _build_message(
        self,
        recipients: list[str],
        subject: str,
        html_body: str,
        text_body: str,
        attachments: list[EmailAttachment],
    ) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((self.settings.SMTP_FROM_NAME, self.settings.SMTP_FROM_EMAIL))
        message["To"] = ", ".join(recipients)
        message["Date"] = formatdate(localtime=True)
        domain = (self.settings.SMTP_FROM_EMAIL or "").split("@")[-1] or None
        message["Message-ID"] = make_msgid(domain=domain)
        message.set_content(text_body)
        # add_attachment() must come after add_alternative() — reversing the
        # order breaks the multipart/mixed(multipart/alternative) nesting and
        # some clients (Gmail) will hide the body. See plan §4.1.
        message.add_alternative(html_body, subtype="html")
        for attachment in attachments:
            maintype, subtype = attachment.mime_type.split("/", 1)
            message.add_attachment(
                attachment.content,
                maintype=maintype,
                subtype=subtype,
                filename=attachment.filename,
            )
        return message

    def _connect(self) -> smtplib.SMTP:
        host = self.settings.SMTP_HOST
        port = self.settings.SMTP_PORT
        timeout = self.settings.SMTP_TIMEOUT_SECONDS
        server: smtplib.SMTP | None = None
        try:
            if self.settings.SMTP_USE_SSL:
                server = smtplib.SMTP_SSL(
                    host, port, timeout=timeout, context=ssl.create_default_context()
                )
            else:
                server = smtplib.SMTP(host, port, timeout=timeout)
                if self.settings.SMTP_USE_STARTTLS:
                    server.starttls(context=ssl.create_default_context())
            if self.settings.SMTP_USERNAME:
                server.login(self.settings.SMTP_USERNAME, self.settings.SMTP_PASSWORD)
            return server
        except Exception:
            if server is not None:
                try:
                    server.quit()
                except Exception:
                    pass
            raise

    def _map_exception(self, exc: Exception) -> ReportEmailError:
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            # Never logger.exception()/include str(exc) here — some SMTP
            # servers echo the attempted credential in the error response.
            logger.warning(
                "SMTP authentication failed (host=%s, port=%s).",
                self.settings.SMTP_HOST,
                self.settings.SMTP_PORT,
            )
            return ReportEmailError(
                "SMTP authentication failed. Check SMTP_USERNAME / SMTP_PASSWORD."
            )
        if isinstance(exc, smtplib.SMTPRecipientsRefused):
            logger.warning("SMTP server rejected all recipients.", exc_info=True)
            return ReportEmailError("All recipients were rejected by the mail server.")
        if isinstance(exc, smtplib.SMTPSenderRefused):
            logger.warning("SMTP server rejected the sender address.", exc_info=True)
            return ReportEmailError("The mail server rejected the sender address.")
        if isinstance(exc, TimeoutError):
            logger.warning(
                "SMTP server timed out (host=%s, port=%s).",
                self.settings.SMTP_HOST,
                self.settings.SMTP_PORT,
            )
            return ReportEmailTimeoutError("SMTP server timed out.")
        if isinstance(exc, ssl.SSLError):
            logger.warning("TLS negotiation with the SMTP server failed.", exc_info=True)
            return ReportEmailError("TLS negotiation with the SMTP server failed.")
        if isinstance(exc, OSError):
            logger.warning("Could not connect to the SMTP server.", exc_info=True)
            return ReportEmailError("Could not connect to the SMTP server.")
        logger.warning("Could not send the report email.", exc_info=True)
        return ReportEmailError("Could not send the report email.")
