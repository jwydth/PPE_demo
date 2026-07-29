from app.core.config import settings
from app.schemas.analytics import (
    AnalyticsCompare,
    AnalyticsSummary,
    AnalyticsTrend,
    SeverityCounts,
    SeverityDelta,
    TrendPoint,
    TypeCount,
    ZoneTotal,
)
from app.services.reporting.insights import build_caveats, build_insights

_ZERO_SEV = SeverityCounts()


def _summary(
    *,
    grand_total: int = 0,
    zone_totals: list[ZoneTotal] | None = None,
    severity_counts: SeverityCounts | None = None,
    type_counts: list[TypeCount] | None = None,
    active_cameras: int = 1,
    total_cameras: int = 1,
) -> AnalyticsSummary:
    return AnalyticsSummary(
        range="7D",
        zone_id=None,
        grand_total=grand_total,
        zone_totals=zone_totals or [],
        severity_counts=severity_counts or _ZERO_SEV,
        type_counts=type_counts or [],
        active_zone_ids=[],
        open_incidents=0,
        active_cameras=active_cameras,
        total_cameras=total_cameras,
    )


def _trend(points: list[TrendPoint] | None = None) -> AnalyticsTrend:
    return AnalyticsTrend(range="7D", bucket="day", zones=[], points=points or [])


def _compare(
    *,
    mode: str = "week",
    current_total: int = 0,
    prior_total: int = 0,
    delta_pct: float = 0.0,
    severity_breakdown: list[SeverityDelta] | None = None,
) -> AnalyticsCompare:
    return AnalyticsCompare(
        mode=mode,
        zone_id=None,
        current_total=current_total,
        prior_total=prior_total,
        delta_pct=delta_pct,
        points=[],
        severity_breakdown=severity_breakdown or [],
    )


def test_trend_direction_up_fires():
    compare = _compare(current_total=128, prior_total=95, delta_pct=34.2)

    insights = build_insights(_summary(), _trend(), compare)

    assert any("up 34.2%" in i and "128 vs 95" in i for i in insights)


def test_trend_direction_down_fires():
    compare = _compare(current_total=50, prior_total=100, delta_pct=-50.0)

    insights = build_insights(_summary(), _trend(), compare)

    assert any("down 50.0%" in i for i in insights)


def test_zero_baseline_trend_fires_instead_of_direction():
    compare = _compare(current_total=10, prior_total=0, delta_pct=0.0)

    insights = build_insights(_summary(), _trend(), compare)

    assert any("no prior baseline" in i for i in insights)
    assert not any("vs the previous" in i for i in insights)


def test_hotspot_zone_fires_at_30_percent():
    zone_totals = [
        ZoneTotal(zone_id=1, zone_name="Production Floor", total=52, severity_counts=_ZERO_SEV),
        ZoneTotal(zone_id=2, zone_name="Warehouse", total=76, severity_counts=_ZERO_SEV),
    ]
    summary = _summary(grand_total=128, zone_totals=zone_totals)

    insights = build_insights(summary, _trend(), _compare())

    assert any("Warehouse" in i and "59%" in i for i in insights)


def test_hotspot_zone_does_not_fire_below_threshold():
    zone_totals = [
        ZoneTotal(zone_id=1, zone_name="A", total=10, severity_counts=_ZERO_SEV),
        ZoneTotal(zone_id=2, zone_name="B", total=10, severity_counts=_ZERO_SEV),
        ZoneTotal(zone_id=3, zone_name="C", total=10, severity_counts=_ZERO_SEV),
        ZoneTotal(zone_id=4, zone_name="D", total=10, severity_counts=_ZERO_SEV),
    ]
    summary = _summary(grand_total=40, zone_totals=zone_totals)

    insights = build_insights(summary, _trend(), _compare())

    assert not any("accounts for" in i for i in insights)


def test_dominant_type_fires_at_25_percent():
    type_counts = [TypeCount(category="ppe", type="Missing Helmet", count=38)]
    summary = _summary(grand_total=128, type_counts=type_counts)

    insights = build_insights(summary, _trend(), _compare())

    assert any("Missing Helmet" in i and "30%" in i for i in insights)


def test_critical_severity_fires():
    summary = _summary(severity_counts=SeverityCounts(Critical=7))

    insights = build_insights(summary, _trend(), _compare())

    assert any("7 Critical" in i for i in insights)


def test_severity_shift_fires_on_doubling():
    compare = _compare(
        severity_breakdown=[SeverityDelta(severity="High", prior=8, current=19)]
    )

    insights = build_insights(_summary(), _trend(), compare)

    assert any("more than doubled" in i and "8 -> 19" in i for i in insights)


def test_severity_shift_does_not_fire_with_no_prior_baseline():
    compare = _compare(
        severity_breakdown=[SeverityDelta(severity="High", prior=0, current=19)]
    )

    insights = build_insights(_summary(), _trend(), compare)

    assert not any("severity incidents" in i for i in insights)


def test_peak_bucket_fires():
    points = [
        TrendPoint(date="Jul 24", zone_totals={"1": 5}),
        TrendPoint(date="Jul 25", zone_totals={"1": 5}),
        TrendPoint(date="Jul 26", zone_totals={"1": 30}),
        TrendPoint(date="Jul 27", zone_totals={"1": 5}),
    ]

    insights = build_insights(_summary(), _trend(points), _compare())

    assert any("Jul 26" in i and "30 incidents" in i for i in insights)


def test_quiet_zones_fires():
    zone_totals = [
        ZoneTotal(zone_id=1, zone_name="Loading Bay", total=0, severity_counts=_ZERO_SEV),
        ZoneTotal(zone_id=2, zone_name="QA Lab", total=0, severity_counts=_ZERO_SEV),
        ZoneTotal(zone_id=3, zone_name="Production Floor", total=5, severity_counts=_ZERO_SEV),
    ]
    summary = _summary(grand_total=5, zone_totals=zone_totals)

    insights = build_insights(summary, _trend(), _compare())

    assert any("Loading Bay" in i and "QA Lab" in i for i in insights)


def test_camera_coverage_fires():
    summary = _summary(active_cameras=4, total_cameras=9)

    insights = build_insights(summary, _trend(), _compare())

    assert any("4 of 9 active cameras" in i for i in insights)


def test_all_clear_fires_when_grand_total_zero():
    summary = _summary(grand_total=0)

    insights = build_insights(summary, _trend(), _compare())

    assert any("No incidents recorded" in i for i in insights)


def test_insights_are_capped_at_six():
    zone_totals = [
        ZoneTotal(zone_id=1, zone_name="Production Floor", total=100, severity_counts=_ZERO_SEV),
        ZoneTotal(zone_id=2, zone_name="Loading Bay", total=0, severity_counts=_ZERO_SEV),
    ]
    type_counts = [TypeCount(category="ppe", type="Missing Helmet", count=90)]
    summary = _summary(
        grand_total=100,
        zone_totals=zone_totals,
        type_counts=type_counts,
        severity_counts=SeverityCounts(Critical=7),
        active_cameras=4,
        total_cameras=9,
    )
    trend = _trend(
        [
            TrendPoint(date="Jul 24", zone_totals={"1": 5}),
            TrendPoint(date="Jul 26", zone_totals={"1": 30}),
        ]
    )
    compare = _compare(
        current_total=128,
        prior_total=95,
        delta_pct=34.2,
        severity_breakdown=[SeverityDelta(severity="High", prior=8, current=19)],
    )

    insights = build_insights(summary, trend, compare)

    assert len(insights) == 6


def test_caveat_fires_on_truncation():
    summary = _summary(grand_total=settings.ANALYTICS_LIMIT)

    caveats = build_caveats(
        summary=summary, zone_id=None, zone_scope_label="All zones", tz_fallback=False
    )

    assert any("aggregation cap" in c for c in caveats)


def test_caveat_fires_on_no_cameras():
    summary = _summary(total_cameras=0, active_cameras=0)

    caveats = build_caveats(
        summary=summary, zone_id=None, zone_scope_label="All zones", tz_fallback=False
    )

    assert any("No active cameras" in c for c in caveats)


def test_caveat_fires_on_zone_filter():
    summary = _summary()

    caveats = build_caveats(
        summary=summary, zone_id=3, zone_scope_label="Production Floor", tz_fallback=False
    )

    assert any("site-wide" in c and "Production Floor" in c for c in caveats)


def test_caveat_fires_on_timezone_fallback():
    summary = _summary()

    caveats = build_caveats(
        summary=summary, zone_id=None, zone_scope_label="All zones", tz_fallback=True
    )

    assert any("Timestamps shown in UTC" in c for c in caveats)


def test_no_caveats_in_the_normal_case():
    summary = _summary(grand_total=5, total_cameras=2, active_cameras=1)

    caveats = build_caveats(
        summary=summary, zone_id=None, zone_scope_label="All zones", tz_fallback=False
    )

    assert caveats == []
