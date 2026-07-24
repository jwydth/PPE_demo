import { useQuery } from "@tanstack/react-query";
import { Bell, HardHat, Users, Video } from "lucide-react";
import { getAnalyticsSummary } from "@/lib/ppe-api";
import type { SafetyMetric } from "@/components/dashboard/data";
import type { AnalyticsRangeParam, AnalyticsSummary } from "@/types/analytics";

const KPI_POLL_MS = 5000;

/**
 * Single source of truth for the "PPE Compliance / Open Incidents / Active
 * Cameras / People On Shift" KPI row, rendered once (DashboardShell's
 * top-of-page row, shown on every tab) rather than duplicated per-page.
 *
 * Known caveat on Active Cameras: its denominator (total_cameras) counts
 * is_active rows in the cameras table, and ppe_violation_service.py /
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
  });
  const summary = query.data ?? null;
  const openIncidents = summary?.open_incidents ?? 0;
  const totalCameras = summary?.total_cameras ?? 0;
  const activeCameras = summary?.active_cameras ?? 0;

  const kpis: SafetyMetric[] = [
    {
      label: "PPE Compliance",
      value: "—",
      helper: "No compliance data source yet",
      trend: "Not yet tracked",
      icon: HardHat,
      tone: "slate",
    },
    {
      label: "Open Incidents",
      value: String(openIncidents),
      helper: "Critical + high severity, in range",
      trend: zoneId != null ? "Selected zone" : "All zones",
      icon: Bell,
      tone: "amber",
    },
    {
      label: "Active Cameras",
      value: `${activeCameras}/${totalCameras}`,
      helper: "Cameras with an incident in range",
      trend: `${range} window`,
      icon: Video,
      tone: "blue",
    },
    {
      label: "People On Shift",
      value: "—",
      helper: "No occupancy data source yet",
      trend: "Not yet tracked",
      icon: Users,
      tone: "slate",
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
