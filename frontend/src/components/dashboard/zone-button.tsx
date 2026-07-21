import { Trash2 } from "lucide-react";
import { type PhysicalZone } from "@/types/zone";

export function ZoneButton({
  zone,
  cameraCount,
  onDelete,
}: {
  zone: PhysicalZone;
  cameraCount: number;
  onDelete: () => void;
}) {
  return (
    <div className="group flex items-center justify-between rounded-md border border-transparent px-3 py-3 text-left transition hover:border-slate-200 hover:bg-white">
      <span className="flex min-w-0 items-center gap-3">
        <span className={`size-2.5 rounded-full ${cameraCount > 0 ? "bg-emerald-500" : "bg-slate-400"}`} />
        <span className="min-w-0">
          <span className="block truncate text-sm font-semibold text-slate-700">{zone.name}</span>
          <span className="text-xs text-slate-500">
            {cameraCount} camera{cameraCount === 1 ? "" : "s"}
          </span>
        </span>
      </span>
      <button
        type="button"
        aria-label={`Delete zone ${zone.name}`}
        onClick={onDelete}
        className="shrink-0 rounded p-1.5 text-slate-300 opacity-0 transition hover:bg-red-50 hover:text-red-600 group-hover:opacity-100"
      >
        <Trash2 className="size-4" aria-hidden="true" />
      </button>
    </div>
  );
}
