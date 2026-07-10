"use client";

import {
  Loader2,
  Maximize2,
  RefreshCw,
  Trash2,
} from "lucide-react";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  deleteAllIncidents,
  deleteViolation,
  deleteZoneViolation,
  getSafetyEvents,
} from "@/lib/ppe-api";
import { BoundingBoxView } from "@/components/ppe/bounding-box-view";
import { FileUpload } from "@/components/ppe/file-upload";
import {
  FallOverlayLayer,
  PPESuggestionBanner,
  SuggestionOverlayLayer,
  TrackingOverlayLayer,
} from "@/components/ppe/video-tracking-overlay";
import {
  DetectionSummary,
  EmptyState,
  ErrorState,
  IncidentCard,
  LoadingState,
  PeopleResults,
} from "@/components/ppe/result-panels";
import { doPolygonsOverlap } from "@/lib/spatial-utils";
import { TrackingOverlay, ViolationReport } from "@/types/detection";
import { ZoneType, ZoneViolation } from "@/types/zone";
import { useDetectionUpload } from "@/hooks/useDetectionUpload";
import { useZoneDrawing } from "@/hooks/useZoneDrawing";
import { useLiveStream } from "@/hooks/useLiveStream";
import { useAutoZoneSuggestions } from "@/hooks/useAutoZoneSuggestions";
import { AnalysisPhase } from "@/hooks/camera-panel-types";
import { BehaviorIncident, FallLiveSummary } from "@/types/behavior";
import { safetyMetrics } from "./data";
import { TopBar, type DashboardView } from "./top-bar";
import { ZoneSidebar } from "./zone-sidebar";
import { MetricCard } from "./metric-card";
import { IconButton } from "./icon-button";
import { ZoneOverlaySvg } from "./zone-overlay-svg";
import { ZoneConfigPanel } from "./zone-config-panel";
import { AnalysisResultPanel } from "./analysis-result-panel";

const Factory3DView = dynamic(
  () => import("@/components/factory3d/factory-3d-view").then((m) => m.Factory3DView),
  { ssr: false },
);

const zoneColors: Record<ZoneType, string> = {
  RESTRICTED: "#dc2626",
  WALKWAY: "#0284c7",
  SLIPPERY: "#f59e0b",
};

function filterTrackingOverlay(
  overlay: TrackingOverlay | undefined,
  {
    showPpe,
    showZone,
  }: {
    showPpe: boolean;
    showZone: boolean;
  },
): TrackingOverlay | undefined {
  if (!overlay || (!showPpe && !showZone)) return undefined;

  const frames = overlay.frames.map((frame) => {
    const missingEquipment = showPpe ? frame.missing_equipment : [];
    const zoneFields = showZone
      ? {
          zone_id: frame.zone_id,
          zone_name: frame.zone_name,
          zone_type: frame.zone_type,
        }
      : {
          zone_id: undefined,
          zone_name: undefined,
          zone_type: undefined,
        };
    const hasViolation =
      missingEquipment.length > 0 || (showZone && Boolean(frame.zone_type));

    return {
      ...frame,
      ...zoneFields,
      missing_equipment: missingEquipment,
      compliant: hasViolation ? false : showPpe ? frame.compliant : true,
      status: hasViolation ? "violation" : showPpe ? frame.status : "compliant",
    };
  });

  return { ...overlay, frames };
}

function CameraPanel() {
  const [phase, setPhase] = useState<AnalysisPhase>("idle");
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [ppeEnabled, setPpeEnabled] = useState(true);
  const [zoneEnabled, setZoneEnabled] = useState(false);
  const [fallEnabled, setFallEnabled] = useState(false);

  const upload = useDetectionUpload();

  const liveStream = useLiveStream({
    ppeEnabled,
    zoneEnabled,
    fallEnabled,
    setPhase,
    setError,
    setStatus,
  });

  const isVideo = upload.isVideo;
  const sourceKey = liveStream.isLive ? liveStream.liveUrl : upload.file?.name;

  const zoneDrawing = useZoneDrawing({
    sourceKey,
    phase,
    isStreaming: liveStream.isStreaming,
    onZonesPersisted: () => liveStream.sendMessage({ event: "reload_zones" }),
    setStatus,
    setPhase,
    setError,
  });

  const autoZone = useAutoZoneSuggestions({
    sourceKey,
    isStreaming: liveStream.isStreaming,
    sendMessage: liveStream.sendMessage,
    zoneSuggestions: liveStream.zoneSuggestions,
    setZoneSuggestions: liveStream.setZoneSuggestions,
    ppeSuggestions: liveStream.ppeSuggestions,
    setPpeSuggestions: liveStream.setPpeSuggestions,
    zonesForVideo: zoneDrawing.zonesForVideo,
    setZonesForVideo: zoneDrawing.setZonesForVideo,
    persistZones: zoneDrawing.persistZones,
    setZoneEnabled,
    setPpeEnabled,
    openModifyModeFor: (draft) => {
      zoneDrawing.setIsDrawing(true);
      zoneDrawing.setConfigMode("modify");
      zoneDrawing.setSelectedZoneId(draft.id);
      zoneDrawing.setZoneName(draft.name);
      zoneDrawing.setZoneType(draft.type);
    },
  });

  useEffect(() => {
    // Automatically connect to RTSP stream on mount
    void zoneDrawing.loadSavedZones(liveStream.liveUrl);
    void liveStream.startStreaming(liveStream.liveUrl, true);
  }, []);

  const currentIncidents = useMemo(
    () => {
      const reports = liveStream.isStreaming || liveStream.streamData.summary
        ? liveStream.streamData.reports
        : (upload.videoResult?.reports ?? []);
      const zoneViolations = liveStream.isStreaming || liveStream.streamData.summary
        ? liveStream.streamData.zone_violations
        : (upload.videoResult?.zone_violations ?? []);

      return [
        ...(ppeEnabled ? reports : []),
        ...(zoneEnabled ? zoneViolations : []),
        ...(fallEnabled ? liveStream.streamData.behavior_incidents : []),
      ];
    },
    [
      liveStream.isStreaming,
      ppeEnabled,
      liveStream.streamData.reports,
      liveStream.streamData.summary,
      liveStream.streamData.zone_violations,
      liveStream.streamData.behavior_incidents,
      upload.videoResult?.reports,
      upload.videoResult?.zone_violations,
      fallEnabled,
      zoneEnabled,
    ],
  );
  const visibleTrackingOverlay = useMemo(
    () =>
      filterTrackingOverlay(
        liveStream.isStreaming || liveStream.streamData.summary
          ? liveStream.streamData.tracking_overlay
          : upload.videoResult?.tracking_overlay,
        {
          showPpe: ppeEnabled,
          showZone: zoneEnabled,
        },
      ),
    [
      liveStream.isStreaming,
      ppeEnabled,
      liveStream.streamData.summary,
      liveStream.streamData.tracking_overlay,
      upload.videoResult?.tracking_overlay,
      zoneEnabled,
    ],
  );
  // Unified identifier for the current camera source: the RTSP URL for a live
  // feed, otherwise the uploaded file name. Used as the zone storage key so
  // auto-zone save/load/clear works identically for live and uploaded sources.
  const feedAspectRatio = visibleTrackingOverlay
    ? `${visibleTrackingOverlay.frame_width ?? 16} / ${visibleTrackingOverlay.frame_height ?? 9}`
    : "16 / 9";
  const streamFrameWidth = liveStream.streamData.tracking_overlay.frame_width;
  const streamFrameHeight = liveStream.streamData.tracking_overlay.frame_height;
  const currentFrameIndex = Math.round(
    liveStream.currentVideoTime * (liveStream.streamData.tracking_overlay.fps || 30),
  );

  const selectFile = async (nextFile: File) => {
    upload.setFile(nextFile);
    upload.setImageResult(null);
    upload.setVideoResult(null);
    liveStream.setStreamData({
      summary: null,
      reports: [],
      zone_violations: [],
      behavior_incidents: [],
      fall_summary: null,
      fall_detections: [],
      fall_unavailable: null,
      tracking_overlay: {
        fps: 30,
        stride: 1,
        frame_width: 1000,
        frame_height: 1000,
        frames: [],
      },
      live_frame: null,
    });
    setError("");
    setStatus("");
    zoneDrawing.setZonesForVideo([]);
    zoneDrawing.setDraftPoints([]);
    zoneDrawing.setSelectedZoneId(null);
    liveStream.setZoneSuggestions({});
    setPhase("idle");
    zoneDrawing.setIsDrawing(false);
    liveStream.setCurrentVideoTime(0);

    if (nextFile.type.startsWith("video/")) {
      await zoneDrawing.loadSavedZones(nextFile.name);
      // Automatically trigger upload and streaming
      void liveStream.startStreaming(nextFile.name);
    }
  };

  const runSelectedModels = async () => {
    if (!liveStream.isLive && !upload.file) return;
    if (!ppeEnabled && !zoneEnabled && !fallEnabled) {
      setError("Enable at least one detection model before running analysis.");
      setPhase("error");
      return;
    }
    if (!liveStream.isLive && !isVideo && !ppeEnabled) {
      setError("Image uploads only support PPE detection in this dashboard.");
      setPhase("error");
      return;
    }

    if (zoneEnabled && (liveStream.isLive || isVideo)) {
      let hasOverlap = false;
      for (let i = 0; i < zoneDrawing.zonesReadyToSave.length; i++) {
        for (let j = i + 1; j < zoneDrawing.zonesReadyToSave.length; j++) {
          if (
            zoneDrawing.zonesReadyToSave[i].type !== zoneDrawing.zonesReadyToSave[j].type &&
            doPolygonsOverlap(zoneDrawing.zonesReadyToSave[i].points, zoneDrawing.zonesReadyToSave[j].points)
          ) {
            hasOverlap = true;
            break;
          }
        }
        if (hasOverlap) break;
      }

      if (hasOverlap) {
        setError("Cannot run analysis: Zones of different types overlap. Please adjust the vertices to avoid overlap.");
        setPhase("error");
        return;
      }
    }

    upload.setImageResult(null);
    upload.setVideoResult(null);
    setError("");
    setPhase("loading");
    zoneDrawing.setIsDrawing(false);
    try {
      if (liveStream.isLive) {
        if (zoneEnabled && zoneDrawing.zonesReadyToSave.length > 0) {
          await zoneDrawing.persistZones(zoneDrawing.zonesReadyToSave);
        }
        await liveStream.startStreaming(liveStream.liveUrl, true);
      } else if (isVideo) {
        if (zoneEnabled && zoneDrawing.zonesReadyToSave.length > 0) {
          await zoneDrawing.persistZones(zoneDrawing.zonesReadyToSave);
        }
        await liveStream.startStreaming(upload.file!.name);
      } else {
        upload.setImageResult(await upload.analyzeUploadedImage());
        setPhase("done");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not analyze the uploaded file");
      setPhase("error");
    }
  };

  const reset = () => {
    upload.setFile(null);
    upload.setImageResult(null);
    upload.setVideoResult(null);
    liveStream.resetStream();
    setError("");
    setStatus("");
    zoneDrawing.setDraftPoints([]);
    liveStream.setZoneSuggestions({});
    setPhase("idle");
    zoneDrawing.setIsDrawing(false);
  };

  const currentSummary = liveStream.isStreaming || liveStream.streamData.summary
    ? liveStream.streamData.summary
    : upload.videoResult?.summary;

  return (
    <section className="h-fit overflow-hidden rounded-md border border-slate-300 bg-slate-950 shadow-md">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
        <div>
          <p className="text-sm font-semibold text-white">
            {liveStream.isLive ? "Live Camera Feed" : "Packaging Line 1 - Uploaded Feed"}
          </p>
          <p className="text-xs text-slate-400">
            {liveStream.isLive
              ? `Connected to ${liveStream.liveUrl}`
              : "Upload a photo or CCTV clip, then choose which detection models run on this camera"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <IconButton label="Fullscreen camera feed" icon={Maximize2} />
        </div>
      </div>

      <div className="grid gap-4 p-4">
        {!liveStream.isLive && !upload.file ? (
          <FileUpload
            label="Upload camera image or video"
            helper="This replaces the live stream for now. Select model detections after the file is loaded."
            onFiles={(files) => selectFile(files[0])}
          />
        ) : null}

        {liveStream.isLive || upload.file ? (
          <div className="grid gap-4 rounded-md border border-slate-800 bg-slate-900 p-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-white">
                  {liveStream.isLive ? liveStream.liveUrl : upload.file?.name}
                </p>
                <p className="text-xs text-slate-400">
                  {liveStream.isLive ? "Live RTSP stream" : isVideo ? "Video feed simulation" : "Image frame simulation"}
                </p>
              </div>
              {!liveStream.isLive && (
                <button
                  type="button"
                  onClick={reset}
                  className="rounded-md border border-slate-700 px-3 py-2 text-sm font-semibold text-slate-200 transition hover:bg-white/10"
                >
                  Replace file
                </button>
              )}
            </div>

            <div className="grid gap-2 sm:grid-cols-3">
              <ModelToggle
                label="PPE Detection"
                description="Helmet and role-uniform compliance"
                enabled={ppeEnabled}
                onToggle={() => setPpeEnabled((current) => !current)}
              />
              <ModelToggle
                label="Zone Monitoring"
                description="Restricted and walkway zones"
                enabled={zoneEnabled && (liveStream.isLive || isVideo)}
                disabled={!liveStream.isLive && !isVideo}
                onToggle={() => setZoneEnabled((current) => !current)}
              />
              <ModelToggle
                label="Fall Detection"
                description="Live pose risk and incident capture"
                enabled={fallEnabled && (liveStream.isLive || isVideo)}
                disabled={!liveStream.isLive && !isVideo}
                onToggle={() => setFallEnabled((current) => !current)}
              />
            </div>

            {(liveStream.isLive || (isVideo && upload.videoUrl)) ? (
              <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_340px]">
                <div>
                  <div
                    ref={(el) => zoneDrawing.setSurfaceElement(el)}
                    onMouseMove={zoneDrawing.handleMouseMove}
                    onMouseUp={zoneDrawing.handleMouseUp}
                    onMouseLeave={zoneDrawing.handleMouseUp}
                    className="relative aspect-video overflow-hidden rounded-md border border-slate-800 bg-black"
                    style={{ aspectRatio: feedAspectRatio }}
                  >
                    {liveStream.isLive && liveStream.streamData.live_frame ? (
                      <img
                        src={liveStream.streamData.live_frame}
                        alt="Live stream"
                        className="absolute inset-0 h-full w-full object-contain"
                      />
                    ) : upload.videoUrl ? (
                      <video
                        ref={(el) => liveStream.setVideoElement(el)}
                        src={upload.videoUrl}
                        controls={!zoneDrawing.isDrawing}
                        muted
                        playsInline
                        onPlay={liveStream.handleVideoPlay}
                        onPause={liveStream.handleVideoPause}
                        onSeeked={liveStream.handleVideoTimeSync}
                        onTimeUpdate={liveStream.handleVideoTimeSync}
                        className={`absolute inset-0 h-full w-full object-contain ${
                          zoneDrawing.isDrawing ? "pointer-events-none" : ""
                        }`}
                      />
                    ) : (
                      <div className="flex h-full w-full items-center justify-center text-slate-500">
                        <Loader2 className="size-8 animate-spin" />
                      </div>
                    )}
                    {zoneEnabled || zoneDrawing.isDrawing || zoneDrawing.pendingAutoZoneIds.size > 0 ? (
                      <ZoneOverlaySvg zoneDrawing={zoneDrawing} zoneColors={zoneColors} />
                    ) : null}
                    {!zoneDrawing.isDrawing ? (
                      <TrackingOverlayLayer
                        overlay={visibleTrackingOverlay}
                        currentTime={liveStream.currentVideoTime}
                      />
                    ) : null}
                    {fallEnabled && !zoneDrawing.isDrawing ? (
                      <FallOverlayLayer
                        detections={liveStream.streamData.fall_detections}
                        frameWidth={streamFrameWidth}
                        frameHeight={streamFrameHeight}
                        currentFrameIndex={currentFrameIndex}
                      />
                    ) : null}
                    <SuggestionOverlayLayer
                      suggestions={Object.values(autoZone.zoneSuggestions)}
                      onAccept={(s, name) => void autoZone.handleAcceptSuggestion(s, name)}
                      onDismiss={autoZone.handleDismissSuggestion}
                    />
                    <PPESuggestionBanner
                      suggestions={ppeEnabled ? [] : Object.values(autoZone.ppeSuggestions)}
                      onEnable={autoZone.handleEnablePPESuggestion}
                      onDismiss={autoZone.handleDismissPPESuggestion}
                    />
                  </div>
                  {zoneDrawing.isDrawing ? (
                    <p className="mt-2 text-xs text-slate-400">
                      Click the video frame to add zone polygon points. Video controls are disabled during drawing.
                    </p>
                  ) : zoneEnabled ? (
                    <p className="mt-2 text-xs text-slate-400">
                      Viewing saved zones. Click &quot;Start draw zone&quot; to add new areas.
                    </p>
                  ) : null}
                </div>

                <div className="grid gap-4">
                  <ZoneConfigPanel
                    zoneDrawing={zoneDrawing}
                    zoneEnabled={zoneEnabled}
                    phase={phase}
                    isStreaming={liveStream.isStreaming}
                    isVideo={isVideo}
                    isLive={liveStream.isLive}
                    isPlaying={liveStream.isPlaying}
                    togglePlayback={liveStream.togglePlayback}
                  />
                  <FallStatusPanel
                    enabled={fallEnabled}
                    summary={liveStream.streamData.fall_summary}
                    unavailable={liveStream.streamData.fall_unavailable}
                    latestIncident={liveStream.streamData.behavior_incidents.at(-1)}
                  />
                </div>
              </div>
            ) : null}
          </div>
        ) : null}

        {phase === "loading" && (ppeEnabled || zoneEnabled || fallEnabled) ? <LoadingState text="Running inference..." /> : null}
        {phase === "error" ? <ErrorState text={error} /> : null}
        {status ? <EmptyState text={status} /> : null}

        {phase === "done" && upload.file && upload.imageResult ? (
          <div className="grid gap-4">
            <DetectionSummary result={upload.imageResult} />
            <BoundingBoxView file={upload.file} detections={upload.imageResult.detections} />
            <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
              <PeopleResults persons={upload.imageResult.persons} />
            </div>
          </div>
        ) : null}

        {phase === "done" && (upload.videoResult || liveStream.streamData.summary) ? (
          <AnalysisResultPanel
            videoName={currentSummary?.video_name}
            incidents={currentIncidents}
            ppeEnabled={ppeEnabled}
            zoneEnabled={zoneEnabled}
            onRerun={() => void runSelectedModels()}
          />
        ) : null}
      </div>
    </section>
  );
}

function ModelToggle({
  label,
  description,
  enabled,
  disabled = false,
  onToggle,
}: {
  label: string;
  description: string;
  enabled: boolean;
  disabled?: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={disabled}
      className={`flex items-center justify-between gap-3 rounded-md border p-3 text-left transition disabled:cursor-not-allowed disabled:opacity-50 ${
        enabled
          ? "border-lime-200 bg-lime-200/10 text-white"
          : "border-slate-800 bg-slate-950 text-slate-300 hover:border-slate-700"
      }`}
    >
      <span className="min-w-0">
        <span className="block text-sm font-semibold">{label}</span>
        <span className="mt-1 block text-xs text-slate-400">{description}</span>
      </span>
      <span
        className={`flex h-6 w-11 shrink-0 items-center rounded-full p-1 transition ${
          enabled ? "bg-lime-200" : "bg-slate-700"
        }`}
      >
        <span
          className={`size-4 rounded-full bg-slate-950 transition ${
            enabled ? "translate-x-5" : "translate-x-0"
          }`}
        />
      </span>
    </button>
  );
}

function FallStatusPanel({
  enabled,
  summary,
  unavailable,
  latestIncident,
}: {
  enabled: boolean;
  summary: FallLiveSummary | null;
  unavailable: string | null;
  latestIncident?: BehaviorIncident;
}) {
  if (!enabled) {
    return (
      <section className="rounded-md border border-slate-800 bg-slate-950 p-3">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-semibold text-white">Fall Detection</h3>
          <span className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs font-semibold text-slate-300">
            Disabled
          </span>
        </div>
      </section>
    );
  }

  const status = unavailable ? "unavailable" : summary?.status ?? (enabled ? "no_detection" : "off");
  const statusClass =
    status === "fall"
      ? "border-red-400 bg-red-500/10 text-red-100"
      : status === "fall_risk"
      ? "border-amber-300 bg-amber-400/10 text-amber-100"
      : status === "unavailable"
      ? "border-slate-600 bg-slate-800 text-slate-200"
      : status === "no_detection"
      ? "border-slate-600 bg-slate-800 text-slate-200"
      : "border-emerald-300 bg-emerald-400/10 text-emerald-100";
  const label =
    status === "fall"
      ? "Fall detected"
      : status === "fall_risk"
      ? "Fall risk"
      : status === "unavailable"
      ? "Unavailable"
      : status === "no_detection"
      ? "No detection"
      : "Normal";

  return (
    <section className="rounded-md border border-slate-800 bg-slate-950 p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-white">Fall Detection</h3>
          <p className="mt-1 text-xs text-slate-400">Live pose status from the backend stream.</p>
        </div>
        <span className={`rounded border px-2 py-1 text-xs font-semibold ${statusClass}`}>
          {label}
        </span>
      </div>

      {unavailable ? (
        <p className="mt-3 text-xs leading-5 text-slate-300">{unavailable}</p>
      ) : (
        <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
          <MetricMini label="Confidence" value={`${Math.round((summary?.top_confidence ?? 0) * 100)}%`} />
          <MetricMini label="People" value={`${summary?.person_count ?? 0}`} />
          <MetricMini label="Risk" value={`${summary?.fall_risk_count ?? 0}`} />
          <MetricMini label="Falls" value={`${summary?.fall_count ?? 0}`} />
        </div>
      )}

      {latestIncident ? (
        <div className="mt-3 rounded-md border border-red-400/30 bg-red-500/10 p-2 text-xs text-red-50">
          <p className="font-semibold">Persisted incident #{latestIncident.id}</p>
          <p className="mt-1 text-red-100/80">
            {latestIncident.severity ?? "HIGH"} - {Math.round((latestIncident.confidence ?? 0) * 100)}%
          </p>
          {latestIncident.snapshot_url ? (
            <img
              src={latestIncident.snapshot_url}
              alt={`Fall incident ${latestIncident.id}`}
              className="mt-2 max-h-32 w-full rounded object-cover"
            />
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function MetricMini({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-slate-800 bg-slate-900 px-2 py-1.5">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 font-semibold text-white">{value}</p>
    </div>
  );
}

function IncidentPanel() {
  const [events, setEvents] = useState<(ViolationReport | ZoneViolation | BehaviorIncident)[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadEvents = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setEvents(await getSafetyEvents());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load violations");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadEvents(), 0);
    return () => window.clearTimeout(timer);
  }, [loadEvents]);

  const deleteEvent = async (event: ViolationReport | ZoneViolation | BehaviorIncident) => {
    if (!event.id || !confirm("Delete this incident?")) return;
    if ("violation_type" in event) {
      await deleteViolation(event.id);
    } else if ("zone_type" in event) {
      await deleteZoneViolation(event.id);
    } else {
      return;
    }
    setEvents((current) => current.filter((item) => item.id !== event.id));
  };

  const deleteAllEvents = async () => {
    if (!confirm("Are you sure you want to delete all logged incidents? This action cannot be undone.")) return;
    setLoading(true);
    setError("");
    try {
      await deleteAllIncidents();
      setEvents([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete incidents");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="rounded-md border border-slate-200 bg-slate-50 p-3 shadow-sm">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-950">Recent Incidents</h2>
          <p className="text-xs text-slate-500">Loaded from PPE, zone, and behavior incident stores.</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void deleteAllEvents()}
            disabled={events.length === 0 || loading}
            className="flex items-center gap-1.5 rounded-md border border-red-200 bg-red-50 px-2.5 py-1.5 text-xs font-semibold text-red-700 transition hover:bg-red-100 hover:border-red-300 disabled:opacity-50 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:border-slate-200 disabled:text-slate-400"
            type="button"
          >
            <Trash2 className="size-3.5" />
            <span>Delete All</span>
          </button>
          <button
            onClick={() => void loadEvents()}
            className="rounded-md border border-slate-200 bg-white p-2 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:text-slate-950"
            type="button"
          >
            <RefreshCw className="size-3.5" aria-hidden="true" />
          </button>
        </div>
      </div>
      {loading ? <LoadingState text="Loading recent incidents..." /> : null}
      {error ? <ErrorState text={error} /> : null}
      {!loading && !error && events.length === 0 ? (
        <EmptyState text="No incidents have been recorded yet." />
      ) : null}
      <div className="grid gap-2">
        {events.slice(0, 6).map((event, index) => (
          <IncidentCard
            key={`${event.id ?? index}-${event.timestamp}`}
            event={event}
            onDelete={"behavior_type" in event ? undefined : () => void deleteEvent(event)}
          />
        ))}
      </div>
    </section>
  );
}

export function DashboardShell() {
  const [activeView, setActiveView] = useState<DashboardView>("feeds");
  const pageTitle =
    activeView === "violations"
      ? "Incident Log"
      : activeView === "factory3d"
        ? "Factory 3D Map"
        : "Packaging Line 1";
  const pageDescription =
    activeView === "violations"
      ? "Review PPE, zone, and behavior incidents recorded by the backend stores."
      : activeView === "factory3d"
        ? "Explore the factory blueprint in 3D and drill into a zone's incident log."
        : "Upload a camera simulation file, choose which detection models are enabled, and review the model outputs in one place.";

  return (
    <div className="min-h-screen bg-slate-100 text-slate-950">
      <TopBar activeView={activeView} onViewChange={setActiveView} />
      <div className="lg:flex">
        <ZoneSidebar />
        <main className="min-w-0 flex-1 p-3 lg:p-4">
          <div className="flex w-full flex-col gap-4">
            <section className="flex flex-col justify-between gap-4 rounded-md border border-slate-200 bg-white p-4 shadow-sm md:flex-row md:items-center">
              <div>
                <div className="flex items-center gap-2">
                  <span className="size-2 rounded-full bg-emerald-500" />
                  <p className="text-sm font-medium text-slate-600">Live monitoring active</p>
                </div>
                <h2 className="mt-2 text-2xl font-semibold tracking-normal text-slate-950 md:text-3xl">
                  {pageTitle}
                </h2>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
                  {pageDescription}
                </p>
              </div>
              <div className="grid grid-cols-2 gap-3 text-sm sm:flex">
                <StatusPill label="Shift" value="Night B" />
                <StatusPill label="Supervisor" value="Nguyen T." />
                <StatusPill label="Model" value="Safety v4.2" />
              </div>
            </section>

            <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              {safetyMetrics.map((metric) => (
                <MetricCard key={metric.label} metric={metric} />
              ))}
            </section>

            <div className="grid items-start gap-4">
              <div className="grid h-fit gap-4">
                {activeView === "violations" ? <IncidentPanel /> : null}
                {activeView === "factory3d" ? <Factory3DView /> : null}
                {/* CameraPanel stays mounted (only hidden) when on other tabs so
                    its WebSocket keeps streaming instead of disconnecting on tab switch. */}
                <div className={activeView === "feeds" ? "grid gap-4" : "hidden"}>
                  <CameraPanel />
                </div>
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

function StatusPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 font-semibold text-slate-950">{value}</p>
    </div>
  );
}
