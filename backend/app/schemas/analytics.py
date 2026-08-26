from typing import Literal

from pydantic import BaseModel

AnalyticsRange = Literal["24H", "7D", "30D"]
CompareMode = Literal["week", "month"]


class SeverityCounts(BaseModel):
    Critical: int = 0
    High: int = 0
    Medium: int = 0
    Low: int = 0


class ZoneTotal(BaseModel):
    zone_id: int | None  # None = Unassigned
    zone_name: str
    total: int
    severity_counts: SeverityCounts


class TypeCount(BaseModel):
    category: Literal["ppe", "zone", "behavior"]
    type: str
    count: int


class AnalyticsSummary(BaseModel):
    range: AnalyticsRange
    zone_id: int | None
    grand_total: int
    zone_totals: list[ZoneTotal]
    severity_counts: SeverityCounts
    type_counts: list[TypeCount]
    active_zone_ids: list[int | None]
    open_incidents: int
    # Cameras that recorded an incident inside `range` — a historical figure,
    # which is what the PDF report's coverage narrative wants. Not the same as
    # live_cameras, and deliberately kept separate from it.
    active_cameras: int
    # Cameras whose source is being streamed right now, point-in-time and
    # independent of `range`. This is what the dashboard KPI card shows.
    # Defaults to 0 so report-side constructors, which describe a past period
    # where "streaming right now" is meaningless, don't have to supply it.
    live_cameras: int = 0
    total_cameras: int


class TrendPoint(BaseModel):
    date: str
    # Keyed by zone_id as a string ("unassigned" for the null bucket), since
    # the count-per-zone shape is what the stacked-area chart consumes directly.
    zone_totals: dict[str, int]


class AnalyticsTrend(BaseModel):
    range: AnalyticsRange
    bucket: Literal["hour", "day"]
    zones: list[ZoneTotal]
    points: list[TrendPoint]


class ComparePoint(BaseModel):
    label: str
    current: int
    prior: int


class SeverityDelta(BaseModel):
    severity: Literal["Critical", "High", "Medium", "Low"]
    prior: int
    current: int


class AnalyticsCompare(BaseModel):
    mode: CompareMode
    zone_id: int | None
    current_total: int
    prior_total: int
    delta_pct: float
    points: list[ComparePoint]
    severity_breakdown: list[SeverityDelta]
