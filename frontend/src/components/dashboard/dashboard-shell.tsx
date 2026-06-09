"use client";

import {
  ArrowUpRight,
  CheckCircle2,
  ChevronDown,
  Factory,
  Maximize2,
  MoreHorizontal,
  RefreshCw,
  Shield,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  analyzeImage,
  analyzeVideo,
  deleteZonesForVideo,
  deleteViolation,
  deleteZoneViolation,
  getSafetyEvents,
  getZones,
  saveZone,
} from "@/lib/ppe-api";
import { BoundingBoxView } from "@/components/ppe/bounding-box-view";
import { FileUpload } from "@/components/ppe/file-upload";
import { VideoTrackingOverlay } from "@/components/ppe/video-tracking-overlay";
import {
  DetectionSummary,
  EmptyState,
  ErrorState,
  IncidentCard,
  LoadingState,
  PeopleResults,
} from "@/components/ppe/result-panels";
import {
  DetectionResponse,
  VideoProcessingResponse,
  ViolationReport,
} from "@/types/detection";
import { Point2D, ZoneConfiguration, ZoneType, ZoneViolation } from "@/types/zone";
import {
  appActions,
  navigation,
  safetyMetrics,
  safetyRules,
  zones,
  type SafetyMetric,
  type SafetyRule,
  type Zone,
} from "./data";

const metricTone: Record<SafetyMetric["tone"], string> = {
  green: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  amber: "bg-amber-50 text-amber-700 ring-amber-200",
  blue: "bg-sky-50 text-sky-700 ring-sky-200",
  slate: "bg-slate-100 text-slate-700 ring-slate-200",
};

const zoneStatus = {
  online: "bg-emerald-500",
  warning: "bg-amber-500",
  standby: "bg-slate-400",
};

type AnalysisPhase = "idle" | "loading" | "done" | "error";
type DashboardView = "feeds" | "violations" | "audit";
type DraftZone = {
  id: string;
  name: string;
  type: ZoneType;
  dwellThresholdSeconds: number;
  points: Point2D[];
};

const navViewByLabel: Record<string, DashboardView> = {
  "Camera Feeds": "feeds",
  "Violations Log": "violations",
  "Safety Audit": "audit",
};

const zoneColors: Record<ZoneType, string> = {
  RESTRICTED: "#dc2626",
  WALKWAY: "#0284c7",
};

function IconButton({
  label,
  icon: Icon,
}: {
  label: string;
  icon: LucideIcon;
}) {
  return (
    <button
      className="inline-flex size-10 items-center justify-center rounded-md text-slate-300 transition hover:bg-white/10 hover:text-white focus:outline-none focus:ring-2 focus:ring-lime-200"
      type="button"
      aria-label={label}
      title={label}
    >
      <Icon className="size-4" aria-hidden="true" />
    </button>
  );
}

function TopBar({
  activeView,
  onViewChange,
}: {
  activeView: DashboardView;
  onViewChange: (view: DashboardView) => void;
}) {
  return (
    <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-slate-800 bg-slate-950 px-4 text-white shadow-sm lg:px-6">
      <div className="flex min-w-0 items-center gap-3">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-md bg-lime-200 text-green-950">
          <Factory className="size-5" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold uppercase tracking-wide text-lime-200">
            De Heus LLC
          </p>
          <h1 className="truncate text-base font-semibold text-slate-50 sm:text-lg">
            Smart Factory Safety Monitoring
          </h1>
        </div>
      </div>

      <nav className="hidden h-full items-center gap-1 lg:flex" aria-label="Main">
        {navigation.map((item) => (
          <button
            key={item.label}
            onClick={() => onViewChange(navViewByLabel[item.label] ?? "feeds")}
            className={`flex h-full items-center gap-2 border-b-2 px-4 text-xs font-semibold uppercase tracking-wide transition ${
              activeView === (navViewByLabel[item.label] ?? "feeds")
                ? "border-lime-200 text-lime-200"
                : "border-transparent text-slate-400 hover:text-slate-100"
            }`}
            type="button"
          >
            <item.icon className="size-4" aria-hidden="true" />
            {item.label}
          </button>
        ))}
      </nav>

      <div className="flex items-center gap-2">
        <div className="hidden items-center gap-1 md:flex">
          {appActions.map((action) => (
            <IconButton key={action.label} label={action.label} icon={action.icon} />
          ))}
        </div>
        <button
          className="flex items-center gap-2 rounded-md border border-white/10 bg-white/5 py-1.5 pl-1.5 pr-2 text-sm text-slate-100 transition hover:bg-white/10"
          type="button"
        >
          <span className="flex size-7 items-center justify-center rounded bg-lime-200 text-xs font-bold text-green-950">
            DH
          </span>
          <ChevronDown className="size-4 text-slate-400" aria-hidden="true" />
        </button>
      </div>
    </header>
  );
}

function ZoneSidebar() {
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
          {zones.map((zone) => (
            <ZoneButton key={zone.name} zone={zone} active={zone.name === "Packaging Line 1"} />
          ))}
        </div>

        <div className="mt-auto rounded-md border border-slate-200 bg-white p-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            System Health
          </p>
          <div className="mt-3 flex items-center justify-between">
            <span className="text-2xl font-semibold text-slate-950">99.7%</span>
            <span className="rounded bg-emerald-50 px-2 py-1 text-xs font-semibold text-emerald-700 ring-1 ring-emerald-200">
              Operational
            </span>
          </div>
          <p className="mt-2 text-sm text-slate-600">
            Edge inference cluster is processing normally.
          </p>
        </div>
      </div>
    </aside>
  );
}

function ZoneButton({ zone, active }: { zone: Zone; active: boolean }) {
  return (
    <button
      className={`group flex items-center justify-between rounded-md border px-3 py-3 text-left transition ${
        active
          ? "border-lime-300 bg-lime-100/70 shadow-sm"
          : "border-transparent bg-transparent hover:border-slate-200 hover:bg-white"
      }`}
      type="button"
    >
      <span className="flex min-w-0 items-center gap-3">
        <span className={`size-2.5 rounded-full ${zoneStatus[zone.status]}`} />
        <span className="min-w-0">
          <span
            className={`block truncate text-sm font-semibold ${
              active ? "text-green-950" : "text-slate-700"
            }`}
          >
            {zone.name}
          </span>
          <span className="text-xs text-slate-500">{zone.cameraCount} cameras</span>
        </span>
      </span>
      {active ? <ArrowUpRight className="size-4 text-green-900" aria-hidden="true" /> : null}
    </button>
  );
}

function MetricCard({ metric }: { metric: SafetyMetric }) {
  return (
    <section className="rounded-md border border-slate-200 bg-white p-3 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-slate-500">{metric.label}</p>
          <p className="mt-2 text-2xl font-semibold text-slate-950">{metric.value}</p>
        </div>
        <div className={`rounded-md p-2 ring-1 ${metricTone[metric.tone]}`}>
          <metric.icon className="size-5" aria-hidden="true" />
        </div>
      </div>
      <p className="mt-2 text-sm text-slate-600">{metric.helper}</p>
      <p className="mt-1 text-xs font-semibold uppercase tracking-wide text-emerald-700">
        {metric.trend}
      </p>
    </section>
  );
}

function CameraPanel() {
  const [file, setFile] = useState<File | null>(null);
  const [imageResult, setImageResult] = useState<DetectionResponse | null>(null);
  const [videoResult, setVideoResult] = useState<VideoProcessingResponse | null>(null);
  const [phase, setPhase] = useState<AnalysisPhase>("idle");
  const [error, setError] = useState("");
  const [ppeEnabled, setPpeEnabled] = useState(true);
  const [zoneEnabled, setZoneEnabled] = useState(false);
  const [zonesForVideo, setZonesForVideo] = useState<DraftZone[]>([]);
  const [draftPoints, setDraftPoints] = useState<Point2D[]>([]);
  const [zoneName, setZoneName] = useState("Restricted Area");
  const [zoneType, setZoneType] = useState<ZoneType>("RESTRICTED");
  const [dwellThresholdSeconds, setDwellThresholdSeconds] = useState(0);
  const [status, setStatus] = useState("");
  const [surfaceElement, setSurfaceElement] = useState<HTMLDivElement | null>(null);
  const videoUrl = useMemo(
    () => (file?.type.startsWith("video/") ? URL.createObjectURL(file) : ""),
    [file],
  );
  const currentIncidents = useMemo(
    () => [
      ...(ppeEnabled ? (videoResult?.reports ?? []) : []),
      ...(zoneEnabled ? (videoResult?.zone_violations ?? []) : []),
    ],
    [ppeEnabled, videoResult, zoneEnabled],
  );
  const isVideo = !!file?.type.startsWith("video/");
  const zonesReadyToSave = useMemo(
    () =>
      draftPoints.length >= 3
        ? [
            ...zonesForVideo,
            {
              id: "draft",
              name: zoneName || `${zoneType} Zone`,
              type: zoneType,
              dwellThresholdSeconds,
              points: draftPoints,
            },
          ]
        : zonesForVideo,
    [draftPoints, dwellThresholdSeconds, zoneName, zoneType, zonesForVideo],
  );
  const displayedZones = useMemo(
    () =>
      draftPoints.length > 0
        ? [
            ...zonesForVideo,
            {
              id: "draft",
              name: zoneName || "Draft zone",
              type: zoneType,
              dwellThresholdSeconds,
              points: draftPoints,
            },
          ]
        : zonesForVideo,
    [draftPoints, dwellThresholdSeconds, zoneName, zoneType, zonesForVideo],
  );

  useEffect(() => {
    if (!videoUrl) return;
    return () => URL.revokeObjectURL(videoUrl);
  }, [videoUrl]);

  const loadSavedZones = async (videoName: string) => {
    try {
      const savedZones = await getZones(videoName);
      setZonesForVideo(savedZones.map(zoneFromBackend));
      setStatus(
        savedZones.length > 0
          ? `Loaded ${savedZones.length} saved zone(s) for this camera video.`
          : "",
      );
    } catch {
      setStatus("");
    }
  };

  const selectFile = (nextFile: File) => {
    setFile(nextFile);
    setImageResult(null);
    setVideoResult(null);
    setError("");
    setStatus("");
    setZonesForVideo([]);
    setDraftPoints([]);
    setPhase("idle");
    if (nextFile.type.startsWith("video/")) {
      void loadSavedZones(nextFile.name);
    }
  };

  const addZonePoint = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!surfaceElement || phase === "loading" || !zoneEnabled) return;
    const rect = surfaceElement.getBoundingClientRect();
    setDraftPoints((current) => [
      ...current,
      {
        x: clamp01((event.clientX - rect.left) / rect.width),
        y: clamp01((event.clientY - rect.top) / rect.height),
      },
    ]);
  };

  const finishZone = () => {
    if (draftPoints.length < 3) return;
    setZonesForVideo((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        name: zoneName || `${zoneType} Zone`,
        type: zoneType,
        dwellThresholdSeconds,
        points: draftPoints,
      },
    ]);
    setDraftPoints([]);
  };

  const persistZones = async (zonesToPersist = zonesReadyToSave) => {
    if (!file || !isVideo) return;
    await deleteZonesForVideo(file.name);
    for (const zone of zonesToPersist) {
      await saveZone(toBackendZone(zone, file.name));
    }
    setZonesForVideo(
      zonesToPersist.map((zone) => ({
        ...zone,
        id: zone.id === "draft" ? crypto.randomUUID() : zone.id,
      })),
    );
    setDraftPoints([]);
    setStatus(`Saved ${zonesToPersist.length} zone(s) for this camera video.`);
  };

  const clearSavedZones = async () => {
    if (!file || !isVideo) return;
    setPhase("loading");
    setError("");
    try {
      await deleteZonesForVideo(file.name);
      setZonesForVideo([]);
      setDraftPoints([]);
      setStatus("Cleared saved zones for this camera video.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not clear saved zones");
    } finally {
      setPhase("idle");
    }
  };

  const runSelectedModels = async () => {
    if (!file) return;
    if (!ppeEnabled && !zoneEnabled) {
      setError("Enable at least one detection model before running analysis.");
      setPhase("error");
      return;
    }
    if (!isVideo && !ppeEnabled) {
      setError("Image uploads only support PPE detection.");
      setPhase("error");
      return;
    }
    if (zoneEnabled && isVideo && zonesReadyToSave.length === 0) {
      setError("Draw or load at least one zone before running Zone Monitoring.");
      setPhase("error");
      return;
    }

    setImageResult(null);
    setVideoResult(null);
    setError("");
    setPhase("loading");
    try {
      if (isVideo) {
        if (zoneEnabled) {
          await persistZones(zonesReadyToSave);
        }
        setVideoResult(
          await analyzeVideo(file, {
            enablePpe: ppeEnabled,
            enableZone: zoneEnabled,
          }),
        );
      } else {
        setImageResult(await analyzeImage(file));
      }
      setPhase("done");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not analyze the uploaded file");
      setPhase("error");
    }
  };

  const reset = () => {
    setFile(null);
    setImageResult(null);
    setVideoResult(null);
    setError("");
    setStatus("");
    setDraftPoints([]);
    setPhase("idle");
  };

  return (
    <section className="h-fit overflow-hidden rounded-md border border-slate-300 bg-slate-950 shadow-md">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
        <div>
          <p className="text-sm font-semibold text-white">Packaging Line 1 - Uploaded Feed</p>
          <p className="text-xs text-slate-400">
            Upload a photo or CCTV clip, then choose which detection models run on this camera
          </p>
        </div>
        <div className="flex items-center gap-2">
          <IconButton label="Fullscreen camera feed" icon={Maximize2} />
        </div>
      </div>

      <div className="grid gap-4 p-4">
        {!file ? (
          <FileUpload
            label="Upload camera image or video"
            helper="This replaces the live stream for now. Select model detections after the file is loaded."
            onFiles={(files) => selectFile(files[0])}
          />
        ) : null}

        {file ? (
          <div className="grid gap-4 rounded-md border border-slate-800 bg-slate-900 p-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-white">{file.name}</p>
                <p className="text-xs text-slate-400">
                  {isVideo ? "Video feed simulation" : "Image frame simulation"}
                </p>
              </div>
              <button
                type="button"
                onClick={reset}
                className="rounded-md border border-slate-700 px-3 py-2 text-sm font-semibold text-slate-200 transition hover:bg-white/10"
              >
                Replace file
              </button>
            </div>

            <div className="grid gap-2 sm:grid-cols-2">
              <ModelToggle
                label="PPE Detection"
                description="Helmet and vest compliance"
                enabled={ppeEnabled}
                onToggle={() => setPpeEnabled((current) => !current)}
              />
              <ModelToggle
                label="Zone Monitoring"
                description="Restricted and walkway zones"
                enabled={zoneEnabled && isVideo}
                disabled={!isVideo}
                onToggle={() => setZoneEnabled((current) => !current)}
              />
            </div>

            {isVideo && videoUrl ? (
              <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_340px]">
                <div>
                  <div
                    ref={setSurfaceElement}
                    onClick={addZonePoint}
                    className="relative aspect-video overflow-hidden rounded-md border border-slate-800 bg-black"
                  >
                    <video
                      src={videoUrl}
                      controls
                      muted
                      playsInline
                      className="absolute inset-0 h-full w-full object-contain"
                    />
                    {zoneEnabled ? (
                      <svg className="absolute inset-0 h-full w-full" viewBox="0 0 1 1" preserveAspectRatio="none">
                        {displayedZones.map((zone) => (
                          <polygon
                            key={zone.id}
                            points={zone.points.map((point) => `${point.x},${point.y}`).join(" ")}
                            fill={`${zoneColors[zone.type]}33`}
                            stroke={zoneColors[zone.type]}
                            strokeWidth={0.004}
                          />
                        ))}
                        {draftPoints.map((point, index) => (
                          <circle
                            key={`${point.x}-${point.y}-${index}`}
                            cx={point.x}
                            cy={point.y}
                            r={0.008}
                            fill="#bef264"
                          />
                        ))}
                      </svg>
                    ) : null}
                  </div>
                  {zoneEnabled ? (
                    <p className="mt-2 text-xs text-slate-400">
                      Click the video frame to add zone polygon points before running Zone Monitoring.
                    </p>
                  ) : null}
                </div>

                <aside className="grid content-start gap-3 rounded-md border border-slate-800 bg-slate-950 p-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                    Camera Model Settings
                  </p>
                  {zoneEnabled ? (
                    <>
                      <label className="grid gap-1 text-sm font-semibold text-slate-200">
                        Zone name
                        <input
                          value={zoneName}
                          onChange={(event) => setZoneName(event.target.value)}
                          className="rounded-md border border-slate-700 bg-slate-900 px-3 py-2 font-normal text-white outline-none focus:border-lime-200"
                        />
                      </label>
                      <label className="grid gap-1 text-sm font-semibold text-slate-200">
                        Zone type
                        <select
                          value={zoneType}
                          onChange={(event) => setZoneType(event.target.value as ZoneType)}
                          className="rounded-md border border-slate-700 bg-slate-900 px-3 py-2 font-normal text-white outline-none focus:border-lime-200"
                        >
                          <option value="RESTRICTED">Restricted</option>
                          <option value="WALKWAY">Walkway</option>
                        </select>
                      </label>
                      <label className="grid gap-1 text-sm font-semibold text-slate-200">
                        Dwell threshold seconds
                        <input
                          type="number"
                          min={0}
                          step={1}
                          value={dwellThresholdSeconds}
                          onChange={(event) =>
                            setDwellThresholdSeconds(
                              Math.max(0, Math.round(Number(event.target.value) || 0)),
                            )
                          }
                          className="rounded-md border border-slate-700 bg-slate-900 px-3 py-2 font-normal text-white outline-none focus:border-lime-200"
                        />
                      </label>
                      <div className="grid grid-cols-2 gap-2">
                        <button
                          type="button"
                          onClick={finishZone}
                          disabled={draftPoints.length < 3 || phase === "loading"}
                          className="rounded-md bg-lime-200 px-3 py-2 text-sm font-semibold text-green-950 disabled:opacity-50"
                        >
                          Finish zone
                        </button>
                        <button
                          type="button"
                          onClick={() => setDraftPoints([])}
                          disabled={phase === "loading"}
                          className="rounded-md border border-slate-700 px-3 py-2 text-sm font-semibold text-slate-200 disabled:opacity-50"
                        >
                          Clear draft
                        </button>
                      </div>
                      <div className="grid grid-cols-2 gap-2">
                        <button
                          type="button"
                          onClick={() => void persistZones()}
                          disabled={phase === "loading"}
                          className="rounded-md border border-lime-200 px-3 py-2 text-sm font-semibold text-lime-200 disabled:opacity-50"
                        >
                          Save zones
                        </button>
                        <button
                          type="button"
                          onClick={() => void clearSavedZones()}
                          disabled={phase === "loading"}
                          className="rounded-md border border-red-400 px-3 py-2 text-sm font-semibold text-red-200 disabled:opacity-50"
                        >
                          Clear zones
                        </button>
                      </div>
                      <div className="rounded-md bg-slate-900 p-3 text-sm text-slate-300">
                        Saved zones: {zonesForVideo.length}
                        <br />
                        Draft points: {draftPoints.length}
                      </div>
                    </>
                  ) : (
                    <EmptyState text="Enable Zone Monitoring to draw or load camera zones." />
                  )}
                </aside>
              </div>
            ) : null}

            <button
              type="button"
              onClick={() => void runSelectedModels()}
              disabled={phase === "loading"}
              className="w-fit rounded-md bg-lime-200 px-4 py-2 text-sm font-semibold text-green-950 transition hover:bg-lime-100 disabled:opacity-50"
            >
              {phase === "loading" ? "Running selected models..." : "Run selected models"}
            </button>
          </div>
        ) : null}

        {phase === "loading" ? <LoadingState text="Running PPE inference..." /> : null}
        {phase === "error" ? <ErrorState text={error} /> : null}
        {status ? <EmptyState text={status} /> : null}

        {phase === "done" && file && imageResult ? (
          <div className="grid gap-4">
            <DetectionSummary result={imageResult} />
            <BoundingBoxView file={file} detections={imageResult.detections} />
            <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
              <PeopleResults persons={imageResult.persons} />
            </div>
          </div>
        ) : null}

        {phase === "done" && videoResult ? (
          <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.45fr)_360px]">
            <div className="grid h-fit content-start gap-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-lime-200">
                    Analysis Result
                  </p>
                  <h3 className="mt-1 text-base font-semibold text-white">
                    {videoResult.summary.video_name}
                  </h3>
                </div>
                <span
                  className={`rounded px-2 py-1 text-xs font-semibold ring-1 ${
                    currentIncidents.length > 0
                      ? "bg-red-500/10 text-red-200 ring-red-400/30"
                      : "bg-emerald-500/10 text-emerald-200 ring-emerald-400/30"
                  }`}
                >
                  {currentIncidents.length > 0 ? `${currentIncidents.length} incident` : "No incidents"}
                </span>
              </div>

              {videoUrl ? (
              <VideoTrackingOverlay
                src={videoUrl}
                overlay={ppeEnabled || zoneEnabled ? videoResult.tracking_overlay : undefined}
              />
              ) : null}
            </div>

            <aside className="grid h-fit content-start gap-3 rounded-md border border-slate-800 bg-slate-900 p-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                  Processing Summary
                </p>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <CompactMetric
                    label="Frames"
                    value={`${videoResult.summary.processed_frames}/${videoResult.summary.total_frames}`}
                  />
                  <CompactMetric
                    label="Runtime"
                    value={`${(videoResult.summary.inference_ms / 1000).toFixed(1)}s`}
                  />
                  <CompactMetric
                    label="Models"
                    value={[ppeEnabled ? "PPE" : "", zoneEnabled ? "Zone" : ""]
                      .filter(Boolean)
                      .join(" + ")}
                  />
                  <CompactMetric
                    label="Incidents"
                    value={currentIncidents.length}
                    alert={currentIncidents.length > 0}
                  />
                </div>
              </div>

              <div className="border-t border-slate-800 pt-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                    Incident Evidence
                  </p>
                  <span className="text-xs text-slate-500">{currentIncidents.length}</span>
                </div>
                {currentIncidents.length > 0 ? (
                  <div className="grid max-h-[520px] gap-2 overflow-auto pr-1">
                    {currentIncidents.map((event, index) => (
                      <IncidentCard
                        key={`${event.id ?? index}-${event.timestamp}`}
                        event={event}
                        compact
                      />
                    ))}
                  </div>
                ) : (
                  <EmptyState text="No confirmed incidents were detected in this video." />
                )}
              </div>

              <button
                type="button"
                onClick={() => void runSelectedModels()}
                className="rounded-md border border-white/10 px-3 py-2 text-sm font-semibold text-slate-200 transition hover:bg-white/10"
              >
                Rerun selected models
              </button>
            </aside>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function CompactMetric({
  label,
  value,
  alert = false,
}: {
  label: string;
  value: string | number;
  alert?: boolean;
}) {
  return (
    <div className="rounded-md border border-slate-800 bg-slate-950 px-3 py-2">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-1 truncate text-sm font-semibold ${alert ? "text-red-300" : "text-lime-200"}`}>
        {value}
      </p>
    </div>
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

function zoneFromBackend(zone: ZoneConfiguration): DraftZone {
  return {
    id: String(zone.id ?? crypto.randomUUID()),
    name: zone.zone_name,
    type: zone.zone_type,
    dwellThresholdSeconds: zone.dwell_threshold_seconds,
    points: safeParsePoints(zone.flattened_coordinates),
  };
}

function toBackendZone(zone: DraftZone, videoName: string): ZoneConfiguration {
  return {
    video_name: videoName,
    zone_name: zone.name,
    zone_type: zone.type,
    dwell_threshold_seconds: zone.dwellThresholdSeconds,
    is_active: true,
    ui_shape_data: JSON.stringify({
      shape: "polygon",
      name: zone.name,
      type: zone.type,
      dwellThresholdSeconds: zone.dwellThresholdSeconds,
      points: zone.points,
    }),
    flattened_coordinates: JSON.stringify(zone.points),
  };
}

function safeParsePoints(value: string): Point2D[] {
  try {
    const parsed = JSON.parse(value) as Point2D[];
    return Array.isArray(parsed)
      ? parsed.filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y))
      : [];
  } catch {
    return [];
  }
}

function clamp01(value: number) {
  return Math.max(0, Math.min(1, value));
}

function RuleCard({ rule }: { rule: SafetyRule }) {
  const standby = rule.status === "Standing By";

  return (
    <article className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="flex size-10 items-center justify-center rounded-md bg-slate-100 text-slate-700">
          <rule.icon className="size-5" aria-hidden="true" />
        </div>
        <span
          className={`rounded px-2 py-1 text-xs font-semibold ring-1 ${
            standby
              ? "bg-sky-50 text-sky-700 ring-sky-200"
              : "bg-emerald-50 text-emerald-700 ring-emerald-200"
          }`}
        >
          {rule.status}
        </span>
      </div>
      <h3 className="mt-4 text-sm font-semibold text-slate-950">{rule.title}</h3>
      <p className="mt-2 text-sm leading-6 text-slate-600">{rule.description}</p>
      <div className="mt-4">
        <div className="flex items-center justify-between text-xs font-medium text-slate-500">
          <span>Confidence</span>
          <span>{rule.confidence}%</span>
        </div>
        <div className="mt-2 h-2 rounded-full bg-slate-100">
          <div
            className="h-2 rounded-full bg-green-900"
            style={{ width: `${rule.confidence}%` }}
          />
        </div>
      </div>
    </article>
  );
}

function RulesPanel() {
  return (
    <section className="rounded-md border border-slate-200 bg-slate-50 p-4 shadow-sm">
      <div className="mb-4 flex items-center justify-between gap-4 border-b border-slate-200 pb-3">
        <div className="flex items-center gap-2">
          <CheckCircle2 className="size-5 text-green-900" aria-hidden="true" />
          <h2 className="text-base font-semibold text-slate-950">Active Safety Rules</h2>
        </div>
        <button
          className="inline-flex size-9 items-center justify-center rounded-md text-slate-500 transition hover:bg-white hover:text-slate-950"
          type="button"
          aria-label="More safety rule actions"
          title="More safety rule actions"
        >
          <MoreHorizontal className="size-5" aria-hidden="true" />
        </button>
      </div>
      <div className="grid gap-3 md:grid-cols-3 lg:grid-cols-1 xl:grid-cols-3">
        {safetyRules.map((rule) => (
          <RuleCard key={rule.title} rule={rule} />
        ))}
      </div>
    </section>
  );
}

function IncidentPanel() {
  const [events, setEvents] = useState<(ViolationReport | ZoneViolation)[]>([]);
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

  const deleteEvent = async (event: ViolationReport | ZoneViolation) => {
    if (!event.id || !confirm("Delete this incident?")) return;
    if ("violation_type" in event) {
      await deleteViolation(event.id);
    } else {
      await deleteZoneViolation(event.id);
    }
    setEvents((current) => current.filter((item) => item.id !== event.id));
  };

  return (
    <section className="rounded-md border border-slate-200 bg-slate-50 p-3 shadow-sm">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-950">Recent Violations</h2>
          <p className="text-xs text-slate-500">Loaded from the PPE backend incident store.</p>
        </div>
        <button
          onClick={() => void loadEvents()}
          className="rounded-md border border-slate-200 bg-white p-2 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:text-slate-950"
          type="button"
        >
          <RefreshCw className="size-3.5" aria-hidden="true" />
        </button>
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
            onDelete={() => void deleteEvent(event)}
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
        ? "Violations Log"
        : activeView === "audit"
          ? "Safety Audit"
          : "Packaging Line 1";
  const pageDescription =
    activeView === "violations"
        ? "Review PPE and zone incidents recorded by the backend incident store."
        : activeView === "audit"
          ? "Review active computer vision safety rules and monitoring confidence."
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

            <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.8fr)_minmax(360px,0.8fr)]">
              <div className="grid h-fit gap-4">
                {activeView === "audit" ? <RulesPanel /> : null}
                {activeView === "violations" ? <IncidentPanel /> : null}
                {activeView === "feeds" ? (
                  <>
                    <CameraPanel />
                    <RulesPanel />
                  </>
                ) : null}
              </div>
              <div className="grid content-start gap-4">
                {activeView !== "violations" ? <IncidentPanel /> : null}
                <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
                  <h2 className="text-base font-semibold text-slate-950">
                    Operational Notes
                  </h2>
                  <p className="mt-3 text-sm leading-6 text-slate-600">
                    Safety rules are running at the edge gateway. Escalations are routed
                    to line supervisors when confidence stays above threshold for 3
                    consecutive frames.
                  </p>
                </section>
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
