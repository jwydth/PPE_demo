"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { getViolations } from "@/lib/api";
import { ViolationReport } from "@/types/detection";

import { GroupedIncidentCard, GroupedIncident } from "./GroupedIncidentCard";

type ViolationFilter = "all" | "missing_helmet" | "missing_vest" | "missing_helmet_and_vest";

const FILTERS: Array<{ label: string; value: ViolationFilter }> = [
  { label: "All", value: "all" },
  { label: "Missing Helmet", value: "missing_helmet" },
  { label: "Missing Vest", value: "missing_vest" },
  { label: "Missing Helmet and Vest", value: "missing_helmet_and_vest" },
];

export function DetectionHistoryPanel() {
  const [reports, setReports] = useState<ViolationReport[]>([]);
  const [activeFilter, setActiveFilter] = useState<ViolationFilter>("all");
  const [isLoading, setIsLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState("");

  const incidents = useMemo(() => groupReportsByIncident(reports), [reports]);
  const filteredIncidents = useMemo(() => {
    if (activeFilter === "all") return incidents;
    return incidents.filter((incident) =>
      incident.records.some((record) => record.violation_type === activeFilter),
    );
  }, [activeFilter, incidents]);

  const loadHistory = useCallback(async () => {
    setIsLoading(true);
    setErrorMsg("");

    try {
      setReports(await getViolations());
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Could not load detection history");
    } finally {
      setIsLoading(false);
    }
  }, []);

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
        <button
          onClick={() => void loadHistory()}
          disabled={isLoading}
          className="
            font-mono text-xs text-zinc-300 hover:text-orange-400 transition-colors
            border border-zinc-800 hover:border-orange-500/30 rounded px-4 py-2
            disabled:opacity-50 disabled:cursor-not-allowed
          "
        >
          {isLoading ? "REFRESHING..." : "REFRESH"}
        </button>
      </div>

      <div className="bg-zinc-950 border border-zinc-800 rounded-lg p-3">
        <p className="font-mono text-[10px] text-zinc-600 tracking-widest mb-3">
          HISTORY SUMMARY
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          <SummaryMetric label="TOTAL INCIDENTS" value={filteredIncidents.length} />
          <SummaryMetric label="VIOLATION RECORDS" value={reports.length} />
          <SummaryMetric label="ACTIVE FILTER" value={filterLabel(activeFilter)} />
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
            <GroupedIncidentCard key={incident.id} incident={incident} />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function groupReportsByIncident(reports: ViolationReport[]): GroupedIncident[] {
  const grouped = new Map<string, ViolationReport[]>();

  for (const report of reports) {
    const key = `${report.video_name ?? "unknown"}:${report.frame_index ?? "unknown"}`;
    grouped.set(key, [...(grouped.get(key) ?? []), report]);
  }

  return [...grouped.entries()]
    .map(([id, records]) => {
      const sortedByTimestamp = [...records].sort(
        (a, b) => timestampValue(a.timestamp) - timestampValue(b.timestamp) || a.id - b.id,
      );
      const firstRecord = sortedByTimestamp[0];
      const firstSnapshot = records.find((record) => record.snapshot_url)?.snapshot_url;

      return {
        id,
        timestamp: firstRecord?.timestamp ?? "",
        snapshotUrl: firstSnapshot,
        videoName: firstRecord?.video_name,
        frameIndex: firstRecord?.frame_index,
        records,
      };
    })
    .sort((a, b) => timestampValue(b.timestamp) - timestampValue(a.timestamp));
}

function SummaryMetric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 min-w-0">
      <p className="text-zinc-500 font-mono text-[10px] tracking-widest truncate">{label}</p>
      <p className="font-mono text-sm font-bold text-orange-400 truncate">{value}</p>
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
