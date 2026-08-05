import type { BoundingBox, TrackingOverlayFrame } from "@/types/detection";

export const OVERLAY_BUFFER_MS = 10_000;
export const MAX_INTERPOLATION_FRAMES = 8;
export const MAX_PAST_HOLD_FRAMES = 2;

export type OverlaySelectionMode = "exact" | "interpolated" | "held" | "missing";

export type OverlaySelection = {
  frames: TrackingOverlayFrame[];
  mode: OverlaySelectionMode;
  signedSkewMs: number | null;
};

export type StreamTimeline = {
  streamEpoch: string | null;
  sourceTimeMs: number | null;
  mediaTimeSeconds: number;
};

export function rtspToHlsUrl(rtspUrl: string, annotated = false): string {
  const configuredBase = process.env.NEXT_PUBLIC_HLS_BASE_URL?.replace(/\/$/, "");
  const source = new URL(rtspUrl);
  const base = configuredBase ?? `${source.protocol === "rtsps:" ? "https" : "http"}://${source.hostname}:8888`;
  const sourcePath = source.pathname.replace(/\/$/, "");
  const outputPath = annotated ? `${sourcePath}_annotated` : sourcePath;
  return `${base}${outputPath}/index.m3u8`;
}

export function appendOverlayRing(
  current: TrackingOverlayFrame[],
  incoming: TrackingOverlayFrame[],
): TrackingOverlayFrame[] {
  const combined = [...current, ...incoming];
  const newest = Math.max(...combined.map((frame) => frame.source_time_ms ?? 0), 0);
  if (newest <= 0) return combined.slice(-240);
  return combined.filter((frame) => (frame.source_time_ms ?? newest) >= newest - OVERLAY_BUFFER_MS);
}

export function selectOverlayAtTime(
  frames: TrackingOverlayFrame[],
  targetSourceTimeMs: number | null,
  fps = 24,
): TrackingOverlayFrame[] {
  return selectOverlayAtTimeWithDiagnostics(frames, targetSourceTimeMs, fps).frames;
}

export function selectOverlayAtTimeWithDiagnostics(
  frames: TrackingOverlayFrame[],
  targetSourceTimeMs: number | null,
  fps = 24,
): OverlaySelection {
  if (targetSourceTimeMs === null) {
    const latestTime = Math.max(...frames.map((frame) => frame.source_time_ms ?? 0), 0);
    const latestFrame = Math.max(...frames.map((frame) => frame.frame_index), 0);
    const latest = frames.filter((frame) =>
      latestTime > 0
        ? frame.source_time_ms === latestTime
        : frame.frame_index === latestFrame,
    );
    return { frames: latest, mode: latest.length > 0 ? "exact" : "missing", signedSkewMs: null };
  }
  const epochs = frames.filter((frame) => frame.source_time_ms !== undefined);
  if (epochs.length === 0) return { frames: [], mode: "missing", signedSkewMs: null };

  const nearest = epochs.reduce((best, frame) =>
    Math.abs((frame.source_time_ms ?? 0) - targetSourceTimeMs)
      < Math.abs((best.source_time_ms ?? 0) - targetSourceTimeMs)
      ? frame
      : best,
  );
  const epoch = nearest.stream_epoch;
  const sameEpoch = epochs.filter((frame) => frame.stream_epoch === epoch);
  const trackIds = new Set(sameEpoch.map((frame) => frame.track_id).filter((id) => id !== undefined));
  const selected: TrackingOverlayFrame[] = [];
  const modes: OverlaySelectionMode[] = [];
  const signedSkews: number[] = [];

  for (const trackId of trackIds) {
    const track = sameEpoch
      .filter((frame) => frame.track_id === trackId)
      .sort((left, right) => (left.source_time_ms ?? 0) - (right.source_time_ms ?? 0));
    const before = [...track].reverse().find((frame) => (frame.source_time_ms ?? 0) <= targetSourceTimeMs);
    const after = track.find((frame) => (frame.source_time_ms ?? 0) >= targetSourceTimeMs);
    if (!before && !after) continue;
    if (before === after && before) {
      selected.push(before);
      modes.push("exact");
      signedSkews.push((before.source_time_ms ?? targetSourceTimeMs) - targetSourceTimeMs);
      continue;
    }
    if (!before) {
      // A future-only detection must not be painted on an earlier video frame.
      continue;
    }
    if (!after) {
      const signedSkewMs = (before.source_time_ms ?? targetSourceTimeMs) - targetSourceTimeMs;
      if (Math.abs(signedSkewMs) <= MAX_PAST_HOLD_FRAMES * 1000 / fps) {
        selected.push(before);
        modes.push("held");
        signedSkews.push(signedSkewMs);
      }
      continue;
    }

    const spanMs = (after.source_time_ms ?? 0) - (before.source_time_ms ?? 0);
    if (spanMs <= 0 || spanMs > (MAX_INTERPOLATION_FRAMES + 1) * 1000 / fps) continue;
    const alpha = (targetSourceTimeMs - (before.source_time_ms ?? 0)) / spanMs;
    selected.push({
      ...before,
      bbox: interpolateBox(before.bbox, after.bbox, alpha),
      source_time_ms: targetSourceTimeMs,
      frame_index: Math.round(before.frame_index + (after.frame_index - before.frame_index) * alpha),
      is_synthetic: true,
    });
    modes.push("interpolated");
    signedSkews.push(0);
  }
  return {
    frames: selected,
    mode: summarizeSelectionMode(modes),
    signedSkewMs: median(signedSkews),
  };
}

function summarizeSelectionMode(modes: OverlaySelectionMode[]): OverlaySelectionMode {
  if (modes.length === 0) return "missing";
  if (modes.includes("held")) return "held";
  if (modes.includes("interpolated")) return "interpolated";
  return "exact";
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const ordered = [...values].sort((left, right) => left - right);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 === 0
    ? (ordered[middle - 1] + ordered[middle]) / 2
    : ordered[middle];
}

function interpolateBox(left: BoundingBox, right: BoundingBox, alpha: number): BoundingBox {
  const value = (start: number, end: number) => start + (end - start) * alpha;
  return {
    x1: value(left.x1, right.x1),
    y1: value(left.y1, right.y1),
    x2: value(left.x2, right.x2),
    y2: value(left.y2, right.y2),
  };
}
