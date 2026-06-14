"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { TrackingOverlay, TrackingOverlayFrame, ZonePolygon } from "@/types/detection";

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
  const zones = overlay?.zones ?? [];

  if (!overlay) return null;

  return (
    <>
      {(currentBoxes.length > 0 || zones.length > 0) ? (
        <svg
          className="pointer-events-none absolute inset-0 h-full w-full"
          viewBox={`0 0 ${frameWidth} ${frameHeight}`}
          preserveAspectRatio="none"
        >
          <ZonePolygons zones={zones} frameWidth={frameWidth} frameHeight={frameHeight} />
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
  const zones = overlay?.zones ?? [];

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

      {overlay && (currentBoxes.length > 0 || zones.length > 0) ? (
        <svg
          className="pointer-events-none absolute inset-0 h-full w-full"
          viewBox={`0 0 ${frameWidth} ${frameHeight}`}
          preserveAspectRatio="none"
        >
          <ZonePolygons zones={zones} frameWidth={frameWidth} frameHeight={frameHeight} />
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

    const tolerance = Math.max(overlay.stride || 1, 1) * 2;
    if (Math.abs(nearestFrame - currentFrame) > tolerance) return [];
    return framesByIndex.get(nearestFrame) ?? [];
  }, [currentTime, framesByIndex, overlay, sortedFrameIndexes]);
  return currentBoxes;
}

function ZonePolygons({
  zones,
  frameWidth,
  frameHeight,
}: {
  zones: ZonePolygon[];
  frameWidth: number;
  frameHeight: number;
}) {
  if (zones.length === 0) return null;
  return (
    <>
      {zones.map((zone) => {
        const pts = zone.points
          .map(([nx, ny]) => `${(nx / 1000) * frameWidth},${(ny / 1000) * frameHeight}`)
          .join(" ");
        const isRestricted = zone.zone_type === "RESTRICTED";
        const fill = isRestricted ? "rgba(239,68,68,0.20)" : "rgba(59,130,246,0.20)";
        const stroke = isRestricted ? "#ef4444" : "#3b82f6";
        const [lx, ly] = zone.points[0] ?? [0, 0];
        const labelX = (lx / 1000) * frameWidth;
        const labelY = (ly / 1000) * frameHeight;
        return (
          <g key={`${zone.zone_name}:${zone.zone_type}`}>
            <polygon
              points={pts}
              fill={fill}
              stroke={stroke}
              strokeWidth={Math.max(frameWidth, frameHeight) * 0.003}
              strokeLinejoin="round"
            />
            <text
              x={labelX + frameWidth * 0.006}
              y={labelY + frameHeight * 0.028}
              fill={stroke}
              fontSize={frameHeight * 0.022}
              fontWeight={700}
              style={{ textShadow: "0 0 4px rgba(0,0,0,0.8)" }}
            >
              {zone.zone_name}
            </text>
          </g>
        );
      })}
    </>
  );
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
    labels.push(`PPE: ${frame.missing_equipment.join(", ")}`);
  }
  if (frame.zone_type) {
    const zoneLabel =
      frame.zone_type === "RESTRICTED"
        ? "Restricted zone"
        : "Walkway violation";
    labels.push(`Zone: ${frame.zone_name ? `${zoneLabel} - ${frame.zone_name}` : zoneLabel}`);
  }
  if (labels.length === 0) {
    labels.push(frame.status === "unknown" ? "Status unknown" : "Compliant");
  }
  labels.push(`Track ${frame.track_id ?? frame.person_id ?? "-"}`);
  return labels;
}

function trackingColor(frame: TrackingOverlayFrame): string {
  if (frame.zone_type === "RESTRICTED") return "#ef4444";
  if (frame.zone_type === "WALKWAY") return "#3b82f6";
  if (frame.missing_equipment.length > 0 || frame.status === "violation" || !frame.compliant) {
    return "#ef4444";
  }
  if (frame.status === "unknown") return "#a1a1aa";
  return "#84cc16";
}

function labelColor(label: string, fallback: string): string {
  if (label.startsWith("PPE:")) return "#fca5a5";
  if (label.startsWith("Zone:")) return fallback;
  if (label.startsWith("Track")) return "#cbd5e1";
  return fallback;
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
