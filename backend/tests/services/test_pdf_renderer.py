from app.schemas.analytics import (
    AnalyticsCompare,
    AnalyticsSummary,
    AnalyticsTrend,
    ComparePoint,
    SeverityCounts,
    SeverityDelta,
    TrendPoint,
    TypeCount,
    ZoneTotal,
)
from app.services.reporting.pdf_renderer import render_incident_report
from app.services.reporting.report_data import ReportData, ReportIncidentRow

_ZERO_SEV = SeverityCounts()


def _summary(
    *,
    grand_total: int = 0,
    zone_totals: list[ZoneTotal] | None = None,
    severity_counts: SeverityCounts | None = None,
    type_counts: list[TypeCount] | None = None,
    active_cameras: int = 1,
    total_cameras: int = 1,
    open_incidents: int = 0,
    zone_id: int | None = None,
) -> AnalyticsSummary:
    return AnalyticsSummary(
        range="7D",
        zone_id=zone_id,
        grand_total=grand_total,
        zone_totals=zone_totals or [],
        severity_counts=severity_counts or _ZERO_SEV,
        type_counts=type_counts or [],
        active_zone_ids=[],
        open_incidents=open_incidents,
        active_cameras=active_cameras,
        total_cameras=total_cameras,
    )


def _trend(points: list[TrendPoint] | None = None) -> AnalyticsTrend:
    return AnalyticsTrend(range="7D", bucket="day", zones=[], points=points or [])


def _compare(
    *,
    points: list[ComparePoint] | None = None,
    current_total: int = 0,
    prior_total: int = 0,
    delta_pct: float = 0.0,
    severity_breakdown: list[SeverityDelta] | None = None,
    mode: str = "week",
) -> AnalyticsCompare:
    return AnalyticsCompare(
        mode=mode,
        zone_id=None,
        current_total=current_total,
        prior_total=prior_total,
        delta_pct=delta_pct,
        points=points or [],
        severity_breakdown=severity_breakdown or [],
    )


def _report_data(
    *,
    summary: AnalyticsSummary | None = None,
    trend: AnalyticsTrend | None = None,
    compare: AnalyticsCompare | None = None,
    top_incidents: list[ReportIncidentRow] | None = None,
    insights: list[str] | None = None,
    data_caveats: list[str] | None = None,
    zone_scope_label: str = "All zones",
    factory_name: str = "Default Factory",
) -> ReportData:
    return ReportData(
        company_name="De Heus LLC",
        factory_name=factory_name,
        factory_location="Bien Hoa, Dong Nai",
        range_label="Last 7 days (22 Jul - 29 Jul 2026)",
        range_param="7D",
        zone_scope_label=zone_scope_label,
        generated_at_local="29 Jul 2026, 14:32",
        timezone_label="UTC+07:00 (Asia/Ho_Chi_Minh)",
        summary=summary or _summary(),
        trend=trend or _trend(),
        compare=compare or _compare(),
        top_incidents=top_incidents or [],
        insights=insights or [],
        data_caveats=data_caveats or [],
    )


def _sample_incidents() -> list[ReportIncidentRow]:
    return [
        ReportIncidentRow(
            timestamp_local="26 Jul 2026, 09:15",
            category="Zone",
            type="Restricted Area Entry",
            severity="Critical",
            zone_name="Khu vực sản xuất",
            camera_label="Cam A",
            snapshot_url="http://example.com/a.jpg",
        ),
        ReportIncidentRow(
            timestamp_local="26 Jul 2026, 08:02",
            category="PPE",
            type="Missing Helmet",
            severity="High",
            zone_name="Khu vực sản xuất",
            camera_label="Cam A",
            snapshot_url=None,
        ),
    ]


def _populated_report_data() -> ReportData:
    zone_totals = [
        ZoneTotal(
            zone_id=1, zone_name="Khu vực sản xuất", total=2, severity_counts=_ZERO_SEV
        ),
        ZoneTotal(zone_id=2, zone_name="Loading Bay", total=0, severity_counts=_ZERO_SEV),
    ]
    summary = _summary(
        grand_total=2,
        zone_totals=zone_totals,
        severity_counts=SeverityCounts(Critical=1, High=1),
        type_counts=[
            TypeCount(category="zone", type="Restricted Area Entry", count=1),
            TypeCount(category="ppe", type="Missing Helmet", count=1),
        ],
        open_incidents=2,
    )
    trend = _trend(
        [
            TrendPoint(date="Jul 24", zone_totals={"1": 0}),
            TrendPoint(date="Jul 25", zone_totals={"1": 0}),
            TrendPoint(date="Jul 26", zone_totals={"1": 2}),
        ]
    )
    compare = _compare(
        points=[ComparePoint(label=f"Day {i + 1}", current=0, prior=0) for i in range(7)],
        current_total=2,
        prior_total=0,
        delta_pct=0.0,
    )
    return _report_data(
        summary=summary,
        trend=trend,
        compare=compare,
        top_incidents=_sample_incidents(),
        insights=["**2 incidents** recorded this period.", "**1 Critical** incident requires review."],
        factory_name="Khu vực sản xuất",
    )


def test_render_produces_valid_pdf_bytes():
    pdf_bytes = render_incident_report(_populated_report_data())

    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 5000


def test_render_handles_vietnamese_zone_name_without_raising():
    pdf_bytes = render_incident_report(_populated_report_data())

    assert pdf_bytes.startswith(b"%PDF-")


def test_render_zero_incident_report_succeeds():
    data = _report_data(
        summary=_summary(grand_total=0),
        insights=["No incidents recorded in this period across any monitored zone."],
    )

    pdf_bytes = render_incident_report(data)

    assert pdf_bytes.startswith(b"%PDF-")


def test_render_handles_30d_trend_and_compare_points():
    trend = _trend([TrendPoint(date=f"Jul {i + 1}", zone_totals={"1": i % 5}) for i in range(30)])
    compare = _compare(
        points=[ComparePoint(label=f"Day {i + 1}", current=i % 4, prior=i % 3) for i in range(30)],
        current_total=60,
        prior_total=45,
        delta_pct=33.3,
    )
    data = _report_data(
        summary=_summary(grand_total=60, severity_counts=SeverityCounts(Low=60)),
        trend=trend,
        compare=compare,
    )

    pdf_bytes = render_incident_report(data)

    assert pdf_bytes.startswith(b"%PDF-")


def test_render_with_snapshots_none():
    pdf_bytes = render_incident_report(_populated_report_data(), snapshots=None)

    assert pdf_bytes.startswith(b"%PDF-")


def test_render_with_corrupt_snapshot_bytes_does_not_raise():
    data = _populated_report_data()
    snapshots = {"http://example.com/a.jpg": b"not-a-real-image"}

    pdf_bytes = render_incident_report(data, snapshots=snapshots)

    assert pdf_bytes.startswith(b"%PDF-")
