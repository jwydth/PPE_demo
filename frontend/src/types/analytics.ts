export type AnalyticsRangeParam = "24H" | "7D" | "30D";
export type CompareMode = "week" | "month";
export type SeverityLevel = "Critical" | "High" | "Medium" | "Low";

export interface SeverityCounts {
  Critical: number;
  High: number;
  Medium: number;
  Low: number;
}

export interface ZoneTotal {
  zone_id: number | null;
  zone_name: string;
  total: number;
  severity_counts: SeverityCounts;
}

export interface TypeCount {
  category: "ppe" | "zone" | "behavior";
  type: string;
  count: number;
}

export interface AnalyticsSummary {
  range: AnalyticsRangeParam;
  zone_id: number | null;
  grand_total: number;
  zone_totals: ZoneTotal[];
  severity_counts: SeverityCounts;
  type_counts: TypeCount[];
  active_zone_ids: (number | null)[];
  open_incidents: number;
  active_cameras: number;
  total_cameras: number;
}

export interface TrendPoint {
  date: string;
  zone_totals: Record<string, number>;
}

export interface AnalyticsTrend {
  range: AnalyticsRangeParam;
  bucket: "hour" | "day";
  zones: ZoneTotal[];
  points: TrendPoint[];
}

export interface ComparePoint {
  label: string;
  current: number;
  prior: number;
}

export interface SeverityDelta {
  severity: SeverityLevel;
  prior: number;
  current: number;
}

export interface UnifiedIncident {
  id: number;
  category: "ppe" | "zone" | "behavior";
  type: string;
  severity: SeverityLevel;
  timestamp: string;
  camera_id: number | null;
  zone_id: number | null;
  zone_name: string;
  camera_label: string;
  snapshot_url: string | null;
}

export interface AnalyticsCompare {
  mode: CompareMode;
  zone_id: number | null;
  current_total: number;
  prior_total: number;
  delta_pct: number;
  points: ComparePoint[];
  severity_breakdown: SeverityDelta[];
}
