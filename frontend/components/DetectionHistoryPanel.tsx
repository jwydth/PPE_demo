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
import { ViolationReportCard } from "./ViolationReportCard";
import { ZoneViolationCard } from "./ZoneViolationCard";

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
  const [reports, setReports] = useState<(ViolationReport | ZoneViolation)[]>([]);
  const [activeFilter, setActiveFilter] = useState<ViolationFilter>("all");
  const [isLoading, setIsLoading] = useState(true);
  const [isDeleting, setIsDeleting] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  const filteredReports = useMemo(() => {
    if (activeFilter === "all") {
      return reports;
    }
    if (activeFilter === "zone_incursion") {
      return reports.filter((r): r is ZoneViolation => !("violation_type" in r));
    }
    const ppeReports = reports.filter(
      (r): r is ViolationReport => "violation_type" in r
    );
    return ppeReports.filter(
      (report) => report.violation_type === activeFilter
    );
  }, [activeFilter, reports]);

  const loadHistory = useCallback(async () => {
    setIsLoading(true);
    setErrorMsg("");

    try {
      setReports(await getSafetyEvents());
    } catch (err) {
      setErrorMsg(
        err instanceof Error ? err.message : "Could not load detection history"
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  const handleDeleteAll = useCallback(async () => {
    if (
      !confirm(
        "Are you sure you want to delete all incidents? This cannot be undone."
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
        err instanceof Error ? err.message : "Could not delete incidents"
      );
    } finally {
      setIsDeleting(false);
    }
  }, []);

  const handleDeleteIncident = useCallback(async (report: ViolationReport | ZoneViolation) => {
    if (!confirm("Delete this incident? This cannot be undone.")) {
      return;
    }
    if (!report.id) return;

    try {
      if ("violation_type" in report) {
        await deleteViolation(report.id);
      } else {
        await deleteZoneViolation(report.id);
      }
      setReports((prev) => prev.filter((r) => r.id !== report.id));
    } catch (err) {
      setErrorMsg(
        err instanceof Error ? err.message : "Could not delete incident"
      );
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
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <SummaryMetric
            label="TOTAL INCIDENTS"
            value={filteredReports.length}
          />
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
      ) : !errorMsg && filteredReports.length === 0 ? (
        <div className="border border-zinc-800 bg-zinc-950 rounded-lg p-4 font-mono text-sm text-zinc-400">
          No incidents found for the selected filter.
        </div>
      ) : !errorMsg ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {filteredReports.map((report) => (
            <div key={report.id} className="relative group">
                { "violation_type" in report ? (
                    <ViolationReportCard report={report} showMetadata />
                ) : (
                    <ZoneViolationCard report={report} />
                )}
              <button
                onClick={() => handleDeleteIncident(report)}
                className="
                  absolute top-2 right-2 z-10
                  font-mono text-xs text-red-400 hover:text-red-300 transition-all
                  border border-red-900 hover:border-red-500/30 rounded px-2 py-1
                  bg-zinc-950/50 backdrop-blur-sm
                  opacity-0 group-hover:opacity-100
                "
              >
                DELETE
              </button>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
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
