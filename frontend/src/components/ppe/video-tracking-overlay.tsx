"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Signpost, X } from "lucide-react";
import { TrackingOverlay, TrackingOverlayFrame } from "@/types/detection";
import { FallLiveDetection } from "@/types/behavior";
import { PPESuggestion, ZoneSuggestion } from "@/types/zone";
import { countLabel } from "@/lib/format";
import { ChipActionButton, DetectionChip } from "@/components/ppe/detection-chip";

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
        Tracking {countLabel(currentBoxes.length, "worker")}
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
          Tracking {countLabel(currentBoxes.length, "worker")}
        </div>
      ) : null}
    </div>
  );
}

export function FallOverlayLayer({
  detections,
  frameWidth,
  frameHeight,
  currentFrameIndex,
  ttlFrames = 10,
}: {
  detections: FallLiveDetection[];
  frameWidth?: number | null;
  frameHeight?: number | null;
  currentFrameIndex?: number;
  ttlFrames?: number;
}) {
  const width = frameWidth || 16;
  const height = frameHeight || 9;
  const visibleDetections = detections.filter((detection) => {
    if (!detection.bbox) return false;
    if (detection.is_stale) return false;
    if (detection.age_frames !== undefined && detection.age_frames > ttlFrames) return false;
    if (detection.frame_index === undefined || currentFrameIndex === undefined) return true;
    return Math.abs(currentFrameIndex - detection.frame_index) <= ttlFrames;
  });

  if (visibleDetections.length === 0) return null;

  return (
    <svg
      className="pointer-events-none absolute inset-0 h-full w-full"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
    >
      {visibleDetections.map((detection, index) => (
        <FallDetectionShape
          key={`${detection.track_id}-${detection.status}-${index}`}
          detection={detection}
          frameWidth={width}
          frameHeight={height}
        />
      ))}
    </svg>
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

function FallDetectionShape({
  detection,
  frameWidth,
  frameHeight,
}: {
  detection: FallLiveDetection;
  frameWidth: number;
  frameHeight: number;
}) {
  const color = fallColor(detection.status);
  const label = fallLabel(detection);
  const box = detection.bbox;
  const width = Math.max(0, box.x2 - box.x1);
  const height = Math.max(0, box.y2 - box.y1);
  const panelWidth = Math.min(
    frameWidth - box.x1,
    Math.max(frameWidth * 0.20, label.length * frameWidth * 0.008),
  );
  const panelHeight = frameHeight * 0.052;
  const panelY =
    box.y1 > panelHeight + frameHeight * 0.012
      ? box.y1 - panelHeight - frameHeight * 0.008
      : Math.min(frameHeight - panelHeight, box.y2 + frameHeight * 0.008);
  const panelX = Math.min(box.x1, frameWidth - panelWidth);

  return (
    <g>
      <rect
        x={box.x1}
        y={box.y1}
        width={width}
        height={height}
        fill="transparent"
        stroke={color}
        strokeWidth={Math.max(frameWidth, frameHeight) * 0.005}
        opacity={detection.is_interpolated ? 0.68 : 1}
      />
      <FallSkeleton
        keypoints={detection.keypoints ?? []}
        color={color}
        frameWidth={frameWidth}
        opacity={detection.is_interpolated ? 0.62 : 1}
      />
      <rect
        x={panelX}
        y={panelY}
        width={panelWidth}
        height={panelHeight}
        rx={frameWidth * 0.006}
        fill="rgba(2, 6, 23, 0.9)"
        stroke={color}
        strokeWidth={Math.max(frameWidth, frameHeight) * 0.0015}
        opacity={detection.is_interpolated ? 0.82 : 1}
      />
      <text
        x={panelX + frameWidth * 0.008}
        y={panelY + panelHeight * 0.64}
        fill="#ffffff"
        fontSize={frameHeight * 0.024}
        fontWeight={800}
        opacity={detection.is_interpolated ? 0.86 : 1}
      >
        {label}
      </text>
    </g>
  );
}

function FallSkeleton({
  keypoints,
  color,
  frameWidth,
  opacity = 1,
}: {
  keypoints: number[][];
  color: string;
  frameWidth: number;
  opacity?: number;
}) {
  if (keypoints.length === 0) return null;
  const usablePoint = (index: number) => {
    const point = keypoints[index];
    if (!point || point.length < 3 || point[2] < 0.12 || point[0] <= 0 || point[1] <= 0) return null;
    return { x: point[0], y: point[1] };
  };

  return (
    <>
      {FALL_SKELETON.map(([a, b]) => {
        const pointA = usablePoint(a);
        const pointB = usablePoint(b);
        if (!pointA || !pointB) return null;
        return (
          <line
            key={`${a}-${b}`}
            x1={pointA.x}
            y1={pointA.y}
            x2={pointB.x}
            y2={pointB.y}
            stroke={color}
            strokeWidth={frameWidth * 0.003}
            strokeLinecap="round"
            opacity={opacity}
          />
        );
      })}
      {keypoints.map((_, index) => {
        const point = usablePoint(index);
        if (!point) return null;
        return (
          <circle
            key={index}
            cx={point.x}
            cy={point.y}
            r={frameWidth * 0.004}
            fill={color}
            opacity={opacity}
          />
        );
      })}
    </>
  );
}

function fallColor(status: FallLiveDetection["status"]): string {
  if (status === "fall") return "#ef4444";
  if (status === "fall_risk") return "#f59e0b";
  return "#22c55e";
}

function fallLabel(detection: FallLiveDetection): string {
  const label =
    detection.status === "fall"
      ? "detected"
      : detection.status === "fall_risk"
      ? "risk"
      : "normal";
  return `Fall: ${label} ${detection.score.toFixed(2)}`;
}

const FALL_SKELETON: Array<[number, number]> = [
  [5, 6],
  [5, 11],
  [6, 12],
  [11, 12],
  [5, 7],
  [7, 9],
  [6, 8],
  [8, 10],
  [11, 13],
  [13, 15],
  [12, 14],
  [14, 16],
];

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
              fill="rgba(56,189,248,0.15)"
              stroke="#38bdf8"
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
            className="absolute w-48"
            style={{
              left: `${centerX * 100}%`,
              top: `${anchorY * 100}%`,
              transform: `translate(-50%, ${translateY})`,
            }}
          >
            <DetectionChip
              icon={Signpost}
              title={`${humanName} sign detected`}
              actions={[
                { label: "Add zone", icon: Check, onClick: () => onAccept(s, editableName), primary: true },
                { label: "Dismiss", icon: X, onClick: () => onDismiss(s) },
              ]}
            >
              <input
                value={editableName}
                onChange={(e) =>
                  setEditableNames((prev) => ({ ...prev, [s.suggestion_id]: e.target.value }))
                }
                aria-label="Zone name"
                className="w-full rounded border border-slate-700 bg-slate-900 px-1.5 py-1 text-[11px] text-white outline-none focus-visible:border-sky-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-sky-400"
              />
            </DetectionChip>
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
    <>
      <svg
        className="pointer-events-none absolute inset-0 h-full w-full"
        viewBox="0 0 1 1"
        preserveAspectRatio="none"
      >
        {suggestions.map((s) => {
          const [x1, y1, x2, y2] = s.bbox;
          return (
            <rect
              key={s.suggestion_id}
              x={x1} y={y1}
              width={x2 - x1}
              height={y2 - y1}
              fill="rgba(56,189,248,0.12)"
              stroke="#38bdf8"
              strokeWidth={0.004}
              strokeDasharray="0.015 0.008"
            />
          );
        })}
      </svg>

      <div className="pointer-events-none absolute left-0 right-0 top-0 flex flex-col gap-1.5 p-2">
        {suggestions.map((s) => {
          const humanName = SIGN_HUMAN_NAMES[s.source_class] ?? s.source_class;
          return (
            <div
              key={s.suggestion_id}
              className="detection-chip-enter pointer-events-auto flex flex-wrap items-center justify-between gap-2 rounded-md border border-sky-400/50 bg-slate-950/95 px-3 py-2 text-xs shadow-lg shadow-black/40"
            >
              <span className="flex items-center gap-1.5">
                <Signpost className="size-3.5 shrink-0 text-sky-300" aria-hidden="true" />
                <span className="font-semibold text-white">{humanName} sign detected</span>
                <span className="text-slate-400">PPE Detection is off</span>
              </span>
              <div className="flex shrink-0 gap-1.5">
                <ChipActionButton
                  action={{ label: "Enable PPE Detection", icon: Check, onClick: () => onEnable(s), primary: true }}
                />
                <ChipActionButton action={{ label: "Dismiss", icon: X, onClick: () => onDismiss(s) }} />
              </div>
            </div>
          );
        })}
      </div>
    </>
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
