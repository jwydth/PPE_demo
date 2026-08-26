import { Check, Loader2, Pause, PenLine, Play } from "lucide-react";
import { useZoneDrawing } from "@/hooks/useZoneDrawing";
import { EmptyState } from "@/components/ppe/result-panels";
import { AnalysisPhase } from "@/hooks/camera-panel-types";
import { ZoneType } from "@/types/zone";
import { countLabel } from "@/lib/format";

function DraftZoneStatus({ count }: { count: number }) {
  return (
    <div className="flex items-center gap-1.5 rounded-md border border-amber-400/40 bg-amber-400/10 px-3 py-2 text-xs font-semibold text-amber-200">
      <PenLine className="size-3.5 shrink-0" aria-hidden="true" />
      {countLabel(count, "zone")} unsaved
    </div>
  );
}

export function ZoneConfigPanel({
  zoneDrawing,
  zoneEnabled,
  phase,
  isStreaming,
  isVideo,
  isLive,
  isPlaying,
  togglePlayback,
}: {
  zoneDrawing: ReturnType<typeof useZoneDrawing>;
  zoneEnabled: boolean;
  phase: AnalysisPhase;
  isStreaming: boolean;
  isVideo: boolean;
  isLive: boolean;
  isPlaying: boolean;
  togglePlayback: () => void;
}) {
  return (
    <aside className="grid content-start gap-3 rounded-md border border-slate-800 bg-slate-950 p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
        Camera Model Settings
      </p>
      {zoneDrawing.isDrawing ? (
        <>
          <div className="flex items-center justify-between border-b border-white/10 pb-2">
            <span className="text-xs font-bold text-lime-200">Drawing Active</span>
            <button
              onClick={togglePlayback}
              className="flex items-center gap-1.5 rounded bg-white/10 px-2 py-1 text-xs font-bold uppercase tracking-wider text-white hover:bg-white/20"
            >
              {isPlaying ? (
                <>
                  <Pause className="size-3" /> Pause
                </>
              ) : (
                <>
                  <Play className="size-3" /> Play
                </>
              )}
            </button>
          </div>

          <div className="flex rounded-md border border-slate-700 bg-slate-900 p-1">
            <button
              type="button"
              onClick={() => {
                zoneDrawing.setConfigMode("draw");
                zoneDrawing.setSelectedZoneId(null);
              }}
              className={`flex-1 rounded py-1.5 text-xs font-semibold transition ${
                zoneDrawing.configMode === "draw"
                  ? "bg-lime-200 text-green-950"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              Draw zones
            </button>
            <button
              type="button"
              onClick={() => zoneDrawing.setConfigMode("modify")}
              className={`flex-1 rounded py-1.5 text-xs font-semibold transition ${
                zoneDrawing.configMode === "modify"
                  ? "bg-lime-200 text-green-950"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              Modify zones
            </button>
          </div>
          {zoneDrawing.pendingAutoZoneIds.size > 0 && (
            <DraftZoneStatus count={zoneDrawing.pendingAutoZoneIds.size} />
          )}
          {zoneDrawing.configMode === "modify" && zoneDrawing.selectedZoneId && (
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => {
                  zoneDrawing.setIsAddingPoint(!zoneDrawing.isAddingPoint);
                  zoneDrawing.setIsDrawingCurve(false);
                }}
                className={`w-full rounded py-1.5 text-xs font-semibold transition ${
                  zoneDrawing.isAddingPoint
                    ? "bg-amber-200 text-amber-950"
                    : "bg-slate-800 text-slate-200 hover:bg-slate-700"
                }`}
              >
                {zoneDrawing.isAddingPoint ? "Cancel Add Point" : "Add Point"}
              </button>
              <button
                type="button"
                onClick={() => {
                  zoneDrawing.setIsDrawingCurve(!zoneDrawing.isDrawingCurve);
                  zoneDrawing.setIsAddingPoint(false);
                }}
                className={`w-full rounded py-1.5 text-xs font-semibold transition ${
                  zoneDrawing.isDrawingCurve
                    ? "bg-amber-200 text-amber-950"
                    : "bg-slate-800 text-slate-200 hover:bg-slate-700"
                }`}
              >
                {zoneDrawing.isDrawingCurve ? "Cancel Draw Curve" : "Draw Curve"}
              </button>
            </div>
          )}
          <label className="grid gap-1 text-sm font-semibold text-slate-200">
            Zone name
            <input
              value={zoneDrawing.zoneName}
              onChange={(event) => {
                const newName = event.target.value;
                zoneDrawing.setZoneName(newName);
                if (zoneDrawing.configMode === "modify" && zoneDrawing.selectedZoneId) {
                  zoneDrawing.setZonesForVideo((prev) =>
                    prev.map((z) =>
                      z.id === zoneDrawing.selectedZoneId ? { ...z, name: newName } : z,
                    ),
                  );
                }
              }}
              disabled={zoneDrawing.configMode === "modify" && !zoneDrawing.selectedZoneId}
              className="rounded-md border border-slate-700 bg-slate-900 px-3 py-2 font-normal text-white outline-none focus:border-lime-200 disabled:opacity-50 disabled:cursor-not-allowed"
            />
          </label>
          <label className="grid gap-1 text-sm font-semibold text-slate-200">
            Zone type
            <select
              value={zoneDrawing.zoneType}
              onChange={(event) => {
                const newType = event.target.value as ZoneType;
                zoneDrawing.setZoneType(newType);
                if (zoneDrawing.configMode === "modify" && zoneDrawing.selectedZoneId) {
                  zoneDrawing.setZonesForVideo((prev) =>
                    prev.map((z) =>
                      z.id === zoneDrawing.selectedZoneId ? { ...z, type: newType } : z,
                    ),
                  );
                }
              }}
              disabled={zoneDrawing.configMode === "modify" && !zoneDrawing.selectedZoneId}
              className="rounded-md border border-slate-700 bg-slate-900 px-3 py-2 font-normal text-white outline-none focus:border-lime-200 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <option value="RESTRICTED">Restricted</option>
              <option value="WALKWAY">Walkway</option>
              <option value="SLIPPERY">Slippery</option>
              <option value="IGNORE">Exclusion Zone</option>
            </select>
          </label>
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={zoneDrawing.finishZone}
              disabled={zoneDrawing.draftPoints.length < 3 || (phase === "loading" && !isStreaming)}
              className="rounded-md bg-lime-200 px-3 py-2 text-sm font-semibold text-green-950 disabled:opacity-50"
            >
              Finish zone
            </button>
            <button
              type="button"
              onClick={() => zoneDrawing.setDraftPoints([])}
              disabled={(phase === "loading" && !isStreaming) || (zoneDrawing.configMode === "modify" && !zoneDrawing.selectedZoneId)}
              className="rounded-md border border-slate-700 px-3 py-2 text-sm font-semibold text-slate-200 disabled:opacity-50"
            >
              Clear draft
            </button>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => void zoneDrawing.handleSaveZones()}
              disabled={phase === "loading" && !isStreaming}
              className={`flex min-w-0 items-center justify-center gap-1.5 rounded-md border px-3 py-2 text-sm font-semibold transition-colors disabled:opacity-50 ${
                zoneDrawing.zoneActionState === "saving"
                  ? "pointer-events-none border-lime-200 text-lime-200"
                  : zoneDrawing.zoneActionState === "saved"
                    ? "pointer-events-none border-emerald-400 text-emerald-300"
                    : zoneDrawing.zoneActionState === "error"
                      ? "border-red-400 text-red-300"
                      : "border-lime-200 text-lime-200"
              }`}
            >
              {zoneDrawing.zoneActionState === "saving" ? (
                <><Loader2 className="size-3.5 animate-spin" /><span>Saving…</span></>
              ) : zoneDrawing.zoneActionState === "saved" ? (
                <><Check className="size-3.5" /><span>Saved</span></>
              ) : zoneDrawing.zoneActionState === "error" ? (
                <span>Save failed</span>
              ) : (
                <span>Save zones</span>
              )}
            </button>
            <button
              type="button"
              onClick={() => void zoneDrawing.clearSavedZones()}
              disabled={phase === "loading" && !isStreaming}
              className={`flex min-w-0 items-center justify-center gap-1.5 rounded-md border px-3 py-2 text-sm font-semibold transition-colors disabled:opacity-50 ${
                zoneDrawing.zoneActionState === "clearing"
                  ? "pointer-events-none border-red-400 text-red-200"
                  : zoneDrawing.zoneActionState === "cleared"
                    ? "pointer-events-none border-emerald-400 text-emerald-300"
                    : "border-red-400 text-red-200"
              }`}
            >
              {zoneDrawing.zoneActionState === "clearing" ? (
                <><Loader2 className="size-3.5 animate-spin" /><span>Clearing…</span></>
              ) : zoneDrawing.zoneActionState === "cleared" ? (
                <><Check className="size-3.5" /><span>Cleared</span></>
              ) : (
                <span>Clear zones</span>
              )}
            </button>
          </div>
          <button
            type="button"
            onClick={() => zoneDrawing.setIsDrawing(false)}
            className="w-full rounded-md border border-white/20 bg-white/5 py-2 text-sm font-semibold text-white hover:bg-white/10"
          >
            Stop configuration
          </button>
          <div className="rounded-md bg-slate-900 p-3 text-sm text-slate-300">
            Draft points: {zoneDrawing.draftPoints.length}
          </div>
        </>
      ) : (
        <div className="grid gap-3">
          {zoneDrawing.pendingAutoZoneIds.size > 0 && (
            <DraftZoneStatus count={zoneDrawing.pendingAutoZoneIds.size} />
          )}
          {!zoneEnabled && zoneDrawing.pendingAutoZoneIds.size === 0 ? (
            <EmptyState text="Enable Zone Monitoring to view saved areas or start drawing." />
          ) : zoneDrawing.pendingAutoZoneIds.size === 0 ? (
            <div className="rounded-md bg-slate-900 p-3 text-sm text-slate-300">
              Viewing {zoneDrawing.zonesForVideo.length} saved zone(s).
            </div>
          ) : null}
          <button
            type="button"
            onClick={() => zoneDrawing.setIsDrawing(true)}
            disabled={!(isVideo || isLive) || (phase === "loading" && !isStreaming)}
            className="w-full rounded-md bg-lime-200 py-2 text-sm font-semibold text-green-950 hover:bg-lime-100 disabled:opacity-50"
          >
            Configure zones
          </button>
        </div>
      )}
    </aside>
  );
}
