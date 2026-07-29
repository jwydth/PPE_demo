"""Deterministic, rule-based "key insights" for the incident report.

Pure functions, no I/O — fully unit-testable, reproducible, no LLM calls.
Insight strings use a lightweight `**bold**` marker, converted to `<b>...</b>`
by the PDF renderer (ReportLab Paragraph accepts that mini-HTML subset).
"""

from app.core.config import settings
from app.schemas.analytics import AnalyticsCompare, AnalyticsSummary, AnalyticsTrend

_MAX_INSIGHTS = 6
_SEVERITY_ORDER = ("Critical", "High", "Medium", "Low")


def build_insights(
    summary: AnalyticsSummary, trend: AnalyticsTrend, compare: AnalyticsCompare
) -> list[str]:
    insights: list[str] = []

    _add_trend_direction(insights, compare)
    _add_hotspot_zone(insights, summary)
    _add_dominant_type(insights, summary)
    _add_critical_severity(insights, summary)
    _add_severity_shift(insights, compare)
    _add_peak_bucket(insights, trend)
    _add_quiet_zones(insights, summary)
    _add_camera_coverage(insights, summary)
    _add_all_clear(insights, summary)

    return insights[:_MAX_INSIGHTS]


def build_caveats(
    *,
    summary: AnalyticsSummary,
    zone_id: int | None,
    zone_scope_label: str,
    tz_fallback: bool,
) -> list[str]:
    caveats: list[str] = []

    if summary.grand_total >= settings.ANALYTICS_LIMIT:
        caveats.append(
            f"⚠ Incident volume reached the aggregation cap "
            f"({settings.ANALYTICS_LIMIT:,} rows). Figures in this report may "
            f"undercount the true total. Raise `ANALYTICS_LIMIT`."
        )
    if summary.total_cameras == 0:
        caveats.append(
            "No active cameras are registered; zone attribution may be incomplete."
        )
    if zone_id is not None:
        caveats.append(
            f"Zone breakdown below is site-wide; headline figures are filtered "
            f"to {zone_scope_label}."
        )
    if tz_fallback:
        caveats.append(
            "Timestamps shown in UTC; configured timezone was unavailable."
        )

    return caveats


def _period_label(mode: str) -> str:
    return "7 days" if mode == "week" else "30 days"


def _add_trend_direction(insights: list[str], compare: AnalyticsCompare) -> None:
    period = _period_label(compare.mode)
    if compare.prior_total == 0:
        if compare.current_total > 0:
            insights.append(
                "First reporting period with recorded incidents — no prior "
                "baseline to compare against."
            )
        return
    direction = "up" if compare.current_total >= compare.prior_total else "down"
    insights.append(
        f"Incidents are **{direction} {abs(compare.delta_pct):.1f}%** vs the "
        f"previous {period} ({compare.current_total} vs {compare.prior_total})."
    )


def _add_hotspot_zone(insights: list[str], summary: AnalyticsSummary) -> None:
    if not summary.zone_totals:
        return
    site_total = sum(z.total for z in summary.zone_totals)
    if site_total <= 0:
        return
    top = max(summary.zone_totals, key=lambda z: z.total)
    share = top.total / site_total
    if share >= 0.30:
        insights.append(
            f"**{top.zone_name}** accounts for {share * 100:.0f}% of all "
            f"incidents ({top.total} of {site_total}) — the highest of "
            f"{len(summary.zone_totals)} zones."
        )


def _add_dominant_type(insights: list[str], summary: AnalyticsSummary) -> None:
    if not summary.type_counts or summary.grand_total <= 0:
        return
    top = max(summary.type_counts, key=lambda t: t.count)
    share = top.count / summary.grand_total
    if share >= 0.25:
        insights.append(
            f"**{top.type}** is the most frequent incident type ({top.count} "
            f"occurrences, {share * 100:.0f}% of total)."
        )


def _add_critical_severity(insights: list[str], summary: AnalyticsSummary) -> None:
    if summary.severity_counts.Critical > 0:
        insights.append(
            f"**{summary.severity_counts.Critical} Critical** incidents require "
            f"immediate review."
        )


def _add_severity_shift(insights: list[str], compare: AnalyticsCompare) -> None:
    for delta in compare.severity_breakdown:
        if delta.prior <= 0 or delta.current <= delta.prior:
            continue
        growth = (delta.current - delta.prior) / delta.prior
        if growth < 0.50:
            continue
        multiplier = delta.current / delta.prior
        if multiplier >= 2:
            insights.append(
                f"**{delta.severity}**-severity incidents more than doubled "
                f"({delta.prior} -> {delta.current})."
            )
        else:
            insights.append(
                f"**{delta.severity}**-severity incidents rose {growth * 100:.0f}% "
                f"({delta.prior} -> {delta.current})."
            )
        return


def _add_peak_bucket(insights: list[str], trend: AnalyticsTrend) -> None:
    totals = [sum(p.zone_totals.values()) for p in trend.points]
    if not totals or sum(totals) <= 0:
        return
    mean = sum(totals) / len(totals)
    if mean <= 0:
        return
    peak_idx = max(range(len(totals)), key=lambda i: totals[i])
    peak_value = totals[peak_idx]
    if peak_value >= 2 * mean:
        insights.append(
            f"Peak activity on **{trend.points[peak_idx].date}** ({peak_value} "
            f"incidents, {peak_value / mean:.1f}x the average)."
        )


def _add_quiet_zones(insights: list[str], summary: AnalyticsSummary) -> None:
    quiet = [z.zone_name for z in summary.zone_totals if z.total == 0]
    if quiet:
        names = ", ".join(quiet)
        noun = "zone" if len(quiet) == 1 else "zones"
        insights.append(
            f"{len(quiet)} {noun} recorded no incidents: {names}."
        )


def _add_camera_coverage(insights: list[str], summary: AnalyticsSummary) -> None:
    if summary.total_cameras > 0 and summary.active_cameras < summary.total_cameras:
        insights.append(
            f"Incidents were recorded on {summary.active_cameras} of "
            f"{summary.total_cameras} active cameras."
        )


def _add_all_clear(insights: list[str], summary: AnalyticsSummary) -> None:
    if summary.grand_total == 0:
        insights.append(
            "No incidents recorded in this period across any monitored zone."
        )
