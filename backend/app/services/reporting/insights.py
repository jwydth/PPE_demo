"""Deterministic, rule-based "key insights" for the incident report.

Pure functions, no I/O — fully unit-testable, reproducible, no LLM calls.
Insight strings use a lightweight `**bold**` marker, converted to `<b>...</b>`
by the PDF renderer (ReportLab Paragraph accepts that mini-HTML subset).

Each generator takes an explicit `language` parameter and returns text in
that language. This is separate from the key-lookup table in i18n.py because
these sentences interpolate numbers/names and need real Vietnamese word
order, not just a swapped noun.
"""

from app.core.config import settings
from app.schemas.analytics import AnalyticsCompare, AnalyticsSummary, AnalyticsTrend
from app.services.reporting.i18n import Language

_MAX_INSIGHTS = 6
_SEVERITY_ORDER = ("Critical", "High", "Medium", "Low")
_SEVERITY_LABEL_VI = {"Critical": "Nghiêm Trọng", "High": "Cao", "Medium": "Trung Bình", "Low": "Thấp"}


def _severity_label(severity: str, language: Language) -> str:
    if language == "vi":
        return _SEVERITY_LABEL_VI.get(severity, severity)
    return severity


def build_insights(
    summary: AnalyticsSummary,
    trend: AnalyticsTrend,
    compare: AnalyticsCompare,
    language: Language = "en",
) -> list[str]:
    insights: list[str] = []

    _add_trend_direction(insights, compare, language)
    _add_hotspot_zone(insights, summary, language)
    _add_dominant_type(insights, summary, language)
    _add_critical_severity(insights, summary, language)
    _add_severity_shift(insights, compare, language)
    _add_peak_bucket(insights, trend, language)
    _add_quiet_zones(insights, summary, language)
    _add_camera_coverage(insights, summary, language)
    _add_all_clear(insights, summary, language)

    return insights[:_MAX_INSIGHTS]


def build_caveats(
    *,
    summary: AnalyticsSummary,
    zone_id: int | None,
    zone_scope_label: str,
    tz_fallback: bool,
    language: Language = "en",
) -> list[str]:
    caveats: list[str] = []

    if summary.grand_total >= settings.ANALYTICS_LIMIT:
        if language == "vi":
            caveats.append(
                f"⚠ Số lượng sự cố đã đạt giới hạn tổng hợp "
                f"({settings.ANALYTICS_LIMIT:,} dòng). Số liệu trong báo cáo này có thể thấp "
                f"hơn thực tế. Hãy tăng `ANALYTICS_LIMIT`."
            )
        else:
            caveats.append(
                f"⚠ Incident volume reached the aggregation cap "
                f"({settings.ANALYTICS_LIMIT:,} rows). Figures in this report may "
                f"undercount the true total. Raise `ANALYTICS_LIMIT`."
            )
    if summary.total_cameras == 0:
        if language == "vi":
            caveats.append(
                "Chưa có camera nào đang hoạt động được đăng ký; việc gán khu vực có thể "
                "chưa đầy đủ."
            )
        else:
            caveats.append(
                "No active cameras are registered; zone attribution may be incomplete."
            )
    if zone_id is not None:
        if language == "vi":
            caveats.append(
                f"Bảng phân tích khu vực bên dưới là số liệu toàn nhà máy; các số liệu "
                f"chính đã được lọc theo {zone_scope_label}."
            )
        else:
            caveats.append(
                f"Zone breakdown below is site-wide; headline figures are filtered "
                f"to {zone_scope_label}."
            )
    if tz_fallback:
        if language == "vi":
            caveats.append("Thời gian hiển thị theo UTC; múi giờ đã cấu hình không khả dụng.")
        else:
            caveats.append("Timestamps shown in UTC; configured timezone was unavailable.")

    return caveats


def _period_label(mode: str, language: Language) -> str:
    if language == "vi":
        return "7 ngày" if mode == "week" else "30 ngày"
    return "7 days" if mode == "week" else "30 days"


def _add_trend_direction(insights: list[str], compare: AnalyticsCompare, language: Language) -> None:
    period = _period_label(compare.mode, language)
    if compare.prior_total == 0:
        if compare.current_total > 0:
            if language == "vi":
                insights.append(
                    "Đây là kỳ báo cáo đầu tiên có ghi nhận sự cố — chưa có dữ liệu kỳ "
                    "trước để so sánh."
                )
            else:
                insights.append(
                    "First reporting period with recorded incidents — no prior "
                    "baseline to compare against."
                )
        return
    direction_up = compare.current_total >= compare.prior_total
    if language == "vi":
        direction = "tăng" if direction_up else "giảm"
        insights.append(
            f"Số sự cố đã **{direction} {abs(compare.delta_pct):.1f}%** so với {period} "
            f"trước đó ({compare.current_total} so với {compare.prior_total})."
        )
    else:
        direction = "up" if direction_up else "down"
        insights.append(
            f"Incidents are **{direction} {abs(compare.delta_pct):.1f}%** vs the "
            f"previous {period} ({compare.current_total} vs {compare.prior_total})."
        )


def _add_hotspot_zone(insights: list[str], summary: AnalyticsSummary, language: Language) -> None:
    if not summary.zone_totals:
        return
    site_total = sum(z.total for z in summary.zone_totals)
    if site_total <= 0:
        return
    top = max(summary.zone_totals, key=lambda z: z.total)
    share = top.total / site_total
    if share < 0.30:
        return
    if language == "vi":
        insights.append(
            f"**{top.zone_name}** chiếm {share * 100:.0f}% tổng số sự cố "
            f"({top.total}/{site_total}) — cao nhất trong số {len(summary.zone_totals)} khu vực."
        )
    else:
        insights.append(
            f"**{top.zone_name}** accounts for {share * 100:.0f}% of all "
            f"incidents ({top.total} of {site_total}) — the highest of "
            f"{len(summary.zone_totals)} zones."
        )


def _add_dominant_type(insights: list[str], summary: AnalyticsSummary, language: Language) -> None:
    if not summary.type_counts or summary.grand_total <= 0:
        return
    top = max(summary.type_counts, key=lambda t: t.count)
    share = top.count / summary.grand_total
    if share < 0.25:
        return
    if language == "vi":
        insights.append(
            f"**{top.type}** là loại sự cố xảy ra nhiều nhất ({top.count} lần, "
            f"chiếm {share * 100:.0f}% tổng số)."
        )
    else:
        insights.append(
            f"**{top.type}** is the most frequent incident type ({top.count} "
            f"occurrences, {share * 100:.0f}% of total)."
        )


def _add_critical_severity(insights: list[str], summary: AnalyticsSummary, language: Language) -> None:
    if summary.severity_counts.Critical <= 0:
        return
    if language == "vi":
        insights.append(
            f"**{summary.severity_counts.Critical} sự cố Nghiêm Trọng** cần được xem xét "
            f"ngay lập tức."
        )
    else:
        insights.append(
            f"**{summary.severity_counts.Critical} Critical** incidents require "
            f"immediate review."
        )


def _add_severity_shift(insights: list[str], compare: AnalyticsCompare, language: Language) -> None:
    for delta in compare.severity_breakdown:
        if delta.prior <= 0 or delta.current <= delta.prior:
            continue
        growth = (delta.current - delta.prior) / delta.prior
        if growth < 0.50:
            continue
        multiplier = delta.current / delta.prior
        sev_label = _severity_label(delta.severity, language)
        if language == "vi":
            if multiplier >= 2:
                insights.append(
                    f"Sự cố mức độ **{sev_label}** đã tăng hơn gấp đôi "
                    f"({delta.prior} → {delta.current})."
                )
            else:
                insights.append(
                    f"Sự cố mức độ **{sev_label}** đã tăng {growth * 100:.0f}% "
                    f"({delta.prior} → {delta.current})."
                )
        else:
            if multiplier >= 2:
                insights.append(
                    f"**{sev_label}**-severity incidents more than doubled "
                    f"({delta.prior} -> {delta.current})."
                )
            else:
                insights.append(
                    f"**{sev_label}**-severity incidents rose {growth * 100:.0f}% "
                    f"({delta.prior} -> {delta.current})."
                )
        return


def _add_peak_bucket(insights: list[str], trend: AnalyticsTrend, language: Language) -> None:
    totals = [sum(p.zone_totals.values()) for p in trend.points]
    if not totals or sum(totals) <= 0:
        return
    mean = sum(totals) / len(totals)
    if mean <= 0:
        return
    peak_idx = max(range(len(totals)), key=lambda i: totals[i])
    peak_value = totals[peak_idx]
    if peak_value < 2 * mean:
        return
    if language == "vi":
        insights.append(
            f"Hoạt động cao điểm vào **{trend.points[peak_idx].date}** ({peak_value} "
            f"sự cố, gấp {peak_value / mean:.1f} lần trung bình)."
        )
    else:
        insights.append(
            f"Peak activity on **{trend.points[peak_idx].date}** ({peak_value} "
            f"incidents, {peak_value / mean:.1f}x the average)."
        )


def _add_quiet_zones(insights: list[str], summary: AnalyticsSummary, language: Language) -> None:
    quiet = [z.zone_name for z in summary.zone_totals if z.total == 0]
    if not quiet:
        return
    names = ", ".join(quiet)
    if language == "vi":
        insights.append(f"{len(quiet)} khu vực không ghi nhận sự cố nào: {names}.")
    else:
        noun = "zone" if len(quiet) == 1 else "zones"
        insights.append(f"{len(quiet)} {noun} recorded no incidents: {names}.")


def _add_camera_coverage(insights: list[str], summary: AnalyticsSummary, language: Language) -> None:
    if summary.total_cameras <= 0 or summary.active_cameras >= summary.total_cameras:
        return
    if language == "vi":
        insights.append(
            f"Sự cố được ghi nhận trên {summary.active_cameras}/{summary.total_cameras} "
            f"camera đang hoạt động."
        )
    else:
        insights.append(
            f"Incidents were recorded on {summary.active_cameras} of "
            f"{summary.total_cameras} active cameras."
        )


def _add_all_clear(insights: list[str], summary: AnalyticsSummary, language: Language) -> None:
    if summary.grand_total != 0:
        return
    if language == "vi":
        insights.append("Không có sự cố nào được ghi nhận trong kỳ này tại bất kỳ khu vực giám sát nào.")
    else:
        insights.append("No incidents recorded in this period across any monitored zone.")
