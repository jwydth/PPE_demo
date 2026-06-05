"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  getSafetyEvents,
  deleteAllIncidents,
  deleteViolation,
  deleteZoneViolation,
} from "@/lib/api";
import { ViolationReport } from "@/types/detection";
import { ZoneViolation } from "@/types/zone";

import { GroupedIncidentCard, GroupedIncident } from "./GroupedIncidentCard";

type ViolationFilter =
  | "all"
  | "missing_helmet"
  | "missing_vest"
  | "missing_helmet_and_vest"
  | "zone_incursion";

const FILTERS: Array<{ label: string; value: ViolationFilter }> = [
  { label: "All", value: "all" },
  { label: "Missing Helmet", value: "missing_helmet" },
  { label: "Missing Vest", value: "missing_vest" },
  { label: "Missing Helmet and Vest", value: "missing_helmet_and_vest" },
  { label: "Zone Incursion", value: "zone_incursion" },
];

export function DetectionHistoryPanel() {
  const [reports, setReports] = useState<(ViolationReport | ZoneViolation)[]>(
    [],
  );
  const [activeFilter, setActiveFilter] = useState<ViolationFilter>("all");
  const [isLoading, setIsLoading] = useState(true);
  const [isDeleting, setIsDeleting] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  const incidents = useMemo(() => groupReportsByIncident(reports), [reports]);
  const filteredIncidents = useMemo(() => {
    if (activeFilter === "all") return incidents;
    return incidents.filter((incident) =>
      incident.records.some((record) => {
        if ("violation_type" in record)
          return record.violation_type === activeFilter;
        return activeFilter === "zone_incursion";
      }),
    );
  }, [activeFilter, incidents]);

  const loadHistory = useCallback(async () => {
    setIsLoading(true);
    setErrorMsg("");

    try {
      setReports(await getSafetyEvents());
    } catch (err) {
      setErrorMsg(
        err instanceof Error ? err.message : "Could not load detection history",
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  const handleDeleteAll = useCallback(async () => {
    if (
      !confirm(
        "Are you sure you want to delete all incidents? This cannot be undone.",
      )
    ) {
      return;
    }

    setIsDeleting(true);
    setErrorMsg("");

    try {
      const result = await deleteAllIncidents();
      setReports([]);
      alert(`Deleted ${result.total_deleted} incident(s).`);
    } catch (err) {
      setErrorMsg(
        err instanceof Error ? err.message : "Could not delete incidents",
      );
    } finally {
      setIsDeleting(false);
    }
  }, []);

  const handleDeleteIncident = useCallback(
    async (incidentId: string, recordIds: (number | undefined)[]) => {
      if (!confirm("Delete this incident? This cannot be undone.")) {
        return;
      }

      try {
        // Delete each record from the backend
        for (const recordId of recordIds.filter((id) => id !== undefined)) {
          // Determine if it's a PPE violation or zone violation
          const record = reports.find((r) => r.id === recordId);
          if (record) {
            if ("violation_type" in record) {
              await deleteViolation(recordId!);
            } else {
              await deleteZoneViolation(recordId!);
            }
          }
        }

        // Remove from local state
        setReports((prev) => prev.filter((r) => !recordIds.includes(r.id)));
      } catch (err) {
        setErrorMsg(
          err instanceof Error ? err.message : "Could not delete incident",
        );
      }
    },
    [reports],
  );

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  return (
    <section className="max-w-6xl mx-auto flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-mono text-[10px] text-orange-500 tracking-widest uppercase">
            Detection History
          </p>
          <h2 className="mt-1 text-xl font-semibold text-zinc-100">
            Previous Safety Incidents
          </h2>
          <p className="mt-1 font-mono text-xs text-zinc-500">
            History shows confirmed incidents from video/CCTV analysis.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => void loadHistory()}
            disabled={isLoading || isDeleting}
            className="
              font-mono text-xs text-zinc-300 hover:text-orange-400 transition-colors
              border border-zinc-800 hover:border-orange-500/30 rounded px-4 py-2
              disabled:opacity-50 disabled:cursor-not-allowed
            "
          >
            {isLoading ? "REFRESHING..." : "REFRESH"}
          </button>
          <button
            onClick={() => void handleDeleteAll()}
            disabled={isLoading || isDeleting || reports.length === 0}
            className="
              font-mono text-xs text-red-400 hover:text-red-300 transition-colors
              border border-red-900 hover:border-red-500/30 rounded px-4 py-2
              disabled:opacity-50 disabled:cursor-not-allowed
            "
          >
            {isDeleting ? "DELETING..." : "DELETE ALL"}
          </button>
        </div>
      </div>

      <div className="bg-zinc-950 border border-zinc-800 rounded-lg p-3">
        <p className="font-mono text-[10px] text-zinc-600 tracking-widest mb-3">
          HISTORY SUMMARY
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          <SummaryMetric
            label="TOTAL INCIDENTS"
            value={filteredIncidents.length}
          />
          <SummaryMetric label="VIOLATION RECORDS" value={reports.length} />
          <SummaryMetric
            label="ACTIVE FILTER"
            value={filterLabel(activeFilter)}
          />
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        {FILTERS.map((filter) => (
          <button
            key={filter.value}
            onClick={() => setActiveFilter(filter.value)}
            className={[
              "font-mono text-xs rounded border px-3 py-2 transition-colors",
              activeFilter === filter.value
                ? "border-orange-500/50 bg-orange-500/10 text-orange-300"
                : "border-zinc-800 bg-zinc-950 text-zinc-500 hover:text-zinc-200 hover:border-zinc-600",
            ].join(" ")}
          >
            {filter.label}
          </button>
        ))}
      </div>

      {errorMsg && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 font-mono text-sm text-red-400">
          <span className="font-bold">ERROR</span> - {errorMsg}
        </div>
      )}

      {!errorMsg && isLoading ? (
        <div className="border border-orange-500/30 bg-orange-500/10 rounded-lg p-4 font-mono text-sm text-orange-300">
          Loading detection history...
        </div>
      ) : !errorMsg && filteredIncidents.length === 0 ? (
        <div className="border border-zinc-800 bg-zinc-950 rounded-lg p-4 font-mono text-sm text-zinc-400">
          No incidents found for the selected filter.
        </div>
      ) : !errorMsg ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {filteredIncidents.map((incident) => (
            <GroupedIncidentCard
              key={incident.id}
              incident={incident}
              onDelete={() =>
                handleDeleteIncident(
                  incident.id,
                  incident.records.map((r) => r.id),
                )
              }
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function groupReportsByIncident(
  reports: (ViolationReport | ZoneViolation)[],
): GroupedIncident[] {
  const grouped = new Map<string, (ViolationReport | ZoneViolation)[]>();

  for (const report of reports) {
    const key = `${report.video_name ?? "unknown"}:${report.frame_index ?? "unknown"}`;
    grouped.set(key, [...(grouped.get(key) ?? []), report]);
  }

  return [...grouped.entries()]
    .map(([id, records]) => {
      const sortedByTimestamp = [...records].sort(
        (a, b) =>
          timestampValue(a.timestamp) - timestampValue(b.timestamp) ||
          (a.id ?? 0) - (b.id ?? 0),
      );
      const firstRecord = sortedByTimestamp[0];
      const firstSnapshot = records.find((r) => {
        if ("snapshot_url" in r) return r.snapshot_url;
        return r.snapshot_path;
      });
      const snapshotUrl = firstSnapshot
        ? "snapshot_url" in firstSnapshot
          ? firstSnapshot.snapshot_url
          : firstSnapshot.snapshot_path
        : undefined;

      return {
        id,
        timestamp: firstRecord?.timestamp ?? "",
        snapshotUrl,
        videoName: firstRecord?.video_name,
        frameIndex: firstRecord?.frame_index,
        records,
      };
    })
    .sort((a, b) => timestampValue(b.timestamp) - timestampValue(a.timestamp));
}

function SummaryMetric({
  label,
  value,
}: {
  label: string;
  value: string | number;
}) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 min-w-0">
      <p className="text-zinc-500 font-mono text-[10px] tracking-widest truncate">
        {label}
      </p>
      <p className="font-mono text-sm font-bold text-orange-400 truncate">
        {value}
      </p>
    </div>
  );
}

function filterLabel(filter: ViolationFilter): string {
  return FILTERS.find((item) => item.value === filter)?.label ?? "All";
}

function timestampValue(timestamp: string): number {
  const value = new Date(timestamp).getTime();
  return Number.isNaN(value) ? 0 : value;
}
