# Incident Analytics PDF Report + SMTP Email Delivery

> **Purpose:** On-demand PDF export of the Incident Analytics dashboard, and an SMTP route that
> emails that PDF as a safety report. Built from
> `PDF_REPORT_EMAIL_IMPLEMENTATION_PLAN.md`; this note records what was actually built, the
> locked decisions, and the known limitations.

## What was built

**Backend** (`backend/app/services/reporting/`):

- `report_data.py` — `ReportDataBuilder` assembles a `ReportData` payload by reading through
  `AnalyticsService` / `UnifiedIncidentService` / `FactoryRepository` (never
  `app/services/ppe/` or `zone_service.py` — see `REFACTOR_NOTES.md`'s import-boundary rule).
  Converts UTC timestamps to `REPORT_TIMEZONE` for display.
- `insights.py` — deterministic, rule-based "key insights" (trend direction, hotspot zone,
  dominant type, critical severity, severity shift, peak bucket, quiet zones, camera coverage,
  all-clear), capped at 6 bullets, plus data-completeness caveats.
- `pdf_renderer.py` — pure function `render_incident_report(data, snapshots) -> bytes` using
  ReportLab (`SimpleDocTemplate` + `reportlab.graphics` charts). Vendors DejaVu Sans
  (`fonts/DejaVuSans*.ttf`) so Vietnamese zone/factory names render correctly — ReportLab's
  bundled `Vera.ttf` is missing the required diacritics.
- `email_sender.py` — `SmtpEmailSender` using stdlib `smtplib` + `email.message.EmailMessage`.
  No third-party email API. Builds `multipart/mixed` (attachment) wrapping
  `multipart/alternative` (text + HTML), with a defined mapping from SMTP exceptions to
  `ReportEmailError` / `ReportEmailTimeoutError`.
- `report_service.py` — `ReportService` orchestrator (`build_pdf`, `email_report`),
  `validate_recipients` (dedupe, cap, header-injection guard, allowlist), `fetch_snapshots`
  (evidence thumbnails, Critical/High only, downscaled to ≤800px, every failure swallowed and
  logged), filename slugification, and the `report_deliveries` audit trail.
- `app/models/report_delivery.py` + `app/repositories/report_delivery_repository.py` — one audit
  row per email attempt (`SENT` or `FAILED`), including archive `object_key` when available.
- `app/routers/reports.py` — `GET /reports/incidents/preview`, `GET /reports/incidents.pdf`,
  `POST /reports/incidents/email`; `GET /health/smtp` added to `app/routers/testing.py`.

**Frontend:** `report-export-dialog.tsx` (preview, snapshot toggle, PDF download, recipient
chips + subject/message + send, idle/sending/success/error states), wired into
`analytics-dashboard.tsx` via an "Export report" toolbar button that passes the dashboard's
*current* `timeRange` and `selectedZone` — the export always matches what the user is looking at.

### Recurring schedule (weekly / monthly / off)

Follow-up to the original plan's "no scheduled reports" limitation:

- `app/models/report_schedule.py` — a **singleton** `report_schedules` row (id=1). The app has no
  auth/multi-tenancy, so one site-wide schedule matches every other `REPORT_*` setting's scope.
- `app/services/reporting/schedule.py` — pure date-math (`most_recent_slot`, `next_run_at`,
  `is_due`). Schedule times are wall-clock in `REPORT_TIMEZONE`; `day_of_week` follows Python's
  `date.weekday()` (0=Monday..6=Sunday); `day_of_month` is clamped to 1-28 so every month has
  that day (no "day 31 skips February").
- `app/services/reporting/schedule_service.py` — `ReportScheduleService` (validation: recipients
  required to enable, day field required for the chosen frequency) and `run_due_schedule()`, the
  entry point the periodic check calls. Weekly sends a `7D`-range report, monthly sends `30D`.
  A failed send does **not** stamp `last_sent_at`, so it retries on the next check instead of
  waiting a full week/month — each attempt still lands a row in `report_deliveries`.
- `app/services/reporting/scheduler.py` + `app/main.py` — an in-process `APScheduler`
  `BackgroundScheduler` job every 15 minutes. Coarser than minute-level cron, but the date-math
  finds the most recent due slot regardless of tick alignment, so nothing is missed.
- `GET`/`PUT /reports/schedule` — read/update the schedule; response includes a computed
  `next_run_at` for display.

**Frontend:** `schedule-report-dialog.tsx` — Off/Weekly/Monthly toggle, day-of-week or
day-of-month + time picker, recipient chips, "Next send" / "Last sent" status — wired into
`analytics-dashboard.tsx` via a "Schedule" toolbar button next to "Export report".

## Locked decisions

| # | Decision |
|---|---|
| D1 | PDF engine = ReportLab, pure-Python — no system dependencies (Windows-friendly). |
| D2 | Charts drawn with `reportlab.graphics`, not matplotlib. |
| D3 | Email via stdlib `smtplib` + `EmailMessage` — no third-party email provider/SDK. |
| D4 | PDF generated server-side (needs MinIO evidence + full-range data the browser never loads). |
| D5 | Insights are deterministic rule-based, not LLM-generated — reproducible, unit-testable. |
| D6 | Email endpoint is `REPORT_EMAIL_ENABLED=false` by default + recipient allowlist + max-recipient cap. |
| D7 | PDF is archived to MinIO on email send (best-effort) and a `report_deliveries` audit row is written for every attempt. |
| D8 | The PDF export endpoint returns the PDF inline in the HTTP response, not a MinIO redirect. |

Confirmed defaults (see plan §12): Gmail-style SMTP (587/STARTTLS), `Asia/Ho_Chi_Minh` report
timezone, English report text, free-form recipient entry (no managed subscriber list), evidence
thumbnails included (up to `REPORT_MAX_SNAPSHOTS`, Critical/High only), no scheduled digests, no
logo (`REPORT_LOGO_PATH` unset).

## Known limitations

- **No authentication.** Inherits the app-wide gap — see
  `backend/docs/internal_deployment.md`. Mitigated by the allowlist + `REPORT_EMAIL_ENABLED` kill
  switch, but this endpoint must not be exposed to the public internet as-is. The schedule config
  itself is also unauthenticated — anyone who can reach the API can change who gets the recurring
  report.
- **Rate limit and the schedule checker are both per-process.** `REPORT_EMAIL_RATE_LIMIT_PER_HOUR`
  and the `BackgroundScheduler` job are in-memory/in-process; neither survives (or coordinates
  across) multi-worker uvicorn — each worker would run its own 15-minute check.
- **One schedule, site-wide.** No per-user or per-zone recurring schedules — matches the rest of
  the app's no-multi-tenancy model, but is worth knowing if that assumption changes later.
- **`ANALYTICS_LIMIT` truncation** is surfaced as a report caveat, not fixed — the underlying
  aggregation is still Python-side (see `analytics_service.py`'s module docstring).
- **No email open/delivery tracking.** `report_deliveries.status` records the SMTP handoff result
  only; a `250 OK` from the relay is not proof of inbox delivery.
