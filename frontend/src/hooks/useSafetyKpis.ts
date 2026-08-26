import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Bell, Video } from "lucide-react";
import { getAnalyticsSummary } from "@/lib/ppe-api";
import type { SafetyMetric } from "@/components/dashboard/data";
import type { AnalyticsRangeParam, AnalyticsSummary } from "@/types/analytics";

const KPI_POLL_MS = 5000;

/**
 * Single source of truth for the "PPE Compliance / Open Incidents / Active
 * Cameras / People On Shift" KPI row, rendered once (DashboardShell's
 * top-of-page row, shown on every tab) rather than duplicated per-page.
 *
 * Active Cameras reads live_cameras, not active_cameras. The latter counts
 * cameras with an *incident in the selected range* — a historical figure that
 * the PDF report's coverage narrative depends on, but one that reads as a bug
 * on a live dashboard: it stays high for days after a camera stops streaming,
 * and shifts when you change the range. live_cameras is a point-in-time count
 * of sources actually being streamed.
 *
 * Known caveat on the denominator (total_cameras): it counts is_active rows in
 * the cameras table, and ppe_violation_service.py /
 * behavior_incident_service.py lazily recreate a Camera row on the next
 * violation for a source_key that has none — so a deleted camera's count can
 * silently climb back up the moment its source produces another violation.
 * That's a backend data-model issue, not a display one; flagging here so
 * it's not mistaken for a UI bug if it comes up again.
 */
export function useSafetyKpis({
  range = "7D",
  zoneId = null,
}: {
  range?: AnalyticsRangeParam;
  zoneId?: number | null;
} = {}): {
  summary: AnalyticsSummary | null;
  kpis: SafetyMetric[];
  isLoading: boolean;
  isError: boolean;
  error: string;
} {
  const query = useQuery({
    queryKey: ["analytics", "summary", range, zoneId],
    queryFn: () => getAnalyticsSummary(range, zoneId),
    refetchInterval: KPI_POLL_MS,
    // zoneId is part of the key, so selecting a zone starts a new query and
    // `data` would otherwise drop to undefined until it resolves. The Zone
    // Pulse builds its nodes from summary.zone_totals, so that blanked the
    // whole chart and rebuilt it on every click — read as lag even once the
    // request was fast. Holding the previous data keeps the nodes mounted and
    // lets the selection highlight paint immediately.
    placeholderData: keepPreviousData,
  });
  const summary = query.data ?? null;
  // No summary yet means the numbers are *unknown*, not zero. Rendering the
  // `?? 0` fallback made the dashboard report "0 Open Incidents" for the
  // several seconds before the first response landed — a specific, believable,
  // wrong number on a safety readout. Cards flagged pending show a placeholder
  // instead. A refetch failure keeps the last good numbers on screen (the
  // 5s poll means one dropped request shouldn't blank the row), but a cold
  // failure with nothing cached stays pending rather than inventing zeros.
  const pending = summary === null;
  const openIncidents = summary?.open_incidents ?? 0;
  const totalCameras = summary?.total_cameras ?? 0;
  const liveCameras = summary?.live_cameras ?? 0;

  // PPE Compliance and People On Shift used to sit here as permanent "—"
  // placeholders, so half the most valuable strip on every page answered
  // nothing and pushed the two real metrics to the edges. They come back when
  // they have a data source behind them; a card that never resolves just
  // teaches people to skip the row.
  const kpis: SafetyMetric[] = [
    {
      label: "Open Incidents",
      value: String(openIncidents),
      helper: "Critical + high severity, in range",
      trend: zoneId != null ? "Selected zone" : "All zones",
      icon: Bell,
      tone: "amber",
      pending,
    },
    {
      label: "Active Cameras",
      value: `${liveCameras}/${totalCameras}`,
      helper: "Streams connected now",
      // Deliberately not `${range} window` — this metric is point-in-time and
      // does not move when the range selector changes.
      trend: "Live",
      icon: Video,
      tone: "blue",
      pending,
    },
  ];

  return {
    summary,
    kpis,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.isError
      ? query.error instanceof Error
        ? query.error.message
        : "Could not load analytics summary"
      : "",
  };
}
