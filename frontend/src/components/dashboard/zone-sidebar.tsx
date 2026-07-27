import { useState } from "react";
import { Plus, Shield } from "lucide-react";
import { createPhysicalZone, deletePhysicalZone, updatePhysicalZone } from "@/lib/ppe-api";
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
  const [newZoneName, setNewZoneName] = useState("");
  const [loading, setLoading] = useState(false);

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

  const handleCreateZone = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = newZoneName.trim();
    if (!name) return;
    setLoading(true);
    try {
      const newZone = await createPhysicalZone(name);
      onPhysicalZonesUpdate([...physicalZones, newZone]);
      setNewZoneName("");
    } catch (err) {
      alert(err instanceof Error ? err.message : "Could not create zone");
    } finally {
      setLoading(false);
    }
  };

  const handleUpdate = async (zone: PhysicalZone, newName: string) => {
    try {
      const updatedZone = await updatePhysicalZone(zone.id, newName);
      onPhysicalZonesUpdate(physicalZones.map((z) => (z.id === zone.id ? updatedZone : z)));
    } catch (err) {
      alert(err instanceof Error ? err.message : "Could not update zone");
      throw err;
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

        <form onSubmit={handleCreateZone} className="flex gap-1.5 px-1">
          <input
            type="text"
            placeholder="New zone name..."
            value={newZoneName}
            disabled={loading}
            onChange={(e) => setNewZoneName(e.target.value)}
            className="flex-1 rounded border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-950 placeholder-slate-400 outline-none focus:border-slate-400 transition"
          />
          <button
            type="submit"
            disabled={loading || !newZoneName.trim()}
            className="flex items-center justify-center rounded bg-slate-900 hover:bg-slate-800 disabled:opacity-50 disabled:bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white transition cursor-pointer"
          >
            <Plus className="size-4" />
          </button>
        </form>

        <div className="flex flex-col gap-2 overflow-y-auto flex-1 pr-1">
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
                onUpdate={(newName) => handleUpdate(zone, newName)}
              />
            ))
          )}
        </div>
      </div>
    </aside>
  );
}
