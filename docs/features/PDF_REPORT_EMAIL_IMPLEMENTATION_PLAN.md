# Incident Analytics PDF Report + SMTP Email Delivery — Implementation Plan

> **Repo:** `jwydth/Smart_Factory_Safety_Monitoring`, branch `develop`
> **Purpose:** Add (a) on-demand **PDF export** of the Incident Analytics dashboard, and (b) an
> **SMTP route** that emails that PDF to end users as a professional safety report.
> **How to execute:** Work top-to-bottom. Do not start a phase until the previous phase's
> acceptance criteria pass. Run the gates in §11 after every phase.
> **Assume nothing is already built** — verified against `develop`: there is currently **zero**
> email, SMTP, or PDF-generation code anywhere in `backend/` or `frontend/`.

---

## 0. Ground truth — verified state of the codebase

These facts were confirmed by reading the `develop` branch. Do not re-derive them; do not
contradict them.

### 0.1 What already exists and MUST be reused (do not rebuild)

| Thing | Location | Why it matters here |
|---|---|---|
| `AnalyticsService` | `backend/app/services/analytics_service.py` | Already produces `get_summary()`, `get_trend()`, `get_compare()`. **This is the entire data source for the report.** |
| `UnifiedIncidentService` | `backend/app/services/incident_service.py` | Normalized `UnifiedIncident` dataclass (`category, type, severity, timestamp, camera_id, zone_id, zone_name, camera_label, snapshot_url`). Source for the incident detail table. |
| Analytics schemas | `backend/app/schemas/analytics.py` | `AnalyticsSummary`, `AnalyticsTrend`, `AnalyticsCompare`, `SeverityCounts`, `ZoneTotal`, `TypeCount`, `TrendPoint`, `ComparePoint`, `SeverityDelta`. |
| **`EvidenceStorage.upload_report()`** | `backend/app/storage/evidence_storage.py` L71 | **Already implemented and already accepts `.pdf`.** Pairs with `build_report_object_key()` → `reports/{YYYY}/{MM}/{uuid}.pdf`. Someone anticipated this feature. **Use it — do not write new MinIO code.** |
| `FactoryRepository.get_or_create_default_factory()` | `backend/app/repositories/factory_repository.py` | Gives the factory `name` / `location` for the report cover page. |
| Service error taxonomy | `backend/app/services/__init__.py` | `ServiceError`, `ServiceValidationError`, `ServiceNotFoundError`. Routers map `ServiceValidationError` → HTTP 422. Follow this. |
| Router registration | `backend/app/main.py` | Routers are imported in `app.routers` tuple then `app.include_router(...)`. |
| Test fixtures | `backend/tests/routers/conftest.py` | In-memory SQLite `session` fixture with `BigInteger`/`JSONB` → SQLite compilers. `backend/tests/routers/test_analytics_router.py` shows the `TestClient` + `dependency_overrides` pattern. Copy it. |

### 0.2 Constraints that will bite you if ignored

1. **Analytics aggregates in Python, not SQL.** `AnalyticsService._fetch()` pulls up to
   `settings.ANALYTICS_LIMIT` (default 20 000) rows and buckets them in memory.
   `UnifiedIncidentService.list_incidents()` logs a **truncation warning** when it hits that cap.
   → The report **must surface a visible data-completeness caveat** when truncation occurs,
   otherwise you are emailing management silently-undercounted numbers. See Phase 3, task 3.4.
2. **`zone_totals` deliberately ignores the `zone_id` filter.** In `get_summary()`,
   `zone_totals=self._zone_totals(all_incidents)` uses the *unfiltered* set, while
   `grand_total`, `severity_counts`, and `type_counts` use the *filtered* set. This is intentional
   (the dashboard needs the full zone list to render the picker). **The report must not mix
   them into one "total" or the numbers will not reconcile.** Label the zone-breakdown section
   "All zones (site-wide)" when a zone filter is active.
3. **All timestamps are UTC** (`_ensure_tz` forces `timezone.utc`). Users are in
   **Asia/Ho_Chi_Minh (UTC+7)**. A report showing UTC times will look wrong to the reader.
   → Add a `REPORT_TIMEZONE` setting and convert on render. See Phase 1, task 1.2.
4. **Import-boundary rule** (`docs/architecture/REFACTOR_NOTES.md`): `app/services/ppe/` and
   `app/services/zone_service.py` must never import each other. The new reporting package touches
   **neither** — it only reads `analytics_service` / `incident_service`. Keep it that way.
5. **No authentication exists anywhere in this app.** Adding an unauthenticated endpoint that
   sends email to a caller-supplied address turns the backend into an **open spam relay**.
   This is the single biggest risk in this feature. Mitigations are mandatory — see Phase 5.
6. **Vietnamese text will render as garbage in the PDF unless you register a Unicode font.**
   Verified: ReportLab's built-in Helvetica uses WinAnsi encoding, and the bundled `Vera.ttf`
   is **missing** `ế ạ Đ ộ ư` (checked glyph-by-glyph against the font's cmap). Zone names,
   factory names, and free-text notes in this project are likely Vietnamese. See Phase 2, task 2.1.
7. **Team runs Windows/PowerShell.** Anything requiring GTK, Cairo, or a headless browser
   (WeasyPrint, wkhtmltopdf, Playwright) is a setup-support nightmare here. Do not use them.

---

## 1. Locked decisions

**Locked (proceed with these — they are the plan):**

| # | Decision | Rationale |
|---|---|---|
| D1 | **PDF engine = ReportLab** (`reportlab==4.4.10`), pure-Python | No system dependencies, installs cleanly on Windows via pip, has built-in vector charts. **Verified working**: `SimpleDocTemplate`, `Table`, `VerticalBarChart`, `Pie`, `HorizontalLineChart`, `Legend` all import and build a valid PDF. |
| D2 | **Charts drawn with `reportlab.graphics`**, not matplotlib | Avoids a ~60 MB dependency and a headless-backend configuration step. Charts stay crisp vector, not raster. |
| D3 | **Email via stdlib `smtplib` + `email.message.EmailMessage`** | Zero new dependencies. Handles MIME multipart/alternative + attachment correctly. |
| D4 | **Generate PDF server-side, not client-side** | The report needs MinIO evidence images and full-range data the browser never loads (dashboard only fetches 30 feed rows). Also required for the email path, which has no browser. |
| D5 | **Insights are deterministic rule-based, not LLM-generated** | Reproducible, unit-testable, no API key, no latency, no hallucinated safety claims. |
| D6 | **Email endpoint is `REPORT_EMAIL_ENABLED=false` by default** + recipient allowlist + max-recipient cap | See §0.2 item 5. |
| D7 | **PDF is archived to MinIO on email send** via existing `upload_report()`, and a `report_deliveries` audit row is written | Regulatory/audit expectation for safety reporting; the storage method already exists. |
| D8 | **Export endpoint returns the PDF inline in the HTTP response** (not a MinIO redirect) | Simpler frontend, no presigned-URL expiry edge cases for the download path. |

**⚠️ CONFIRM with you — defaults are baked in, so the plan runs unblocked either way.** See §12.

---

## 2. Phase 1 — Dependencies & configuration

### 1.1 Add dependencies

**File:** `backend/requirements.txt` — append:

```
reportlab==4.4.10
tzdata==2026.1
```

> **Why `tzdata`:** Python's `zoneinfo` reads the OS tz database. **Windows has no such database**,
> so `ZoneInfo("Asia/Ho_Chi_Minh")` raises `ZoneInfoNotFoundError` on the team's dev machines
> without this package. This is a guaranteed break otherwise.

No `smtplib` install needed — stdlib.

### 1.2 Add settings

**File:** `backend/app/core/config.py` — add to the `Settings` class (keep the existing
comment-heavy style of that file):

```python
    # ---- Reporting / PDF export ----
    # Display timezone for rendered report timestamps. All storage is UTC
    # (see UnifiedIncidentService._ensure_tz); this only affects presentation.
    REPORT_TIMEZONE: str = "Asia/Ho_Chi_Minh"
    REPORT_COMPANY_NAME: str = "De Heus LLC"
    REPORT_LOGO_PATH: str | None = None          # optional PNG/JPG, absolute or backend-relative
    REPORT_MAX_INCIDENT_ROWS: int = 25           # rows in the detail table
    REPORT_MAX_SNAPSHOTS: int = 6                # evidence thumbnails in the appendix
    REPORT_SNAPSHOT_TIMEOUT_SECONDS: float = 5.0 # per-image fetch budget
    REPORT_ARCHIVE_TO_MINIO: bool = True

    # ---- SMTP ----
    REPORT_EMAIL_ENABLED: bool = False           # master switch; see security note in the plan
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_USE_STARTTLS: bool = True               # port 587
    SMTP_USE_SSL: bool = False                   # port 465; mutually exclusive with STARTTLS
    SMTP_TIMEOUT_SECONDS: float = 20.0
    SMTP_FROM_EMAIL: str | None = None
    SMTP_FROM_NAME: str = "Smart Factory Safety Monitoring"
    # Empty list = allow any recipient. NON-EMPTY IS STRONGLY RECOMMENDED: without
    # auth on this API, an open recipient field makes /reports/incidents/email a
    # spam relay. Exact-match emails and/or "@domain.com" suffixes are accepted.
    REPORT_RECIPIENT_ALLOWLIST: list[str] = []
    REPORT_MAX_RECIPIENTS: int = 10
```

Add a `@model_validator(mode="after")` guard (the class already has one — add a second, or extend):

```python
    @model_validator(mode="after")
    def _validate_smtp(self) -> "Settings":
        if self.SMTP_USE_SSL and self.SMTP_USE_STARTTLS:
            raise ValueError("SMTP_USE_SSL and SMTP_USE_STARTTLS are mutually exclusive.")
        return self
```

### 1.3 Document the env vars

**File:** `backend/.env.example` — append (values are placeholders, never real secrets):

```dotenv
# Reporting / PDF export
REPORT_TIMEZONE=Asia/Ho_Chi_Minh
REPORT_COMPANY_NAME=De Heus LLC
REPORT_ARCHIVE_TO_MINIO=true

# SMTP email delivery (disabled until configured)
REPORT_EMAIL_ENABLED=false
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_USE_STARTTLS=true
SMTP_USE_SSL=false
SMTP_FROM_EMAIL=safety-reports@example.com
SMTP_FROM_NAME=Smart Factory Safety Monitoring
REPORT_RECIPIENT_ALLOWLIST=["@deheus.com"]
REPORT_MAX_RECIPIENTS=10
```

Confirm `.gitignore` already excludes `backend/.env` (**it does** — verify, do not skip).

### ✅ Phase 1 acceptance
- `pip install -r requirements.txt` succeeds on Windows.
- `python -c "from app.core.config import settings; print(settings.REPORT_TIMEZONE)"` prints the value.
- `python -c "from zoneinfo import ZoneInfo; ZoneInfo('Asia/Ho_Chi_Minh')"` does not raise.
- Setting both `SMTP_USE_SSL=true` and `SMTP_USE_STARTTLS=true` raises at startup.

---

## 3. Phase 2 — Report data assembly + insight engine

Create a new package: `backend/app/services/reporting/`. It reads **only**
`analytics_service`, `incident_service`, `factory_repository`, and `evidence_storage` —
never `services/ppe/` or `zone_service.py` (§0.2 item 4).

```
backend/app/services/reporting/
├── __init__.py           # re-exports; error types
├── fonts/                # DejaVuSans.ttf, DejaVuSans-Bold.ttf  (committed to the repo)
├── report_data.py        # ReportData assembly (this phase)
├── insights.py           # deterministic insight rules (this phase)
├── pdf_renderer.py       # ReportLab rendering (Phase 3)
├── email_sender.py       # smtplib transport (Phase 4)
└── report_service.py     # orchestrator (Phase 4)
```

### 2.1 Vendor the Unicode font — **do this first, it is a hard blocker**

Download **DejaVu Sans** (Bitstream Vera–derived, permissive license, verified to contain
`ế ạ Đ ộ ư`) and commit:

```
backend/app/services/reporting/fonts/DejaVuSans.ttf
backend/app/services/reporting/fonts/DejaVuSans-Bold.ttf
```

Source: https://github.com/dejavu-fonts/dejavu-fonts/releases (`dejavu-fonts-ttf-2.37.zip` →
`ttf/DejaVuSans.ttf`, `ttf/DejaVuSans-Bold.ttf`). Include the license file
`fonts/LICENSE-DejaVu.txt` alongside them.

> Do **not** rely on ReportLab's bundled `Vera.ttf`. Verified: its cmap is missing every
> Vietnamese diacritic tested. Do **not** rely on `C:\Windows\Fonts\arial.ttf` either —
> that path does not exist in the Docker image (`backend/Dockerfile`).

### 2.2 `report_data.py` — the assembled payload

```python
@dataclass(frozen=True)
class ReportIncidentRow:
    timestamp_local: str      # already formatted in REPORT_TIMEZONE
    category: str             # "PPE" | "Zone" | "Behavior"
    type: str
    severity: str
    zone_name: str
    camera_label: str
    snapshot_url: str | None

@dataclass(frozen=True)
class ReportData:
    company_name: str
    factory_name: str
    factory_location: str | None
    range_label: str                  # "Last 7 days (22 Jul – 29 Jul 2026)"
    range_param: str                  # "24H" | "7D" | "30D"
    zone_scope_label: str             # "All zones" | "Production Floor"
    generated_at_local: str
    timezone_label: str               # "UTC+07:00 (Asia/Ho_Chi_Minh)"
    summary: AnalyticsSummary
    trend: AnalyticsTrend
    compare: AnalyticsCompare
    top_incidents: list[ReportIncidentRow]
    insights: list[str]
    data_caveats: list[str]           # truncation / empty-data notices
```

**`ReportDataBuilder`** class with constructor-injected `AnalyticsService`,
`UnifiedIncidentService`, `FactoryRepository` (mirror `AnalyticsService.__init__`'s
`Annotated[..., Depends(...)]` style), and a module-level
`get_report_data_builder(session, storage)` factory that mirrors
`get_analytics_service()` exactly.

`build(range_: str, zone_id: int | None) -> ReportData`:
1. `summary = analytics.get_summary(range_=range_, zone_id=zone_id)`
2. `trend = analytics.get_trend(range_=range_, zone_id=zone_id)`
3. `compare = analytics.get_compare(mode="week" if range_ in ("24H","7D") else "month", zone_id=zone_id)`
4. `incidents = incident_service.list_incidents(zone_id=zone_id, date_from=..., date_to=..., limit=settings.ANALYTICS_LIMIT)`
   — reuse `analytics_service._range_to_dates(range_)`. **Promote that private helper to a public
   `range_to_dates()` in `analytics_service.py`** rather than duplicating the date math; update its
   two internal call sites.
5. Sort `incidents` by `(severity_rank, timestamp desc)` where rank is
   `Critical=0, High=1, Medium=2, Low=3`; take the first `REPORT_MAX_INCIDENT_ROWS`.
6. `insights = build_insights(summary, trend, compare)`
7. `data_caveats = build_caveats(...)`

**Timezone helper** (put in `report_data.py`, unit-test it):

```python
def to_local(ts: datetime, tz_name: str) -> datetime:
    return ts.astimezone(ZoneInfo(tz_name))
```
Guard `ZoneInfoNotFoundError` → fall back to UTC and append a caveat rather than 500-ing.

### 2.3 `insights.py` — the "key insights" engine

Pure functions, no I/O, fully unit-testable. `build_insights(summary, trend, compare) -> list[str]`
runs these rules in order and returns only the ones that fire (cap at 6 bullets):

| Rule | Fires when | Example output |
|---|---|---|
| **Trend direction** | always | "Incidents are **up 34.2%** vs the previous 7 days (128 vs 95)." |
| **Zero-baseline trend** | `compare.prior_total == 0 and current_total > 0` | "First reporting period with recorded incidents — no prior baseline to compare against." |
| **Hotspot zone** | top `zone_totals` entry ≥ 30 % of grand total | "**Production Floor** accounts for 41% of all incidents (52 of 128) — the highest of 6 zones." |
| **Dominant type** | top `type_counts` entry ≥ 25 % of grand total | "**Missing Helmet** is the most frequent incident type (38 occurrences, 30% of total)." |
| **Critical severity** | `severity_counts.Critical > 0` | "**7 Critical** incidents require immediate review." |
| **Severity shift** | any `SeverityDelta` where `current > prior` and `prior > 0` and delta ≥ 50 % | "High-severity incidents more than doubled (8 → 19)." |
| **Peak bucket** | a `trend.points` bucket ≥ 2× the mean | "Peak activity on **Jul 26** (30 incidents, 2.3× the daily average)." |
| **Quiet zones** | zones with `total == 0` | "3 zones recorded no incidents: Loading Bay, QA Lab, Office Wing." |
| **Camera coverage** | `active_cameras < total_cameras` | "Incidents were recorded on 4 of 9 active cameras." |
| **All clear** | `grand_total == 0` | "No incidents recorded in this period across any monitored zone." |

`build_caveats(...) -> list[str]` fires:
- `grand_total >= settings.ANALYTICS_LIMIT` → "⚠ Incident volume reached the aggregation cap
  (20,000 rows). Figures in this report may undercount the true total. Raise `ANALYTICS_LIMIT`."
- `summary.total_cameras == 0` → "No active cameras are registered; zone attribution may be incomplete."
- `zone_id is not None` → "Zone breakdown below is site-wide; headline figures are filtered to
  {zone name}." (this is §0.2 item 2 made visible to the reader)
- timezone fallback → "Timestamps shown in UTC; configured timezone was unavailable."

**Write insight strings with a lightweight bold marker** (`**...**`) and convert to
`<b>...</b>` at render time — ReportLab `Paragraph` accepts a mini-HTML subset.

### ✅ Phase 2 acceptance
- New test file `backend/tests/services/test_report_insights.py`: ≥ 10 tests, one per rule,
  including the zero-incident and zero-prior-baseline edge cases. All pass.
- New test file `backend/tests/services/test_report_data.py`: builds `ReportData` off the seeded
  SQLite fixture (copy `_seed()` from `tests/routers/test_analytics_router.py`), asserts
  local-time conversion and severity ordering of `top_incidents`.
- `python -m ruff check app tests` clean.

---

## 4. Phase 3 — PDF renderer

**File:** `backend/app/services/reporting/pdf_renderer.py`

Public surface — keep it this small:

```python
def render_incident_report(data: ReportData, snapshots: dict[str, bytes] | None = None) -> bytes
```

Returns raw PDF bytes. Takes pre-fetched snapshot bytes so the renderer stays I/O-free and
unit-testable (the caller does the MinIO fetching — see 3.5).

### 3.1 Font + style registration (module level, run once)

```python
_FONTS_DIR = Path(__file__).parent / "fonts"

def _register_fonts() -> tuple[str, str]:
    """Returns (regular_name, bold_name). Falls back to Helvetica if the
    vendored TTFs are missing, so the report still renders — Latin only."""
    try:
        pdfmetrics.registerFont(TTFont("DejaVuSans", _FONTS_DIR / "DejaVuSans.ttf"))
        pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", _FONTS_DIR / "DejaVuSans-Bold.ttf"))
        registerFontFamily("DejaVuSans", normal="DejaVuSans", bold="DejaVuSans-Bold")
        return "DejaVuSans", "DejaVuSans-Bold"
    except Exception:
        logger.warning("Vendored report fonts unavailable; falling back to Helvetica "
                       "(non-Latin characters will not render).")
        return "Helvetica", "Helvetica-Bold"
```

`registerFontFamily` is required or `<b>` inside `Paragraph` silently falls back to Helvetica
and you get mixed fonts on the same line.

### 3.2 Design tokens — match the dashboard, don't invent new colors

Lift these directly from `frontend/src/components/analytics/analytics-dashboard.tsx` so the PDF
and the screen agree:

```python
SEVERITY_COLORS = {"Critical": "#ef4444", "High": "#f97316",
                   "Medium": "#f59e0b", "Low": "#10b981"}
ZONE_PALETTE = ["#0ea5e9", "#8b5cf6", "#f59e0b", "#f43f5e",
                "#14b8a6", "#eab308", "#6366f1", "#ec4899"]
UNASSIGNED_COLOR = "#94a3b8"
INK = "#0f172a"        # slate-950, the dashboard header color
ACCENT = "#d9f99d"     # lime-200, the dashboard accent
MUTED = "#64748b"
RULE = "#e2e8f0"
```

### 3.3 Document structure (A4 portrait, `SimpleDocTemplate`, 18 mm margins)

| § | Section | Flowables |
|---|---|---|
| 1 | **Header band** | Optional logo (`REPORT_LOGO_PATH`) + company name + "Incident Analytics Report". Dark `INK` bar with `ACCENT` text, matching the dashboard header. |
| 2 | **Report metadata** | 2-column table: Factory, Location, Period, Zone scope, Generated at, Timezone. |
| 3 | **Data caveats** | Only rendered if `data_caveats` is non-empty. Amber-tinted box. **Never silently omit.** |
| 4 | **KPI band** | 4-up `Table`: Total Incidents (+ delta % with ▲/▼), Critical, High, Open Incidents. Second row: Active Cameras `n/m`, Zones Affected. |
| 5 | **Key Insights** | Bulleted `Paragraph` list from `data.insights`. This is the section a manager actually reads — put it above the charts. |
| 6 | **Severity distribution** | `Pie` + `Legend` with counts and percentages. |
| 7 | **Incidents by zone** | `VerticalBarChart`, one bar per zone, colored from `ZONE_PALETTE`, value labels on top. Include the "site-wide" note when a zone filter is active. |
| 8 | **Trend over time** | `HorizontalLineChart` over `trend.points`; x-labels from `point.date` (already formatted `"14:00"` or `"Jul 26"` by `_bucket_label`). Rotate labels 45° when `len(points) > 12` (the 30D case). |
| 9 | **Current vs prior period** | Grouped `VerticalBarChart`, two series from `compare.points`. For 30-day mode this is 30 category pairs — **downsample to weekly buckets** when `len(points) > 14`, or the chart is unreadable. |
| 10 | **Incident types** | Table: Category / Type / Count / % of total, sorted desc, top 10. |
| 11 | **Recent priority incidents** | Table of `top_incidents`: Time (local) / Severity (color-coded cell) / Category / Type / Zone / Camera. `repeatRows=1` so the header repeats across page breaks. |
| 12 | **Evidence appendix** | Only if snapshots were fetched. Up to `REPORT_MAX_SNAPSHOTS` thumbnails, 2-up grid, each captioned with time + type + zone. |
| 13 | **Footer** (`onPage` callback) | Left: "{company} — Confidential · Generated {timestamp}". Right: "Page N of M". Use `canvas.saveState()/restoreState()`. |

For "Page N of M", use the standard two-pass `doc.multiBuild` + `NumberedCanvas` pattern, or
simply omit the total and render "Page N" — decide once, don't half-do it.

### 3.4 Empty-state handling — do not skip this

If `summary.grand_total == 0`, the charts must not be attempted (`Pie` with all-zero data
raises `ZeroDivisionError` in ReportLab). Render §1–5 plus a centered
"No incidents recorded in this period." panel and stop. **Add an explicit test for this**;
a zero-incident day is the *most likely* first real-world run.

Also guard: `trend.points` all-zero → skip the line chart; `compare.prior_total == 0` → the
delta label shows "n/a" not "∞%" (note `AnalyticsService` already returns `delta_pct=0.0` here,
which reads as "no change" and is misleading — override the label in the renderer).

### 3.5 Snapshot fetching (separate, in `report_service.py`)

```python
def fetch_snapshots(rows: list[ReportIncidentRow], limit: int) -> dict[str, bytes]
```
- Take the first `limit` rows that have a non-null `snapshot_url` and severity in
  `{"Critical", "High"}`.
- Local paths (`/snapshots/...`) → read from `SNAPSHOT_DIR` on disk.
- MinIO presigned URLs → `httpx.get(url, timeout=REPORT_SNAPSHOT_TIMEOUT_SECONDS)`.
  (`httpx` arrives transitively with FastAPI/Starlette; if `pip show httpx` fails, add it
  explicitly to `requirements.txt`.)
- **Every failure is swallowed and logged at WARNING.** A missing thumbnail must never fail
  the report. Wrap the whole function so one bad object key cannot 500 the endpoint.
- Downscale to ≤ 800 px wide with Pillow (already a dependency) before embedding, or a
  25-incident report becomes a 40 MB email attachment that the SMTP server rejects.

### ✅ Phase 3 acceptance
- `backend/tests/services/test_pdf_renderer.py`:
  - output starts with `b"%PDF-"` and is > 5 KB;
  - renders with a Vietnamese zone name (`"Khu vực sản xuất"`) without raising;
  - **zero-incident `ReportData` renders successfully** (regression guard for 3.4);
  - a `ReportData` with 30 trend points and 30 compare points renders (the 30D path);
  - renders with `snapshots=None` and with one corrupt image byte-string.
- Manually open the generated PDF and eyeball it. Automated tests cannot catch overlapping
  chart labels. **Budget one iteration pass for visual polish.**

---

## 5. Phase 4 — SMTP sender + orchestrator

### 4.1 `email_sender.py`

Error type in `reporting/__init__.py`:

```python
class ReportEmailError(ServiceError):
    """SMTP transport or configuration failure."""
```

```python
@dataclass(frozen=True)
class EmailAttachment:
    filename: str
    content: bytes
    mime_type: str = "application/pdf"

class SmtpEmailSender:
    def __init__(self, settings_obj=settings) -> None: ...

    def validate_configuration(self) -> None:
        """Raise ReportEmailError if SMTP_HOST / SMTP_FROM_EMAIL are unset or
        REPORT_EMAIL_ENABLED is False. Called before doing any work."""

    def verify_connection(self) -> dict[str, str]:
        """Connect, STARTTLS/SSL, login, NOOP, quit. For GET /health/smtp."""

    def send(self, *, recipients: list[str], subject: str,
             html_body: str, text_body: str,
             attachments: list[EmailAttachment]) -> None: ...
```

**Message construction** — use `EmailMessage`, not the legacy `MIMEMultipart` API:

```python
msg = EmailMessage()
msg["Subject"] = subject
msg["From"] = formataddr((settings.SMTP_FROM_NAME, settings.SMTP_FROM_EMAIL))
msg["To"] = ", ".join(recipients)
msg["Date"] = formatdate(localtime=True)
msg["Message-ID"] = make_msgid(domain=sender_domain)
msg.set_content(text_body)                       # plain-text fallback
msg.add_alternative(html_body, subtype="html")   # rich version
for att in attachments:
    maintype, subtype = att.mime_type.split("/", 1)
    msg.add_attachment(att.content, maintype=maintype,
                       subtype=subtype, filename=att.filename)
```

> **Ordering gotcha:** `add_attachment()` **after** `add_alternative()` correctly promotes the
> message to `multipart/mixed` wrapping the `multipart/alternative`. Doing it in the other
> order produces a message where Gmail hides the body. Do not reorder these lines.

**Transport:**

```python
if settings.SMTP_USE_SSL:
    server = smtplib.SMTP_SSL(host, port, timeout=t, context=ssl.create_default_context())
else:
    server = smtplib.SMTP(host, port, timeout=t)
    if settings.SMTP_USE_STARTTLS:
        server.starttls(context=ssl.create_default_context())
if settings.SMTP_USERNAME:
    server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
server.send_message(msg)
```
Use `with` / `try/finally` so `quit()` always runs.

**Exception mapping — map to safe messages, never leak credentials:**

| Caught | Raise | Router → HTTP |
|---|---|---|
| `smtplib.SMTPAuthenticationError` | `ReportEmailError("SMTP authentication failed. Check SMTP_USERNAME / SMTP_PASSWORD.")` | 502 |
| `smtplib.SMTPRecipientsRefused` | `ReportEmailError("All recipients were rejected by the mail server.")` | 502 |
| `smtplib.SMTPSenderRefused` | `ReportEmailError("The mail server rejected the sender address.")` | 502 |
| `socket.timeout` / `TimeoutError` | `ReportEmailError("SMTP server timed out.")` | 504 |
| `ssl.SSLError` | `ReportEmailError("TLS negotiation with the SMTP server failed.")` | 502 |
| `OSError` / `ConnectionRefusedError` | `ReportEmailError("Could not connect to the SMTP server.")` | 502 |

**Logging rule:** log host, port, recipient count, and outcome. **Never log `SMTP_PASSWORD`,
and never `logger.exception()` on `SMTPAuthenticationError`** — its `str()` can echo the
attempted credential in some server responses.

### 4.2 Recipient validation (`report_service.py`)

```python
def validate_recipients(raw: list[str]) -> list[str]:
```
1. Strip, lowercase, de-duplicate.
2. Reject empty list → `ServiceValidationError("At least one recipient is required.")`
3. Reject `len > REPORT_MAX_RECIPIENTS` → `ServiceValidationError`
4. Validate each against a conservative regex; reject any containing `\r` or `\n`
   (**header-injection guard — mandatory**, a newline in a recipient lets an attacker inject
   arbitrary SMTP headers such as `Bcc:`).
5. If `REPORT_RECIPIENT_ALLOWLIST` is non-empty, each address must exact-match an entry or
   end with an entry beginning `@`. Otherwise → `ServiceValidationError("Recipient {x} is not
   on the allowed list.")`

### 4.3 Email body templates

Keep them in `email_sender.py` as module constants with `str.format` placeholders — no Jinja2.

**Subject default:**
`"[Safety Report] {factory_name} — {range_label} — {total} incidents ({critical} critical)"`

**HTML body:** a compact card mirroring the KPI band — total / critical / high / delta, the
top 3 insight bullets, and a line stating the full report is attached. Inline CSS only
(email clients strip `<style>` blocks). Max width 600 px. **Include the plain-text fallback**
or the message scores badly on spam filters.

### 4.4 `report_service.py` — the orchestrator

```python
class ReportService:
    def build_pdf(self, *, range_: str, zone_id: int | None,
                  include_snapshots: bool) -> tuple[bytes, str]:
        """Returns (pdf_bytes, filename). Filename:
        safety-report_{factory-slug}_{range}_{YYYYMMDD-HHMM}.pdf"""

    def email_report(self, *, range_: str, zone_id: int | None,
                     recipients: list[str], subject: str | None,
                     message: str | None, include_snapshots: bool) -> ReportDeliveryResult:
```

`email_report` sequence — **order matters**:
1. `sender.validate_configuration()` — fail fast before generating a PDF nobody can send.
2. `validate_recipients(recipients)`
3. `build_pdf(...)`
4. If `REPORT_ARCHIVE_TO_MINIO`: write PDF to a `tempfile.NamedTemporaryFile(suffix=".pdf",
   delete=False)`, call `storage.upload_report(path)`, capture `object_key`, then
   `finally: os.unlink(path)`. **Wrap in try/except `StorageError` and continue on failure** —
   archiving must never block delivery.
5. `sender.send(...)`
6. Write the `report_deliveries` audit row (Phase 4.5).
7. Return `ReportDeliveryResult(recipients, filename, size_bytes, object_key, sent_at)`.

### 4.5 Audit model (D7)

**File:** `backend/app/models/report_delivery.py` — follow `behavior_incident.py`'s style exactly
(`BigInteger` PK, `DateTime(timezone=True)`, `server_default=func.now()`, `String(n)` columns):

```python
class ReportDelivery(SQLModel, table=True):
    __tablename__ = "report_deliveries"
    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    report_type: str          # "incident_analytics"
    range_param: str          # "24H" | "7D" | "30D"
    zone_id: int | None       # FK cameras-free: plain nullable BigInteger, no FK constraint
    recipients: str           # comma-joined, String(1000)
    subject: str              # String(500)
    filename: str             # String(255)
    object_key: str | None    # MinIO key when archived
    size_bytes: int
    status: str               # "SENT" | "FAILED"
    error_message: str | None # Text
    created_at: datetime
```

Register in `backend/app/models/__init__.py` (import **and** `__all__` — both, the file lists
each model twice). Add `ReportDeliveryRepository` in `backend/app/repositories/` following
`behavior_incident_repository.py`'s constructor-takes-`Session` shape.

Run `python -m app.db.init_db` to create the table (this project uses
`SQLModel.metadata.create_all`, **there is no Alembic** — do not add migrations).

### ✅ Phase 4 acceptance
- `backend/tests/services/test_email_sender.py`: monkeypatch `smtplib.SMTP`; assert the built
  message has `multipart/mixed` → `multipart/alternative` structure, a `.pdf` attachment with
  the right filename, and both `text/plain` and `text/html` parts. Assert each exception in
  the mapping table produces the right `ReportEmailError` message.
- `backend/tests/services/test_report_service.py`: recipient validation — allowlist pass/fail,
  over-cap rejection, **`"a@b.com\nBcc: evil@x.com"` rejected**, duplicates collapsed.
- No test performs a real network connection.

---

## 6. Phase 5 — API routes

**File:** `backend/app/routers/reports.py`. Register in `backend/app/main.py`
(add `reports` to the `from app.routers import (...)` tuple **and** `app.include_router(reports.router)`).

### 5.1 Schemas — `backend/app/schemas/report.py`

```python
class ReportEmailRequest(BaseModel):
    recipients: list[EmailStr]                 # pydantic validates shape; service validates policy
    range: AnalyticsRange = "7D"
    zone_id: int | None = None
    subject: str | None = Field(default=None, max_length=200)
    message: str | None = Field(default=None, max_length=2000)
    include_snapshots: bool = True

class ReportEmailResponse(BaseModel):
    status: Literal["sent"]
    recipients: list[str]
    filename: str
    size_bytes: int
    object_key: str | None
    sent_at: str

class ReportPreview(BaseModel):
    range: AnalyticsRange
    zone_scope_label: str
    generated_at_local: str
    grand_total: int
    severity_counts: SeverityCounts
    insights: list[str]
    data_caveats: list[str]
```

> `EmailStr` needs `email-validator`. Check `pip show email-validator` — FastAPI 0.111 usually
> pulls it in. **If it is absent, add `email-validator==2.1.1` to `requirements.txt`**, or use
> `list[str]` and rely solely on service-layer regex validation. Verify before writing the schema.

### 5.2 Endpoints

| Method | Path | Notes |
|---|---|---|
| `GET` | `/reports/incidents/preview` | Query: `range`, `zone_id`. Returns `ReportPreview`. Lets the frontend show the insights before download/send, and makes the whole feature testable without parsing a PDF. |
| `GET` | `/reports/incidents.pdf` | Query: `range`, `zone_id`, `include_snapshots`. Returns `Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{fn}"'})`. |
| `POST` | `/reports/incidents/email` | Body: `ReportEmailRequest` → `ReportEmailResponse`. **Synchronous** (returns 200 after the send completes) — see 5.3. |
| `GET` | `/health/smtp` | Add to `backend/app/routers/testing.py` next to `/health/db` and `/health/storage`. Returns `{"smtp": "connected", "host": ..., "port": ...}` or 503. |

**Filename quoting:** the factory name may contain non-ASCII (Vietnamese). A raw non-ASCII
`Content-Disposition` header raises `UnicodeEncodeError` in Starlette. Slugify to ASCII
(`unicodedata.normalize("NFKD", s).encode("ascii", "ignore")`) and additionally emit
`filename*=UTF-8''{quoted}` per RFC 5987.

### 5.3 Sync vs background — decide deliberately

**Do it synchronously.** Rationale: PDF generation is ~0.5–2 s, SMTP is ~1–3 s; a 5 s request is
acceptable, and the user gets a **real success/failure result** instead of a fire-and-forget 202
that silently fails. Run the blocking work off the event loop:

```python
from starlette.concurrency import run_in_threadpool
result = await run_in_threadpool(service.email_report, ...)
```
**This is mandatory** — `smtplib` and ReportLab are blocking, and calling them directly in an
`async def` handler stalls every open camera WebSocket in this app (`streaming.py` holds
long-lived connections). This is the highest-impact performance mistake available here.

### 5.4 Error mapping in the router

```python
except ServiceValidationError as exc:  raise HTTPException(422, str(exc))
except ReportEmailError as exc:        raise HTTPException(502, str(exc))   # 504 for timeout subtype
except StorageError as exc:            raise HTTPException(503, str(exc))
```
Matches the existing convention in `analytics.py` and `testing.py`.

### 5.5 Abuse controls — non-negotiable (§0.2 item 5)

1. `REPORT_EMAIL_ENABLED=false` by default → endpoint returns **503** with
   "Email delivery is not enabled on this server."
2. Recipient allowlist + `REPORT_MAX_RECIPIENTS` (Phase 4.2).
3. **In-process rate limit**: module-level `deque` of send timestamps; reject with **429** past
   N sends per hour (`REPORT_EMAIL_RATE_LIMIT_PER_HOUR: int = 20` — add to config). Adequate for
   a single-process internal deployment; note in a code comment that it does not survive
   multi-worker uvicorn.
4. `message` field is user text that lands in an HTML email → **HTML-escape it**
   (`html.escape`) before interpolation. Otherwise this is stored XSS in the recipient's inbox.
5. Add a line to `backend/docs/internal_deployment.md`: this endpoint must not be exposed to
   the public internet until authentication exists.

### ✅ Phase 5 acceptance
- `backend/tests/routers/test_reports_router.py` (copy the `_client()` + `dependency_overrides`
  scaffold from `test_analytics_router.py`):
  - `GET /reports/incidents.pdf` → 200, `content-type: application/pdf`, body starts `%PDF-`,
    `Content-Disposition` present;
  - `GET /reports/incidents/preview` → 200 with insight strings;
  - `POST /reports/incidents/email` with `REPORT_EMAIL_ENABLED=false` → 503;
  - with it enabled and a stubbed sender → 200 and the sender called once with the expected
    recipients;
  - recipient not on allowlist → 422;
  - 11 recipients with cap 10 → 422;
  - stubbed sender raising `ReportEmailError` → 502.
- `curl -o out.pdf "http://127.0.0.1:8000/reports/incidents.pdf?range=7D"` opens in a PDF viewer.
- Update `backend/docs/api_routes.md` with all four endpoints in that file's existing
  Method/Path/Purpose/Request body/Storage used format.

---

## 7. Phase 6 — Frontend

### 6.1 Types — `frontend/src/types/report.ts`

Mirror the Pydantic schemas: `ReportPreview`, `ReportEmailRequest`, `ReportEmailResponse`.

### 6.2 API client — `frontend/src/lib/ppe-api.ts`

Append, matching the existing `readError()` / `API_URL` conventions:

```ts
export async function getReportPreview(range, zoneId): Promise<ReportPreview>

export async function downloadIncidentReportPdf(
  range: AnalyticsRangeParam, zoneId: number | null, includeSnapshots = true,
): Promise<void> {
  const params = new URLSearchParams({ range, include_snapshots: String(includeSnapshots) });
  if (zoneId != null) params.set("zone_id", String(zoneId));
  const res = await fetch(`${API_URL}/reports/incidents.pdf?${params}`);
  if (!res.ok) throw await readError(res, "Could not generate the PDF report");
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(cd)?.[1] ?? "safety-report.pdf";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

export async function emailIncidentReport(payload: ReportEmailRequest): Promise<ReportEmailResponse>
```

> `readError()` calls `res.json()`. For the PDF endpoint an error response *is* JSON (FastAPI's
> `{"detail": ...}`), so this works — but only because we check `res.ok` **before** `res.blob()`.
> Keep that order.

### 6.3 UI — `frontend/src/components/analytics/report-export-dialog.tsx` (new)

A modal following the visual language of `incident-detail-modal.tsx` (read it first; reuse its
overlay/panel/close-button structure rather than inventing a new modal).

Contents:
- Read-only summary of what will be exported: current `timeRange` + `selectedZone` name.
- Insight preview (from `getReportPreview`) so the user sees the content before sending.
- `include_snapshots` toggle.
- **Download PDF** button → `downloadIncidentReportPdf`, with a spinner (generation is seconds,
  not milliseconds — an un-spinnered button will get double-clicked).
- **Email report** section: recipients input (comma/enter-separated chips), optional subject,
  optional message textarea, **Send** button.
- States: idle / generating / sending / success (green, echoes recipients) / error (red, shows
  `err.message` from the backend — the backend messages are already user-safe by design).
- Disable Send while in flight; re-enable on settle.

### 6.4 Wire the entry point — `frontend/src/components/analytics/analytics-dashboard.tsx`

In the toolbar row (`<div className="mb-4 flex flex-wrap items-center justify-between gap-3">`,
around L393), add a button **to the left of** the existing `24H / 7D / 30D` group:

```tsx
<button
  type="button"
  onClick={() => setExportOpen(true)}
  className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50"
>
  <FileDown className="size-4" aria-hidden="true" />
  Export report
</button>
```
Import `FileDown` from `lucide-react` (already a dependency; the file already imports from it).
Add `const [exportOpen, setExportOpen] = useState(false);` alongside the existing state, and
render `<ReportExportDialog open={exportOpen} onClose={...} range={timeRange} zoneId={selectedZone} />`.

The dialog must receive the **current** `timeRange` and `selectedZone` so the export matches what
the user is looking at. This is the whole point of the feature — do not default it to `7D`/all-zones.

### ✅ Phase 6 acceptance
- `npm run build` clean, `npx eslint .` clean.
- Clicking Export report with the dashboard on `30D` + a zone selected downloads a PDF whose
  metadata page shows that same range and zone.
- Email flow shows a success state and the message arrives with the PDF attached.

---

## 8. Phase 7 — Documentation

1. `backend/docs/api_routes.md` — the four new endpoints (see 5.5).
2. `README.md` — a short "Email Reports" subsection under **Backend Configuration** covering
   the SMTP env vars and the Gmail app-password caveat (Gmail rejects account passwords;
   `SMTP_PASSWORD` must be a 16-character App Password with 2FA enabled).
3. `backend/docs/internal_deployment.md` — the "do not expose without auth" warning.
4. `docs/features/PDF_REPORT_EMAIL.md` — a short feature note in the style of
   `INCIDENT_ANALYTICS_IMPLEMENTATION_PLAN.md`: what was built, the locked decisions from §1,
   and the known limitations from §10.

---

## 9. File manifest

**New — backend (13):**
```
backend/app/services/reporting/__init__.py
backend/app/services/reporting/report_data.py
backend/app/services/reporting/insights.py
backend/app/services/reporting/pdf_renderer.py
backend/app/services/reporting/email_sender.py
backend/app/services/reporting/report_service.py
backend/app/services/reporting/fonts/DejaVuSans.ttf
backend/app/services/reporting/fonts/DejaVuSans-Bold.ttf
backend/app/services/reporting/fonts/LICENSE-DejaVu.txt
backend/app/schemas/report.py
backend/app/routers/reports.py
backend/app/models/report_delivery.py
backend/app/repositories/report_delivery_repository.py
```

**New — tests (6):**
```
backend/tests/services/test_report_insights.py
backend/tests/services/test_report_data.py
backend/tests/services/test_pdf_renderer.py
backend/tests/services/test_email_sender.py
backend/tests/services/test_report_service.py
backend/tests/routers/test_reports_router.py
```

**New — frontend (2):**
```
frontend/src/types/report.ts
frontend/src/components/analytics/report-export-dialog.tsx
```

**Modified (10):**
```
backend/requirements.txt                                    + reportlab, tzdata (± email-validator, httpx)
backend/app/core/config.py                                  + reporting/SMTP settings + validator
backend/app/main.py                                         + reports router
backend/app/models/__init__.py                              + ReportDelivery (import AND __all__)
backend/app/routers/testing.py                              + GET /health/smtp
backend/app/services/analytics_service.py                   _range_to_dates → public range_to_dates
backend/.env.example                                        + documented env vars
backend/docs/api_routes.md                                  + 4 endpoints
README.md                                                   + Email Reports section
frontend/src/lib/ppe-api.ts                                 + 3 functions
frontend/src/components/analytics/analytics-dashboard.tsx   + Export button + dialog wiring
```

---

## 10. Known limitations to write down, not solve

State these in `docs/features/PDF_REPORT_EMAIL.md` so they are decisions, not surprises:

- **No scheduled/recurring reports.** On-demand only. A daily digest needs a scheduler
  (APScheduler or an external cron hitting the endpoint) — out of scope, easy follow-up.
- **No authentication.** Inherits the app-wide gap. Mitigated by the allowlist + kill switch.
- **Rate limit is per-process.** Breaks under multi-worker uvicorn.
- **`ANALYTICS_LIMIT` truncation** is surfaced as a caveat, not fixed. The real fix is SQL-side
  aggregation, which `analytics_service.py`'s own docstring already flags as a known tradeoff.
- **No email open/delivery tracking.** `report_deliveries.status` records the SMTP handoff
  result only — a `250 OK` from the relay is not proof of inbox delivery.

---

## 11. Gates — run after every phase

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m pytest                      # baseline is 118 passed / 8 pre-existing failures
python -m ruff check app tests        # must be clean

cd ..\frontend
npm run build                         # must be clean
```

> **Important:** the repo has **8 pre-existing test failures** on `develop` (documented in
> `REFACTOR_NOTES.md`). **Record the exact baseline before starting Phase 1** and compare against
> it, not against zero. Do not "fix" unrelated failures inside this work.

Manual smoke, in order:
1. `docker compose up -d` → postgres healthy, MinIO up
2. `python -m app.db.init_db` (creates `report_deliveries`)
3. `uvicorn app.main:app --reload --reload-dir app --port 8000`
4. `GET /health/smtp` → 503 while disabled, 200 once configured
5. `GET /reports/incidents/preview?range=7D` → insight strings
6. `GET /reports/incidents.pdf?range=7D` → opens in a viewer, correct fonts, no overlapping labels
7. `POST /reports/incidents/email` → arrives in the inbox with the attachment
8. Repeat step 6 against an **empty database** (the zero-incident path)

---

## 12. ⚠️ Questions to confirm before or during execution

Defaults are baked in for all of these, so execution is never blocked — but confirming changes
what gets built:

1. **SMTP provider?** Gmail (587/STARTTLS + App Password), Microsoft 365/Exchange, SendGrid, or
   an internal corporate relay (often port 25, no auth)? *Default assumed: Gmail-style
   587/STARTTLS with username+password.* An internal relay changes the auth path.
2. **Timezone on the report** — `Asia/Ho_Chi_Minh` as assumed, or UTC for consistency with the API?
3. **Report language** — English (as assumed, matching the dashboard), or Vietnamese, or bilingual
   labels? This changes §3.3 strings but not the structure. The font work in 2.1 is needed either
   way, because *zone names* may be Vietnamese even in an English report.
4. **Recipients** — free-form entry from the UI (as assumed), or a managed subscriber list stored
   in the DB and picked from a dropdown? The latter adds a model + CRUD endpoints + admin UI,
   roughly one extra phase.
5. **Evidence thumbnails in the PDF?** Assumed yes, up to 6, Critical/High only. They make the
   report far more useful and the attachment far bigger. Say the word to drop them and skip 3.5.
6. **Scheduled digests** (e.g. every Monday 07:00 to the safety manager) — assumed out of scope.
   If wanted now, say so and I will add a Phase 8 with APScheduler and a `report_schedules` table.
7. **Logo** — is there a De Heus logo file to place at `REPORT_LOGO_PATH`? Assumed none; the
   header falls back to a text wordmark.

---

## 13. Suggested execution order for Claude Desktop

Feed the phases one at a time, not the whole document at once. Between each, run §11 and paste
back any failures.

```
Phase 1  →  gates  →  Phase 2  →  gates  →  Phase 3  →  gates + open the PDF and look at it
        →  Phase 4  →  gates  →  Phase 5  →  gates + curl smoke  →  Phase 6  →  gates
        →  Phase 7 (docs)
```

Phase 3 is the one that will need a visual iteration pass. Everything else should land first try
if the acceptance criteria are respected.
