import { Shield } from "lucide-react";
import { deletePhysicalZone } from "@/lib/ppe-api";
import { PhysicalZone } from "@/types/zone";
import { ZoneButton } from "./zone-button";

export function ZoneSidebar({
  physicalZones,
  cameraCounts,
  onPhysicalZonesUpdate,
}: {
  physicalZones: PhysicalZone[];
  cameraCounts: Record<number, number>;
  onPhysicalZonesUpdate: (updated: PhysicalZone[]) => void;
}) {
  const handleDelete = async (zone: PhysicalZone) => {
    if (!confirm(`Delete the "${zone.name}" zone? Cameras assigned to it will become unassigned.`)) {
      return;
    }
    try {
      await deletePhysicalZone(zone.id);
      onPhysicalZonesUpdate(physicalZones.filter((z) => z.id !== zone.id));
    } catch (err) {
      alert(err instanceof Error ? err.message : "Could not delete zone");
    }
  };

  return (
    <aside className="border-b border-slate-200 bg-slate-50 lg:sticky lg:top-16 lg:h-[calc(100vh-4rem)] lg:w-72 lg:shrink-0 lg:border-b-0 lg:border-r">
      <div className="flex h-full flex-col gap-4 p-4">
        <div className="rounded-md border border-slate-200 bg-white p-3">
          <div className="flex items-center gap-3">
            <div className="flex size-11 items-center justify-center rounded-md bg-green-950 text-lime-200">
              <Shield className="size-5" aria-hidden="true" />
            </div>
            <div>
              <p className="text-lg font-semibold text-slate-950">Factory Zones</p>
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Live access
              </p>
            </div>
          </div>
        </div>

        <div className="grid gap-2">
          {physicalZones.length === 0 ? (
            <p className="px-1 text-xs text-slate-400">
              No zones yet — create one from Camera Feeds → Configure cameras.
            </p>
          ) : (
            physicalZones.map((zone) => (
              <ZoneButton
                key={zone.id}
                zone={zone}
                cameraCount={cameraCounts[zone.id] ?? 0}
                onDelete={() => void handleDelete(zone)}
              />
            ))
          )}
        </div>
      </div>
    </aside>
  );
}
