import { IncidentCard } from "@/components/ppe/result-panels";
import { EmptyState } from "@/components/ppe/result-panels";
import { BehaviorIncident } from "@/types/behavior";
import { ViolationReport } from "@/types/detection";
import { ZoneViolation } from "@/types/zone";

export function CompactMetric({
  label,
  value,
  alert = false,
}: {
  label: string;
  value: string | number;
  alert?: boolean;
}) {
  return (
    <div className="rounded-md border border-slate-800 bg-slate-950 px-3 py-2">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-1 truncate text-sm font-semibold ${alert ? "text-red-300" : "text-lime-200"}`}>
        {value}
      </p>
    </div>
  );
}

export function AnalysisResultPanel({
  videoName,
  incidents,
  ppeEnabled,
  zoneEnabled,
  onRerun,
}: {
  videoName: string | undefined;
  incidents: (ViolationReport | ZoneViolation | BehaviorIncident)[];
  ppeEnabled: boolean;
  zoneEnabled: boolean;
  onRerun: () => void;
}) {
  return (
    <div className="grid h-fit content-start gap-3 rounded-md border border-slate-800 bg-slate-900 p-3">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-lime-200">
            Analysis Result
          </p>
          <h3 className="mt-1 text-base font-semibold text-white">
            {videoName}
          </h3>
        </div>
        <span
          className={`rounded px-2 py-1 text-xs font-semibold ring-1 ${
            incidents.length > 0
              ? "bg-red-500/10 text-red-200 ring-red-400/30"
              : "bg-emerald-500/10 text-emerald-200 ring-emerald-400/30"
          }`}
        >
          {incidents.length > 0 ? `${incidents.length} incident` : "No incidents"}
        </span>
      </div>

      <div className="grid items-start gap-3 xl:grid-cols-[minmax(0,320px)_1fr_auto]">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Processing Summary
          </p>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <CompactMetric
              label="Models"
              value={[ppeEnabled ? "PPE" : "", zoneEnabled ? "Zone" : ""]
                .filter(Boolean)
                .join(" + ")}
            />
            <CompactMetric
              label="Incidents"
              value={incidents.length}
              alert={incidents.length > 0}
            />
          </div>
        </div>

        <div className="border-t border-slate-800 pt-3 xl:border-l xl:border-t-0 xl:pl-3 xl:pt-0">
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
              Incident Evidence
            </p>
            <span className="text-xs text-slate-500">{incidents.length}</span>
          </div>
          {incidents.length > 0 ? (
            <div className="grid max-h-[520px] gap-2 overflow-auto pr-1">
              {incidents.map((event, index) => (
                <IncidentCard
                  key={`${event.id ?? index}-${event.timestamp}`}
                  event={event}
                  compact
                />
              ))}
            </div>
          ) : (
            <EmptyState text="No confirmed incidents were detected in this video." />
          )}
        </div>

        <button
          type="button"
          onClick={onRerun}
          className="h-fit rounded-md border border-white/10 px-3 py-2 text-sm font-semibold text-slate-200 transition hover:bg-white/10"
        >
          Rerun selected models
        </button>
      </div>
    </div>
  );
}
