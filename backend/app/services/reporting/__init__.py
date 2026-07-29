"""Incident-analytics PDF report generation + SMTP delivery.

This package reads only `analytics_service`, `incident_service`,
`factory_repository`, `physical_zone_repository`, and `evidence_storage` — it
must never import `app/services/ppe/` or `app/services/zone_service.py` (see
docs/architecture/REFACTOR_NOTES.md's import-boundary rule). Those are
detection-pipeline internals, not incident storage.
"""

from app.services import ServiceError


class ReportEmailError(ServiceError):
    """SMTP transport or configuration failure."""


class ReportEmailTimeoutError(ReportEmailError):
    """Raised when the SMTP server does not respond in time."""


__all__ = ["ReportEmailError", "ReportEmailTimeoutError"]
