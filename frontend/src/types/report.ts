import { AnalyticsRangeParam, SeverityCounts } from "@/types/analytics";

export type ReportLanguage = "en" | "vi";

export interface ReportPreview {
  range: AnalyticsRangeParam;
  zone_scope_label: string;
  generated_at_local: string;
  grand_total: number;
  severity_counts: SeverityCounts;
  insights: string[];
  data_caveats: string[];
  language: ReportLanguage;
}

export interface ReportEmailRequest {
  recipients: string[];
  range: AnalyticsRangeParam;
  zone_id: number | null;
  subject?: string | null;
  message?: string | null;
  include_snapshots: boolean;
  language: ReportLanguage;
}

export interface ReportEmailResponse {
  status: "sent";
  recipients: string[];
  filename: string;
  size_bytes: number;
  object_key: string | null;
  sent_at: string;
}

export type ScheduleFrequency = "off" | "weekly" | "monthly";

export interface ReportScheduleRequest {
  frequency: ScheduleFrequency;
  day_of_week?: number | null;
  day_of_month?: number | null;
  hour: number;
  minute: number;
  zone_id?: number | null;
  recipients: string[];
  include_snapshots: boolean;
  subject?: string | null;
  message?: string | null;
  language: ReportLanguage;
}

export interface ReportScheduleResponse {
  frequency: ScheduleFrequency;
  day_of_week: number | null;
  day_of_month: number | null;
  hour: number;
  minute: number;
  zone_id: number | null;
  recipients: string[];
  include_snapshots: boolean;
  subject: string | null;
  message: string | null;
  language: ReportLanguage;
  last_sent_at: string | null;
  next_run_at: string | null;
  timezone_label: string;
}
