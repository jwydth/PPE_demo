"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { TrackingOverlay, TrackingOverlayFrame } from "@/types/detection";
import { PPESuggestion, ZoneSuggestion } from "@/types/zone";

export function TrackingOverlayLayer({
  overlay,
  currentTime,
}: {
  overlay?: TrackingOverlay;
  currentTime: number;
}) {
  const currentBoxes = useCurrentBoxes(overlay, currentTime);
  const frameWidth = overlay?.frame_width || 16;
  const frameHeight = overlay?.frame_height || 9;

  if (!overlay) return null;

  return (
    <>
      {currentBoxes.length > 0 ? (
        <svg
          className="pointer-events-none absolute inset-0 h-full w-full"
          viewBox={`0 0 ${frameWidth} ${frameHeight}`}
          preserveAspectRatio="none"
        >
          <TrackingBoxes
            frames={currentBoxes}
            frameWidth={frameWidth}
            frameHeight={frameHeight}
          />
        </svg>
      ) : null}
      <div className="pointer-events-none absolute left-3 top-3 rounded bg-black/65 px-2 py-1 text-xs font-semibold text-white ring-1 ring-white/10">
        Tracking {currentBoxes.length} worker{currentBoxes.length === 1 ? "" : "s"}
      </div>
    </>
  );
}

export function VideoTrackingOverlay({
  src,
  overlay,
}: {
  src: string;
  overlay?: TrackingOverlay;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const animationRef = useRef<number | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const currentBoxes = useCurrentBoxes(overlay, currentTime);
  const frameWidth = overlay?.frame_width || 16;
  const frameHeight = overlay?.frame_height || 9;

  useEffect(() => {
    return () => {
      if (animationRef.current !== null) cancelAnimationFrame(animationRef.current);
    };
  }, []);

  const syncFrame = () => {
    const video = videoRef.current;
    if (!video) return;
    setCurrentTime(video.currentTime);
    if (!video.paused && !video.ended) {
      animationRef.current = requestAnimationFrame(syncFrame);
    }
  };

  return (
    <div
      className="relative overflow-hidden rounded-md border border-slate-800 bg-black"
      style={{ aspectRatio: `${frameWidth} / ${frameHeight}` }}
    >
      <video
        ref={videoRef}
        src={src}
        controls
        muted
        playsInline
        onPlay={syncFrame}
        onPause={syncFrame}
        onSeeked={syncFrame}
        onTimeUpdate={syncFrame}
        className="absolute inset-0 h-full w-full"
      />

      {overlay && currentBoxes.length > 0 ? (
        <svg
          className="pointer-events-none absolute inset-0 h-full w-full"
          viewBox={`0 0 ${frameWidth} ${frameHeight}`}
          preserveAspectRatio="none"
        >
          <TrackingBoxes
            frames={currentBoxes}
            frameWidth={frameWidth}
            frameHeight={frameHeight}
          />
        </svg>
      ) : null}

      {overlay ? (
        <div className="pointer-events-none absolute left-3 top-3 rounded bg-black/65 px-2 py-1 text-xs font-semibold text-white ring-1 ring-white/10">
          Tracking {currentBoxes.length} worker{currentBoxes.length === 1 ? "" : "s"}
        </div>
      ) : null}
    </div>
  );
}

function useCurrentBoxes(overlay: TrackingOverlay | undefined, currentTime: number) {
  const sortedFrameIndexes = useMemo(() => {
    if (!overlay?.frames.length) return [];
    return Array.from(new Set(overlay.frames.map((frame) => frame.frame_index))).sort(
      (a, b) => a - b,
    );
  }, [overlay]);
  const framesByIndex = useMemo(() => {
    const grouped = new Map<number, TrackingOverlayFrame[]>();
    for (const frame of overlay?.frames ?? []) {
      grouped.set(frame.frame_index, [...(grouped.get(frame.frame_index) ?? []), frame]);
    }
    return grouped;
  }, [overlay]);
  const currentBoxes = useMemo(() => {
    if (!overlay || sortedFrameIndexes.length === 0) return [];
    const currentFrame = Math.round(currentTime * overlay.fps);
    const nearestFrame = findNearestFrame(sortedFrameIndexes, currentFrame);
    if (nearestFrame === null) return [];

    const tolerance = Math.max(overlay.stride || 1, 1) * 30; // Increased tolerance for slow inference
    if (Math.abs(nearestFrame - currentFrame) > tolerance) return [];
    return framesByIndex.get(nearestFrame) ?? [];
  }, [currentTime, framesByIndex, overlay, sortedFrameIndexes]);
  return currentBoxes;
}

function TrackingBoxes({
  frames,
  frameWidth,
  frameHeight,
}: {
  frames: TrackingOverlayFrame[];
  frameWidth: number;
  frameHeight: number;
}) {
  return (
    <>
      {frames.map((frame, index) => {
        const labels = trackingLabels(frame);
        const color = trackingColor(frame);
        const width = Math.max(0, frame.bbox.x2 - frame.bbox.x1);
        const height = Math.max(0, frame.bbox.y2 - frame.bbox.y1);
        const panelWidth = Math.min(
          frameWidth - frame.bbox.x1,
          Math.max(
            frameWidth * 0.22,
            Math.max(...labels.map((label) => label.length)) * frameWidth * 0.008,
          ),
        );
        const lineHeight = frameHeight * 0.032;
        const panelHeight = lineHeight * labels.length + frameHeight * 0.018;
        const panelY =
          frame.bbox.y1 > panelHeight + frameHeight * 0.012
            ? frame.bbox.y1 - panelHeight - frameHeight * 0.008
            : Math.min(frameHeight - panelHeight, frame.bbox.y2 + frameHeight * 0.008);
        const panelX = Math.min(frame.bbox.x1, frameWidth - panelWidth);

        return (
          <g key={`${frame.frame_index}-${frame.track_id ?? frame.person_id ?? index}-${index}`}>
            <rect
              x={frame.bbox.x1}
              y={frame.bbox.y1}
              width={width}
              height={height}
              fill="transparent"
              stroke={color}
              strokeWidth={Math.max(frameWidth, frameHeight) * 0.004}
            />
            <rect
              x={panelX}
              y={panelY}
              width={panelWidth}
              height={panelHeight}
              rx={frameWidth * 0.006}
              fill="rgba(2, 6, 23, 0.88)"
              stroke={color}
              strokeWidth={Math.max(frameWidth, frameHeight) * 0.0015}
            />
            <text
              x={panelX + frameWidth * 0.008}
              y={panelY + lineHeight * 0.85}
              fill="#ffffff"
              fontSize={frameHeight * 0.022}
              fontWeight={700}
            >
              {labels.map((label, labelIndex) => (
                <tspan
                  key={label}
                  x={panelX + frameWidth * 0.008}
                  dy={labelIndex === 0 ? 0 : lineHeight}
                  fill={labelColor(label, color)}
                >
                  {label}
                </tspan>
              ))}
            </text>
          </g>
        );
      })}
    </>
  );
}

function trackingLabels(frame: TrackingOverlayFrame): string[] {
  const labels: string[] = [];
  if (frame.missing_equipment.length > 0) {
    labels.push(`PPE: ${frame.missing_equipment.map(formatEquipmentLabel).join(", ")}`);
  }
  if (frame.zone_type) {
    const zoneLabel =
      frame.zone_type === "RESTRICTED"
        ? "Restricted zone"
        : frame.zone_type === "SLIPPERY"
        ? "Slippery area"
        : "Walkway violation";
    labels.push(`Zone: ${frame.zone_name ? `${zoneLabel} - ${frame.zone_name}` : zoneLabel}`);
  }
  if (labels.length === 0) {
    labels.push(frame.status === "unknown" ? unknownStatusLabel(frame) : "Compliant");
  }
  labels.push(`${formatFrameRole(frame)} ${frame.track_id ?? frame.person_id ?? "-"}`);
  return labels;
}

function unknownStatusLabel(frame: TrackingOverlayFrame): string {
  if (frame.role === "worker" || frame.role === "janitor") return "PPE status pending";
  return "Role unknown";
}

function formatFrameRole(frame: TrackingOverlayFrame): string {
  if (frame.role === "worker") return "Worker";
  if (frame.role === "janitor") return "Janitor";
  return "Person";
}

function formatEquipmentLabel(label: string): string {
  if (label === "Role Uniform") return "Role Uniform (Vest or Cleaning Coverall)";
  return label;
}

function trackingColor(frame: TrackingOverlayFrame): string {
  if (frame.missing_equipment.length > 0 || frame.status === "violation" || !frame.compliant) {
    return "#ef4444";
  }
  if (frame.status === "unknown") return "#a1a1aa";
  return "#84cc16";
}

function labelColor(label: string, fallback: string): string {
  if (label.startsWith("PPE:")) return "#fca5a5";
  if (label.startsWith("Zone:")) return fallback;
  if (
    label.startsWith("Track") ||
    label.startsWith("Worker") ||
    label.startsWith("Janitor") ||
    label.startsWith("Person")
  ) {
    return "#cbd5e1";
  }
  return fallback;
}

const SIGN_HUMAN_NAMES: Record<string, string> = {
  M001_MustWearHardHat: "Must Wear Hard Hat",
  M002_MustWearSafetyVest: "Must Wear Safety Vest",
  P004_NoThoroughfare: "No Thoroughfare",
  W011_Slippery: "Slippery",
};

export function SuggestionOverlayLayer({
  suggestions,
  onAccept,
  onDismiss,
}: {
  suggestions: ZoneSuggestion[];
  onAccept: (suggestion: ZoneSuggestion, name: string) => void;
  onDismiss: (suggestion: ZoneSuggestion) => void;
}) {
  const [editableNames, setEditableNames] = useState<Record<string, string>>({});

  useEffect(() => {
    setEditableNames((prev) => {
      const next = { ...prev };
      for (const s of suggestions) {
        if (!(s.suggestion_id in next)) {
          next[s.suggestion_id] = SIGN_HUMAN_NAMES[s.source_class] ?? s.source_class;
        }
      }
      return next;
    });
  }, [suggestions]);

  if (suggestions.length === 0) return null;

  return (
    <>
      <svg
        className="pointer-events-none absolute inset-0 h-full w-full"
        viewBox="0 0 1 1"
        preserveAspectRatio="none"
      >
        {suggestions.map((s) => {
          const pts = s.normalized_coordinates;
          if (pts.length < 3) return null;
          const d =
            `M ${pts[0].x} ${pts[0].y} ` +
            pts.slice(1).map((p) => `L ${p.x} ${p.y}`).join(" ") +
            " Z";
          return (
            <path
              key={s.suggestion_id}
              d={d}
              fill="rgba(251,146,60,0.15)"
              stroke="#f97316"
              strokeWidth={0.005}
              strokeDasharray="0.02 0.01"
            />
          );
        })}
      </svg>

      {suggestions.map((s) => {
        const pts = s.normalized_coordinates;
        if (pts.length < 3) return null;
        const minX = Math.min(...pts.map((p) => p.x));
        const minY = Math.min(...pts.map((p) => p.y));
        const maxX = Math.max(...pts.map((p) => p.x));
        const maxY = Math.max(...pts.map((p) => p.y));
        const centerX = (minX + maxX) / 2;
        // Place label above the polygon; if too close to top, place it below instead
        const anchorY = minY > 0.12 ? minY : maxY;
        const translateY = minY > 0.12 ? "-100%" : "0%";
        const humanName = SIGN_HUMAN_NAMES[s.source_class] ?? s.source_class;
        const editableName = editableNames[s.suggestion_id] ?? humanName;

        return (
          <div
            key={s.suggestion_id}
            className="pointer-events-auto absolute"
            style={{
              left: `${centerX * 100}%`,
              top: `${anchorY * 100}%`,
              transform: `translate(-50%, ${translateY})`,
            }}
          >
            <div className="flex flex-col items-center gap-1 rounded-md border border-orange-400 bg-slate-950/90 px-2 py-1.5 text-xs shadow-lg">
              <span className="whitespace-nowrap font-semibold text-orange-300">
                ⚠ Suggested: {humanName}
              </span>
              <input
                value={editableName}
                onChange={(e) =>
                  setEditableNames((prev) => ({ ...prev, [s.suggestion_id]: e.target.value }))
                }
                className="w-full rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 text-[10px] text-white outline-none focus:border-orange-400"
              />
              <div className="flex gap-1">
                <button
                  type="button"
                  onClick={() => onAccept(s, editableName)}
                  className="rounded bg-orange-500 px-2 py-0.5 text-[10px] font-bold text-white hover:bg-orange-400"
                >
                  Accept ✓
                </button>
                <button
                  type="button"
                  onClick={() => onDismiss(s)}
                  className="rounded border border-slate-600 px-2 py-0.5 text-[10px] font-bold text-slate-300 hover:bg-white/10"
                >
                  Dismiss ✕
                </button>
              </div>
            </div>
          </div>
        );
      })}
    </>
  );
}

export function PPESuggestionBanner({
  suggestions,
  onEnable,
  onDismiss,
}: {
  suggestions: PPESuggestion[];
  onEnable: (suggestion: PPESuggestion) => void;
  onDismiss: (suggestion: PPESuggestion) => void;
}) {
  if (suggestions.length === 0) return null;

  return (
    <div className="pointer-events-auto absolute left-0 right-0 top-0 flex flex-col gap-1 p-2">
      {suggestions.map((s) => {
        const humanName = SIGN_HUMAN_NAMES[s.source_class] ?? s.source_class;
        return (
          <div
            key={s.suggestion_id}
            className="flex items-center justify-between gap-2 rounded-md border border-yellow-400/60 bg-slate-950/90 px-3 py-2 text-xs shadow-lg"
          >
            <span className="font-semibold text-yellow-300">
              ⚠ Sign detected: <span className="text-white">{humanName}</span> — PPE monitoring required
            </span>
            <div className="flex shrink-0 gap-1.5">
              <button
                type="button"
                onClick={() => onEnable(s)}
                className="rounded bg-yellow-400 px-2 py-1 text-[10px] font-bold text-slate-950 hover:bg-yellow-300"
              >
                Enable PPE ✓
              </button>
              <button
                type="button"
                onClick={() => onDismiss(s)}
                className="rounded border border-slate-600 px-2 py-1 text-[10px] font-bold text-slate-300 hover:bg-white/10"
              >
                Dismiss ✕
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function findNearestFrame(frameIndexes: number[], target: number): number | null {
  if (frameIndexes.length === 0) return null;

  let low = 0;
  let high = frameIndexes.length - 1;
  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    const value = frameIndexes[mid];
    if (value === target) return value;
    if (value < target) low = mid + 1;
    else high = mid - 1;
  }

  const before = frameIndexes[Math.max(0, high)];
  const after = frameIndexes[Math.min(frameIndexes.length - 1, low)];
  return Math.abs((before ?? 0) - target) <= Math.abs((after ?? 0) - target)
    ? before
    : after;
}
