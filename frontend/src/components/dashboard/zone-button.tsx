import { ArrowUpRight } from "lucide-react";
import { type Zone } from "./data";

const zoneStatus = {
  online: "bg-emerald-500",
  warning: "bg-amber-500",
  standby: "bg-slate-400",
};

export function ZoneButton({ zone, active }: { zone: Zone; active: boolean }) {
  return (
    <button
      className={`group flex items-center justify-between rounded-md border px-3 py-3 text-left transition ${
        active
          ? "border-lime-300 bg-lime-100/70 shadow-sm"
          : "border-transparent bg-transparent hover:border-slate-200 hover:bg-white"
      }`}
      type="button"
    >
      <span className="flex min-w-0 items-center gap-3">
        <span className={`size-2.5 rounded-full ${zoneStatus[zone.status]}`} />
        <span className="min-w-0">
          <span
            className={`block truncate text-sm font-semibold ${
              active ? "text-green-950" : "text-slate-700"
            }`}
          >
            {zone.name}
          </span>
          <span className="text-xs text-slate-500">{zone.cameraCount} cameras</span>
        </span>
      </span>
      {active ? <ArrowUpRight className="size-4 text-green-900" aria-hidden="true" /> : null}
    </button>
  );
}
