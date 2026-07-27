import { useState } from "react";
import { Check, Edit2, Trash2, X } from "lucide-react";
import { type PhysicalZone } from "@/types/zone";

export function ZoneButton({
  zone,
  cameraCount,
  onDelete,
  onUpdate,
}: {
  zone: PhysicalZone;
  cameraCount: number;
  onDelete: () => void;
  onUpdate: (newName: string) => Promise<void>;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [editName, setEditName] = useState(zone.name);
  const [loading, setLoading] = useState(false);

  const handleSave = async () => {
    const name = editName.trim();
    if (!name || name === zone.name) {
      setIsEditing(false);
      return;
    }
    setLoading(true);
    try {
      await onUpdate(name);
      setIsEditing(false);
    } catch (err) {
      // Parent alert handles showing the error, so we stay in edit mode
    } finally {
      setLoading(false);
    }
  };

  const handleCancel = () => {
    setEditName(zone.name);
    setIsEditing(false);
  };

  return (
    <div className="group flex h-14 items-center justify-between rounded-md border border-transparent px-3 text-left transition hover:border-slate-200 hover:bg-white">
      <span className="flex min-w-0 flex-1 items-center gap-3">
        <span className={`size-2.5 shrink-0 rounded-full ${cameraCount > 0 ? "bg-emerald-500" : "bg-slate-400"}`} />
        <span className="min-w-0 flex-1">
          {isEditing ? (
            <input
              type="text"
              value={editName}
              disabled={loading}
              onChange={(e) => setEditName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void handleSave();
                if (e.key === "Escape") handleCancel();
              }}
              className="w-full rounded border border-slate-300 bg-white px-2 py-0.5 text-xs text-slate-900 outline-none focus:border-slate-500 font-semibold"
              autoFocus
            />
          ) : (
            <>
              <span className="block truncate text-sm font-semibold text-slate-700">{zone.name}</span>
              <span className="text-xs text-slate-500">
                {cameraCount} camera{cameraCount === 1 ? "" : "s"}
              </span>
            </>
          )}
        </span>
      </span>

      <div className="flex items-center gap-1 shrink-0 ml-2">
        {isEditing ? (
          <>
            <button
              type="button"
              disabled={loading}
              onClick={() => void handleSave()}
              aria-label="Save changes"
              className="rounded p-1 text-slate-400 hover:bg-emerald-50 hover:text-emerald-600 transition cursor-pointer"
            >
              <Check className="size-4" />
            </button>
            <button
              type="button"
              disabled={loading}
              onClick={handleCancel}
              aria-label="Cancel changes"
              className="rounded p-1 text-slate-400 hover:bg-red-50 hover:text-red-600 transition cursor-pointer"
            >
              <X className="size-4" />
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={() => setIsEditing(true)}
              aria-label={`Edit zone ${zone.name}`}
              className="rounded p-1 text-slate-300 opacity-0 transition hover:bg-slate-100 hover:text-slate-700 group-hover:opacity-100 cursor-pointer"
            >
              <Edit2 className="size-3.5" />
            </button>
            <button
              type="button"
              aria-label={`Delete zone ${zone.name}`}
              onClick={onDelete}
              className="rounded p-1 text-slate-300 opacity-0 transition hover:bg-red-50 hover:text-red-600 group-hover:opacity-100 cursor-pointer"
            >
              <Trash2 className="size-3.5" />
            </button>
          </>
        )}
      </div>
    </div>
  );
}
