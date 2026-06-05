"use client";

import { useMemo } from "react";

import { ViolationReport } from "@/types/detection";
import { ZoneViolation } from "@/types/zone";

import { formatIncidentType } from "./ViolationReportCard";

interface GroupedIncident {
  id: string;
  timestamp: string;
  snapshotUrl?: string;
  videoName?: string;
  frameIndex?: number;
  records: (ViolationReport | ZoneViolation)[];
}

interface GroupedIncidentCardProps {
  incident: GroupedIncident;
}

export function GroupedIncidentCard({ incident }: GroupedIncidentCardProps) {
  const sortedRecords = useMemo(() => sortRecordsForDisplay(incident.records), [incident.records]);
  const incidentTitle = summarizeIncidentTypes(sortedRecords);

  return (
    <article className="bg-zinc-950 border border-red-500/30 rounded-lg overflow-hidden">
      {incident.snapshotUrl && (
        <img src={incident.snapshotUrl} alt="Grouped safety incident evidence" className="w-full aspect-video object-cover" />
      )}

      <div className="p-3 space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-mono text-[10px] text-red-300 tracking-widest uppercase">
              Safety Incident
            </p>
            <h3 className="mt-1 text-base font-semibold text-zinc-100">
              {incidentTitle}
            </h3>
          </div>

          <div className="bg-red-500/10 border border-red-500/30 rounded px-3 py-2 text-right shrink-0">
            <p className="font-mono text-[10px] text-red-300 tracking-widest">EVENTS</p>
            <p className="font-mono text-sm font-bold text-red-300">{incident.records.length}</p>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <ReviewField label="DETECTED" value={formatDetectedTime(incident.timestamp)} />
          <ReviewField label="SOURCE" value={incident.videoName ?? "Unknown source"} />
        </div>

        <div className="pt-3 border-t border-zinc-800">
          <p className="font-mono text-[10px] text-zinc-600 tracking-widest mb-2">
            INCIDENT DETAILS
          </p>
          <div className="flex flex-col gap-2">
            {sortedRecords.map((record, index) => {
              const isPpe = "violation_type" in record;
              return (
                <div key={record.id} className="bg-zinc-900 border border-zinc-800 rounded px-3 py-2">
                  <p className="font-mono text-xs text-zinc-200">
                    {isPpe 
                      ? `PPE Violation: ${formatIncidentType(record.violation_type)}`
                      : `Zone Incursion: Track ${record.track_id} in restricted area`}
                  </p>
                </div>
              );
            })}
          </div>
        </div>

      </div>
    </article>
  );
}

function ReviewField({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded px-3 py-2 min-w-0">
      <p className="font-mono text-[10px] text-zinc-600 tracking-widest">{label}</p>
      <p className="font-mono text-xs text-zinc-300 truncate">{value}</p>
    </div>
  );
}

function sortRecordsForDisplay(records: (ViolationReport | ZoneViolation)[]): (ViolationReport | ZoneViolation)[] {
  return [...records].sort((a, b) => {
    const aTrack = a.track_id ?? Number.MAX_SAFE_INTEGER;
    const bTrack = b.track_id ?? Number.MAX_SAFE_INTEGER;
    return aTrack - bTrack || (a.id ?? 0) - (b.id ?? 0);
  });
}

function summarizeIncidentTypes(records: (ViolationReport | ZoneViolation)[]): string {
  const ppeRecords = records.filter((r): r is ViolationReport => "violation_type" in r);
  const zoneRecords = records.filter((r): r is ZoneViolation => !("violation_type" in r));

  if (ppeRecords.length > 0 && zoneRecords.length > 0) return "PPE Violation & Zone Incursion";
  if (zoneRecords.length > 0) return "Restricted Zone Incursion";
  
  const types = new Set(ppeRecords.map((record) => record.violation_type));
  if (types.has("missing_helmet_and_vest")) return "Missing Safety Helmet and Vest";
  if (types.has("missing_helmet") && types.has("missing_vest")) return "Missing Safety Helmet and Vest";
  if (types.has("missing_helmet")) return "Missing Safety Helmet";
  if (types.has("missing_vest")) return "Missing Safety Vest";
  return formatIncidentType(ppeRecords[0]?.violation_type ?? "ppe_violation");
}

function formatDetectedTime(timestamp: string): string {
  const detectedAt = new Date(timestamp);
  return Number.isNaN(detectedAt.getTime()) ? timestamp : detectedAt.toLocaleString();
}

export type { GroupedIncident };
