"use client";

import {
  ChevronLeft,
  ChevronRight,
  Loader2,
  Maximize2,
  RefreshCw,
  Settings,
  Trash2,
  Shield,
  Eye,
  Activity,
} from "lucide-react";
import dynamic from "next/dynamic";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createPhysicalZone,
  deleteAllIncidents,
  deleteCamera,
  deleteIncident,
  ensureCamera,
  getCameras,
  getPhysicalZones,
  getSafetyEvents,
  getZones,
  setCameraHomeZone,
  getCameraFeatures,
  updateCameraFeatures,
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
import { CONFIRM_DELETE_ALL_INCIDENTS, CONFIRM_DELETE_INCIDENT } from "@/lib/messages";
import { TrackingOverlay, ViolationReport } from "@/types/detection";
import { PhysicalZone, ZoneType, ZoneViolation } from "@/types/zone";
import { useDetectionUpload } from "@/hooks/useDetectionUpload";
import { useZoneDrawing } from "@/hooks/useZoneDrawing";
import { useLiveStream } from "@/hooks/useLiveStream";
import { useAutoZoneSuggestions } from "@/hooks/useAutoZoneSuggestions";
import { AnalysisPhase, DraftZone } from "@/hooks/camera-panel-types";
import { BehaviorIncident, FallLiveSummary } from "@/types/behavior";
import { useSafetyKpis } from "@/hooks/useSafetyKpis";
import { TopBar, type DashboardView } from "./top-bar";
import { ZoneSidebar } from "./zone-sidebar";
import { MetricCard } from "./metric-card";
import { IconButton } from "./icon-button";
import { ZoneOverlaySvg } from "./zone-overlay-svg";
import { ZoneConfigPanel } from "./zone-config-panel";
import { AnalysisResultPanel } from "./analysis-result-panel";
import { IncidentCategory, IncidentDetailModal } from "./incident-detail-modal";

const Factory3DView = dynamic(
  () => import("@/components/factory3d/factory-3d-view").then((m) => m.Factory3DView),
  { ssr: false },
);

const AnalyticsDashboard = dynamic(
  () => import("@/components/analytics/analytics-dashboard").then((m) => m.AnalyticsDashboard),
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

interface CameraConfig {
  id: number;
  name: string;
  rtspUrl: string;
  zoneId: string;
  /** physical_zones.id this camera belongs to, persisted server-side (null = Unassigned). */
  homeZoneId: number | null;
  active: boolean;
}

const DEFAULT_CAMERAS: CameraConfig[] = [
  { id: 1, name: "Production Area", rtspUrl: "rtsp://127.0.0.1:8554/stream1", zoneId: "Z01", homeZoneId: null, active: true },
  { id: 2, name: "Warehouse Intake", rtspUrl: "rtsp://127.0.0.1:8554/stream2", zoneId: "Z02", homeZoneId: null, active: false },
  { id: 3, name: "Packing Area", rtspUrl: "rtsp://127.0.0.1:8554/stream3", zoneId: "Z03", homeZoneId: null, active: false },
];

interface CameraPanelProps {
  cameras: CameraConfig[];
  activeCameraId: number;
  physicalZones: PhysicalZone[];
  onCameraChange: (id: number) => void;
  onCamerasUpdate: (updated: CameraConfig[]) => void;
  onPhysicalZonesUpdate: (updated: PhysicalZone[]) => void;
}

function CameraPanel({
  cameras,
  activeCameraId,
  physicalZones,
  onCameraChange,
  onCamerasUpdate,
  onPhysicalZonesUpdate,
}: CameraPanelProps) {
  const [phase, setPhase] = useState<AnalysisPhase>("idle");
  const [error, setError] = useState("");
  const [viewMode, setViewMode] = useState<"single" | "matrix">("single");
  const [selectedCameraIds, setSelectedCameraIds] = useState<number[]>([]);
  const [cameraZones, setCameraZones] = useState<Record<string, DraftZone[]>>({});

  useEffect(() => {
    const activeIds = cameras.filter((c) => c.active).map((c) => c.id);
    setSelectedCameraIds((prev) => {
      const filteredPrev = prev.filter((id) => activeIds.includes(id));
      const newActiveIds = activeIds.filter((id) => !prev.includes(id));
      return [...filteredPrev, ...newActiveIds];
    });
  }, [cameras]);
  const [status, setStatus] = useState("");
  const [ppeEnabled, setPpeEnabled] = useState(true);
  const [zoneEnabled, setZoneEnabled] = useState(false);
  const [fallEnabled, setFallEnabled] = useState(false);
  const [cameraFeatureMap, setCameraFeatureMap] = useState<Record<number, Record<string, boolean>>>({});

  const currentPpeEnabled = viewMode === "single"
    ? (cameraFeatureMap[activeCameraId]?.["ppe_detection"] ?? ppeEnabled)
    : ppeEnabled;

  const currentZoneEnabled = viewMode === "single"
    ? (cameraFeatureMap[activeCameraId]?.["zone_monitoring"] ?? zoneEnabled)
    : zoneEnabled;

  const currentFallEnabled = viewMode === "single"
    ? (cameraFeatureMap[activeCameraId]?.["fall_detection"] ?? fallEnabled)
    : fallEnabled;

  useEffect(() => {
    cameras.forEach(async (cam) => {
      if (cam.active && cameraFeatureMap[cam.id] === undefined) {
        // Set an empty dict first to prevent double fetches while fetching
        setCameraFeatureMap((prev) => ({ ...prev, [cam.id]: {} }));
        try {
          const configs = await getCameraFeatures(cam.id);
          const featureDict = configs.reduce((acc, c) => {
            acc[c.feature_key] = c.is_enabled;
            return acc;
          }, {} as Record<string, boolean>);
          setCameraFeatureMap((prev) => ({ ...prev, [cam.id]: featureDict }));
        } catch (err) {
          // Rollback empty dict so it can retry if needed
          setCameraFeatureMap((prev) => {
            const next = { ...prev };
            delete next[cam.id];
            return next;
          });
        }
      }
    });
  }, [cameras, cameraFeatureMap]);

  const handleToggleCameraFeature = async (cameraId: number, featureKey: string) => {
    const currentEnabled = cameraFeatureMap[cameraId]?.[featureKey] ?? (featureKey === "ppe_detection");
    const nextEnabled = !currentEnabled;

    // Optimistic UI update
    setCameraFeatureMap((prev) => {
      const current = prev[cameraId] || {};
      return {
        ...prev,
        [cameraId]: { ...current, [featureKey]: nextEnabled },
      };
    });

    try {
      await updateCameraFeatures(cameraId, [
        { feature_key: featureKey, is_enabled: nextEnabled },
      ]);
    } catch (err) {
      // Rollback optimistic update
      setCameraFeatureMap((prev) => {
        const current = prev[cameraId] || {};
        return {
          ...prev,
          [cameraId]: { ...current, [featureKey]: currentEnabled },
        };
      });
      alert(err instanceof Error ? err.message : "Failed to toggle feature");
    }
  };

  const upload = useDetectionUpload();

  const liveStream = useLiveStream({
    ppeEnabled,
    zoneEnabled,
    fallEnabled,
    setPhase,
    setError,
    setStatus,
    viewMode,
    selectedCameraIds,
    cameras,
    cameraFeatureMap,
  });

  const [isConfiguringCameras, setIsConfiguringCameras] = useState(false);
  const [tempCameras, setTempCameras] = useState<CameraConfig[]>([]);
  const [creatingZoneForIdx, setCreatingZoneForIdx] = useState<number | null>(null);
  const [newZoneName, setNewZoneName] = useState("");
  const [newZoneError, setNewZoneError] = useState("");

  const handleCreateZone = async (idx: number) => {
    const name = newZoneName.trim();
    if (!name) return;
    try {
      const zone = await createPhysicalZone(name);
      onPhysicalZonesUpdate([...physicalZones, zone]);
      const updated = [...tempCameras];
      updated[idx] = { ...updated[idx], homeZoneId: zone.id };
      setTempCameras(updated);
      setCreatingZoneForIdx(null);
      setNewZoneName("");
      setNewZoneError("");
    } catch (err) {
      setNewZoneError(err instanceof Error ? err.message : "Could not create zone");
    }
  };

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

  useEffect(() => {
    cameras.forEach(async (cam) => {
      if (cam.active && cam.rtspUrl && !cameraZones[cam.rtspUrl]) {
        try {
          const saved = await getZones(cam.rtspUrl);
          const parsed = saved.map(zoneFromBackend);
          setCameraZones((prev) => ({ ...prev, [cam.rtspUrl]: parsed }));
        } catch (err) {
          // ignore
        }
      }
    });
  }, [cameras, cameraZones]);

  useEffect(() => {
    if (liveStream.liveUrl) {
      setCameraZones((prev) => ({
        ...prev,
        [liveStream.liveUrl]: zoneDrawing.zonesForVideo,
      }));
    }
  }, [zoneDrawing.zonesForVideo, liveStream.liveUrl]);

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
    setZoneEnabled: (enabled) => {
      if (viewMode === "single" && activeCameraId) {
        void updateCameraFeatures(activeCameraId, [
          { feature_key: "zone_monitoring", is_enabled: enabled },
        ]);
        setCameraFeatureMap((prev) => {
          const current = prev[activeCameraId] || {};
          return {
            ...prev,
            [activeCameraId]: { ...current, zone_monitoring: enabled },
          };
        });
      } else {
        setZoneEnabled(enabled);
      }
    },
    setPpeEnabled: (enabled) => {
      if (viewMode === "single" && activeCameraId) {
        void updateCameraFeatures(activeCameraId, [
          { feature_key: "ppe_detection", is_enabled: enabled },
        ]);
        setCameraFeatureMap((prev) => {
          const current = prev[activeCameraId] || {};
          return {
            ...prev,
            [activeCameraId]: { ...current, ppe_detection: enabled },
          };
        });
      } else {
        setPpeEnabled(enabled);
      }
    },
    openModifyModeFor: (draft) => {
      zoneDrawing.setIsDrawing(true);
      zoneDrawing.setConfigMode("modify");
      zoneDrawing.setSelectedZoneId(draft.id);
      zoneDrawing.setZoneName(draft.name);
      zoneDrawing.setZoneType(draft.type);
    },
  });

  const handleCameraChange = async (cameraId: number) => {
    const targetCam = cameras.find((c) => c.id === cameraId);
    if (!targetCam) return;
    onCameraChange(cameraId);
    setError("");
    setPhase("loading");
    setStatus(`Switching to ${targetCam.name}...`);
    try {
      await zoneDrawing.loadSavedZones(targetCam.rtspUrl);
      liveStream.setViewedCamera(targetCam.rtspUrl);
      setPhase("idle");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load camera zones");
      setPhase("error");
    }
  };

  const handleSaveCameraConfig = async (updatedCameras: CameraConfig[]) => {
    // Identify cameras that were deleted
    const deletedCameras = cameras.filter((c) => !updatedCameras.some((uc) => uc.id === c.id));

    onCamerasUpdate(updatedCameras);
    localStorage.setItem("ppe_demo_cameras", JSON.stringify(updatedCameras));
    setIsConfiguringCameras(false);

    // Sync all camera connections to open/close sockets as needed
    liveStream.syncCameraConnections(updatedCameras);

    const currentCam = updatedCameras.find((c) => c.id === activeCameraId);
    if (currentCam) {
      if (!currentCam.active) {
        const firstActive = updatedCameras.find((c) => c.active) || updatedCameras[0];
        if (firstActive) {
          void handleCameraChange(firstActive.id);
        }
      } else {
        void handleCameraChange(activeCameraId);
      }
    } else if (updatedCameras.length > 0) {
      const firstActive = updatedCameras.find((c) => c.active) || updatedCameras[0];
      if (firstActive) {
        void handleCameraChange(firstActive.id);
      }
    }

    try {
      // First, handle deletions on the backend
      if (deletedCameras.length > 0) {
        const backendCameras = await getCameras();
        for (const cam of deletedCameras) {
          const match = backendCameras.find((bc) => bc.source_key === cam.rtspUrl);
          if (match && match.id) {
            await deleteCamera(match.id);
          }
        }
      }

      // Then save/ensure updated cameras
      await Promise.all(
        updatedCameras.map(async (cam) => {
          const backendCamera = await ensureCamera(cam.name, cam.rtspUrl);
          await setCameraHomeZone(backendCamera.id, cam.homeZoneId);
        }),
      );

      // Re-fetch backend cameras to sync any homeZoneId changes
      const latestBackendCameras = await getCameras();
      const reconciled = updatedCameras.map((cam) => {
        const match = latestBackendCameras.find((bc) => bc.source_key === cam.rtspUrl);
        return match ? { ...cam, id: match.id, homeZoneId: match.home_zone_id } : cam;
      });
      onCamerasUpdate(reconciled);
      localStorage.setItem("ppe_demo_cameras", JSON.stringify(reconciled));
      window.alert("Camera configuration saved successfully.");
    } catch (err) {
      setError(
        err instanceof Error
          ? `Could not sync camera configuration: ${err.message}`
          : "Could not sync camera configuration",
      );
    }
  };

  const initializedRef = useRef(false);
  const prevActiveCameraIdRef = useRef<number | null>(null);

  // Synchronize stream selection on external activeCameraId change (e.g. from 3D map)
  useEffect(() => {
    if (
      prevActiveCameraIdRef.current !== null &&
      prevActiveCameraIdRef.current !== activeCameraId
    ) {
      void handleCameraChange(activeCameraId);
    }
    prevActiveCameraIdRef.current = activeCameraId;
  }, [activeCameraId]);

  // Connect to all active cameras on initial load and set the viewed one
  useEffect(() => {
    if (cameras.length === 0 || initializedRef.current) return;
    initializedRef.current = true;
    const active = cameras.find((c) => c.id === activeCameraId) || cameras[0];
    prevActiveCameraIdRef.current = active.id;
    
    // Open WebSockets for all active cameras
    liveStream.syncCameraConnections(cameras);
    
    // Focus the view on the current active camera
    liveStream.setViewedCamera(active.rtspUrl);
    void zoneDrawing.loadSavedZones(active.rtspUrl);
  }, [cameras]);

  const currentIncidents = useMemo(
    () => {
      const reports = liveStream.isStreaming || liveStream.streamData.summary
        ? liveStream.streamData.reports
        : (upload.videoResult?.reports ?? []);
      const zoneViolations = liveStream.isStreaming || liveStream.streamData.summary
        ? liveStream.streamData.zone_violations
        : (upload.videoResult?.zone_violations ?? []);

      return [
        ...(currentPpeEnabled ? reports : []),
        ...(currentZoneEnabled ? zoneViolations : []),
        ...(currentFallEnabled ? liveStream.streamData.behavior_incidents : []),
      ];
    },
    [
      liveStream.isStreaming,
      currentPpeEnabled,
      liveStream.streamData.reports,
      liveStream.streamData.summary,
      liveStream.streamData.zone_violations,
      liveStream.streamData.behavior_incidents,
      upload.videoResult?.reports,
      upload.videoResult?.zone_violations,
      currentFallEnabled,
      currentZoneEnabled,
    ],
  );
  const visibleTrackingOverlay = useMemo(
    () =>
      filterTrackingOverlay(
        liveStream.isStreaming || liveStream.streamData.summary
          ? liveStream.streamData.tracking_overlay
          : upload.videoResult?.tracking_overlay,
        {
          showPpe: currentPpeEnabled,
          showZone: currentZoneEnabled,
        },
      ),
    [
      liveStream.isStreaming,
      currentPpeEnabled,
      liveStream.streamData.summary,
      liveStream.streamData.tracking_overlay,
      upload.videoResult?.tracking_overlay,
      currentZoneEnabled,
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
    if (!currentPpeEnabled && !currentZoneEnabled && !currentFallEnabled) {
      setError("Enable at least one detection model before running analysis.");
      setPhase("error");
      return;
    }
    if (!liveStream.isLive && !isVideo && !currentPpeEnabled) {
      setError("Image uploads only support PPE detection in this dashboard.");
      setPhase("error");
      return;
    }

    if (currentZoneEnabled && (liveStream.isLive || isVideo)) {
      let hasOverlap = false;
      for (let i = 0; i < zoneDrawing.zonesReadyToSave.length; i++) {
        for (let j = i + 1; j < zoneDrawing.zonesReadyToSave.length; j++) {
          const typeA = zoneDrawing.zonesReadyToSave[i].type;
          const typeB = zoneDrawing.zonesReadyToSave[j].type;
          const isWalkwaySlipperyPair =
             (typeA === "WALKWAY" && typeB === "SLIPPERY") ||
             (typeA === "SLIPPERY" && typeB === "WALKWAY");

          if (
            typeA !== typeB &&
            !isWalkwaySlipperyPair &&
            doPolygonsOverlap(zoneDrawing.zonesReadyToSave[i].points, zoneDrawing.zonesReadyToSave[j].points)
          ) {
            hasOverlap = true;
            break;
          }
        }
        if (hasOverlap) break;
      }

      if (hasOverlap) {
        setError("Cannot run analysis: Zones of different types (excluding Walkway & Slippery) overlap. Please adjust the vertices to avoid overlap.");
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
        if (currentZoneEnabled && zoneDrawing.zonesReadyToSave.length > 0) {
          await zoneDrawing.persistZones(zoneDrawing.zonesReadyToSave);
        }
        await liveStream.startStreaming(liveStream.liveUrl, true);
      } else if (isVideo) {
        if (currentZoneEnabled && zoneDrawing.zonesReadyToSave.length > 0) {
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

  const connectedCameraNames = cameras
    .filter((c) => c.active && liveStream.liveFrames[c.rtspUrl])
    .map((c) => c.name);

  const viewedCameraName = cameras.find((c) => c.rtspUrl === liveStream.liveUrl)?.name;

  const displayedCameraNames = viewMode === "single"
    ? (viewedCameraName ? [viewedCameraName] : [])
    : connectedCameraNames;

  return (
    <section className="h-fit overflow-hidden rounded-md border border-slate-300 bg-slate-950 shadow-md">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
        <div>
          <p className="text-sm font-semibold text-white">
            {liveStream.isLive ? "Live Camera Feed" : "Packaging Line 1 - Uploaded Feed"}
          </p>
          <p className="text-xs text-slate-400">
            {liveStream.isLive
              ? displayedCameraNames.length > 0
                ? `Connected to ${displayedCameraNames.join(", ")}`
                : "Connecting…"
              : "Upload a photo or CCTV clip, then choose which detection models run on this camera"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {liveStream.isLive && (
            <div className="flex rounded-md border border-slate-700 bg-slate-900 p-0.5">
              <button
                type="button"
                onClick={() => setViewMode("single")}
                className={`rounded px-2.5 py-1 text-xs font-semibold transition cursor-pointer ${
                  viewMode === "single"
                    ? "bg-slate-800 text-white shadow-sm"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                Single Feed
              </button>
              <button
                type="button"
                onClick={() => setViewMode("matrix")}
                className={`rounded px-2.5 py-1 text-xs font-semibold transition cursor-pointer ${
                  viewMode === "matrix"
                    ? "bg-slate-800 text-white shadow-sm"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                Matrix View
              </button>
            </div>
          )}
          {viewMode === "single" && liveStream.isLive && cameras.length > 0 && (
            <select
              value={activeCameraId}
              onChange={(e) => handleCameraChange(Number(e.target.value))}
              className="rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white outline-none focus:border-slate-500 cursor-pointer"
            >
              {cameras.map((c) => (
                <option key={c.id} value={c.id} disabled={!c.active}>
                  {c.name} {!c.active ? "(offline)" : ""}
                </option>
              ))}
            </select>
          )}
          {liveStream.isLive && (
            <button
              type="button"
              disabled={isConfiguringCameras}
              onClick={() => {
                setTempCameras(cameras);
                setIsConfiguringCameras(true);
              }}
              className="flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-800 disabled:opacity-50 disabled:cursor-not-allowed transition cursor-pointer"
            >
              <Settings className="size-3.5" />
              Configure cameras
            </button>
          )}
        </div>
      </div>

      <div className="grid gap-4 p-4">
        {isConfiguringCameras && (
          <div className="rounded-md border border-slate-800 bg-slate-900/60 p-4">
            <div className="flex items-center justify-between mb-3 border-b border-slate-800 pb-2">
              <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">
                Configure cameras
              </h3>
              <button
                type="button"
                onClick={() => {
                  const nextId = tempCameras.length > 0 ? Math.max(...tempCameras.map((c) => c.id)) + 1 : 1;
                  const newCam: CameraConfig = {
                    id: nextId,
                    name: `Camera ${nextId}`,
                    rtspUrl: `rtsp://127.0.0.1:8554/stream${nextId}`,
                    zoneId: "",
                    homeZoneId: null,
                    active: true,
                  };
                  setTempCameras([...tempCameras, newCam]);
                }}
                className="flex items-center gap-1 rounded bg-lime-600 hover:bg-lime-500 px-2.5 py-1 text-xs font-semibold text-white transition cursor-pointer"
              >
                + Add camera
              </button>
            </div>
            {tempCameras.length === 0 ? (
              <div className="text-center py-6 text-xs text-slate-500">
                No camera streams configured. Click "+ Add camera" to add one.
              </div>
            ) : (
              <div className="grid gap-4 md:grid-cols-3">
                {tempCameras.map((cam, idx) => (
                  <div key={cam.id} className="grid gap-2 rounded border border-slate-800 bg-slate-900 p-3">
                    <div className="flex items-start justify-between gap-2">
                      <label className="grid gap-0.5 text-[10px] text-slate-400 flex-1 min-w-0">
                        Camera name
                        <input
                          type="text"
                          value={cam.name}
                          placeholder="Camera Name"
                          onChange={(e) => {
                            const updated = [...tempCameras];
                            updated[idx] = { ...updated[idx], name: e.target.value };
                            setTempCameras(updated);
                          }}
                          className="rounded border border-slate-700 bg-slate-950 px-2 py-0.5 text-xs text-white font-semibold outline-none focus:border-slate-500 w-full"
                        />
                      </label>
                      <div className="flex items-center gap-1.5 shrink-0 pt-3.5">
                        <label className="flex items-center gap-1 cursor-pointer text-[10px] text-slate-300">
                          <input
                            type="checkbox"
                            checked={cam.active}
                            onChange={(e) => {
                              const updated = [...tempCameras];
                              updated[idx] = { ...updated[idx], active: e.target.checked };
                              setTempCameras(updated);
                            }}
                            className="rounded border-slate-700 bg-slate-950 text-lime-500 focus:ring-0 cursor-pointer size-3"
                          />
                          Active
                        </label>
                        <button
                          type="button"
                          onClick={() => {
                            const updated = tempCameras.filter((c) => c.id !== cam.id);
                            setTempCameras(updated);
                          }}
                          className="text-red-400 hover:text-red-300 hover:bg-red-500/10 p-1 rounded transition cursor-pointer"
                          title="Delete stream totally"
                        >
                          <Trash2 className="size-3.5" />
                        </button>
                      </div>
                    </div>
                    <label className="grid gap-0.5 text-[10px] text-slate-400">
                      Camera URL
                      <input
                        type="text"
                        value={cam.rtspUrl}
                        placeholder="rtsp://address/stream"
                        onChange={(e) => {
                          const updated = [...tempCameras];
                          updated[idx] = { ...updated[idx], rtspUrl: e.target.value };
                          setTempCameras(updated);
                        }}
                        className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-white outline-none focus:border-slate-500"
                      />
                    </label>
                    <div className="grid grid-cols-2 gap-2">
                      <label className="grid gap-0.5 text-[10px] text-slate-400">
                        3D Blueprint Zone
                        <select
                          value={cam.zoneId ?? ""}
                          onChange={(e) => {
                            const updated = [...tempCameras];
                            updated[idx] = { ...updated[idx], zoneId: e.target.value };
                            setTempCameras(updated);
                          }}
                          className="rounded border border-slate-700 bg-slate-950 px-1 py-1 text-xs text-white outline-none focus:border-slate-500 cursor-pointer"
                        >
                          <option value="">None</option>
                          <option value="Z01">Production (Z01)</option>
                          <option value="Z02">Warehouse (Z02)</option>
                          <option value="Z03">Packing (Z03)</option>
                        </select>
                      </label>
                      <label className="grid gap-0.5 text-[10px] text-slate-400">
                        Home zone (Analytics)
                        <select
                          value={cam.homeZoneId ?? ""}
                          onChange={(e) => {
                            if (e.target.value === "__new__") {
                              setCreatingZoneForIdx(idx);
                              setNewZoneName("");
                              setNewZoneError("");
                              return;
                            }
                            const updated = [...tempCameras];
                            updated[idx] = {
                              ...updated[idx],
                              homeZoneId: e.target.value === "" ? null : Number(e.target.value),
                            };
                            setTempCameras(updated);
                          }}
                          className="rounded border border-slate-700 bg-slate-950 px-1 py-1 text-xs text-white outline-none focus:border-slate-500 cursor-pointer"
                        >
                          {/* Not a real choice — every camera should belong to a zone.
                              Disabled placeholder only so cameras that predate this
                              requirement still render their true (unset) state instead
                              of silently snapping to whichever zone renders first. */}
                          <option value="" disabled>
                            No zone assigned
                          </option>
                          {physicalZones.map((zone) => (
                            <option key={zone.id} value={zone.id}>
                              {zone.name}
                            </option>
                          ))}
                          <option value="__new__">+ Create new zone…</option>
                        </select>
                      </label>
                    </div>
                    {creatingZoneForIdx === idx && (
                      <div className="grid gap-1.5 rounded border border-lime-700/50 bg-slate-950 p-2">
                        <input
                          type="text"
                          autoFocus
                          value={newZoneName}
                          placeholder="New zone name"
                          onChange={(e) => setNewZoneName(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") void handleCreateZone(idx);
                            if (e.key === "Escape") setCreatingZoneForIdx(null);
                          }}
                          className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-white outline-none focus:border-lime-500"
                        />
                        {newZoneError && <p className="text-[10px] text-red-400">{newZoneError}</p>}
                        <div className="flex justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => setCreatingZoneForIdx(null)}
                            className="rounded px-2 py-1 text-[10px] font-semibold text-slate-400 hover:text-white transition cursor-pointer"
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleCreateZone(idx)}
                            className="rounded bg-lime-600 hover:bg-lime-500 px-2 py-1 text-[10px] font-semibold text-white transition cursor-pointer"
                          >
                            Create &amp; assign
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
            <div className="flex justify-end gap-2 mt-4">
              <button
                type="button"
                onClick={() => {
                  if (window.confirm("All changes have not been saved yet. Are you sure you want to discard them?")) {
                    setIsConfiguringCameras(false);
                  }
                }}
                className="rounded px-3 py-1.5 text-xs font-semibold text-slate-400 hover:text-white transition cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => handleSaveCameraConfig(tempCameras)}
                className="rounded bg-lime-600 hover:bg-lime-500 px-3 py-1.5 text-xs font-semibold text-white transition cursor-pointer"
              >
                Save Changes
              </button>
            </div>
          </div>
        )}
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
                  {liveStream.isLive
                    ? displayedCameraNames.length > 0
                      ? displayedCameraNames.join(", ")
                      : "Connecting…"
                    : upload.file?.name}
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

            {viewMode === "single" && (
              <div className="grid gap-2 sm:grid-cols-3">
                <ModelToggle
                  label="PPE Detection"
                  description="Helmet and role-uniform compliance"
                  enabled={currentPpeEnabled}
                  onToggle={() => {
                    if (viewMode === "single" && activeCameraId) {
                      void handleToggleCameraFeature(activeCameraId, "ppe_detection");
                    } else {
                      setPpeEnabled((current) => !current);
                    }
                  }}
                />
                <ModelToggle
                  label="Zone Monitoring"
                  description="Restricted and walkway zones"
                  enabled={currentZoneEnabled && (liveStream.isLive || isVideo)}
                  disabled={!liveStream.isLive && !isVideo}
                  onToggle={() => {
                    if (viewMode === "single" && activeCameraId) {
                      void handleToggleCameraFeature(activeCameraId, "zone_monitoring");
                    } else {
                      setZoneEnabled((current) => !current);
                    }
                  }}
                />
                <ModelToggle
                  label="Fall Detection"
                  description="Live pose risk and incident capture"
                  enabled={currentFallEnabled && (liveStream.isLive || isVideo)}
                  disabled={!liveStream.isLive && !isVideo}
                  onToggle={() => {
                    if (viewMode === "single" && activeCameraId) {
                      void handleToggleCameraFeature(activeCameraId, "fall_detection");
                    } else {
                      setFallEnabled((current) => !current);
                    }
                  }}
                />
              </div>
            )}

            {(liveStream.isLive || (isVideo && upload.videoUrl)) ? (
              viewMode === "matrix" ? (
                <div className="grid gap-4">
                  {/* Visible Cameras Selector */}
                  <div className="flex flex-wrap items-center gap-4 rounded-md border border-slate-800 bg-slate-950/40 p-3">
                    <span className="text-xs font-semibold text-slate-300">Visible Cameras:</span>
                    <div className="flex flex-wrap items-center gap-3.5">
                      {cameras.map((c) => (
                        <label key={c.id} className="flex items-center gap-1.5 text-xs text-white cursor-pointer select-none">
                          <input
                            type="checkbox"
                            checked={selectedCameraIds.includes(c.id)}
                            onChange={(e) => {
                              if (e.target.checked) {
                                setSelectedCameraIds([...selectedCameraIds, c.id]);
                              } else {
                                setSelectedCameraIds(selectedCameraIds.filter((id) => id !== c.id));
                              }
                            }}
                            className="rounded border-slate-700 bg-slate-950 text-lime-500 focus:ring-0 cursor-pointer size-3.5"
                          />
                          {c.name}
                        </label>
                      ))}
                    </div>
                  </div>

                  {/* Grid of streams */}
                  {selectedCameraIds.length === 0 ? (
                    <div className="flex flex-col items-center justify-center rounded-md border border-dashed border-slate-800 py-16 text-center text-slate-400">
                      <p className="text-sm font-medium">No cameras selected</p>
                      <p className="text-xs text-slate-500 mt-1">Check at least one camera above to view its stream feed.</p>
                    </div>
                  ) : (
                    <div
                      className={`grid gap-4 ${
                        selectedCameraIds.length === 1
                          ? "grid-cols-1"
                          : selectedCameraIds.length === 2
                            ? "grid-cols-2"
                            : selectedCameraIds.length <= 4
                              ? "grid-cols-2"
                              : "grid-cols-3"
                      }`}
                    >
                      {cameras
                        .filter((c) => selectedCameraIds.includes(c.id))
                        .map((c) => {
                          const frameUrl = liveStream.liveFrames[c.rtspUrl];
                          return (
                            <div
                              key={c.id}
                              onClick={() => {
                                void handleCameraChange(c.id);
                                setViewMode("single");
                              }}
                              className="group relative aspect-video overflow-hidden rounded-md border border-slate-800 bg-black cursor-pointer hover:border-slate-500 transition-all shadow-md"
                            >
                              {c.active && frameUrl ? (
                                <>
                                  {/* Camera specific inline feature toggles */}
                                  <div className="absolute top-2 right-2 flex gap-1.5 z-10">
                                    <button
                                      type="button"
                                      title="Toggle PPE Detection"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        void handleToggleCameraFeature(c.id, "ppe_detection");
                                      }}
                                      className={`rounded-md p-1.5 shadow-md backdrop-blur transition-all duration-200 border cursor-pointer ${
                                        (cameraFeatureMap[c.id]?.["ppe_detection"] ?? true)
                                          ? "bg-lime-500/90 text-slate-950 border-lime-400 hover:bg-lime-500"
                                          : "bg-slate-900/80 text-slate-400 border-slate-700/50 hover:bg-slate-800"
                                      }`}
                                    >
                                      <Shield className="size-3.5" />
                                    </button>
                                    <button
                                      type="button"
                                      title="Toggle Zone Monitoring"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        void handleToggleCameraFeature(c.id, "zone_monitoring");
                                      }}
                                      className={`rounded-md p-1.5 shadow-md backdrop-blur transition-all duration-200 border cursor-pointer ${
                                        (cameraFeatureMap[c.id]?.["zone_monitoring"] ?? false)
                                          ? "bg-yellow-500/90 text-slate-950 border-yellow-400 hover:bg-yellow-500"
                                          : "bg-slate-900/80 text-slate-400 border-slate-700/50 hover:bg-slate-800"
                                      }`}
                                    >
                                      <Eye className="size-3.5" />
                                    </button>
                                    <button
                                      type="button"
                                      title="Toggle Fall Detection"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        void handleToggleCameraFeature(c.id, "fall_detection");
                                      }}
                                      className={`rounded-md p-1.5 shadow-md backdrop-blur transition-all duration-200 border cursor-pointer ${
                                        (cameraFeatureMap[c.id]?.["fall_detection"] ?? false)
                                          ? "bg-red-500/90 text-slate-950 border-red-400 hover:bg-red-500"
                                          : "bg-slate-900/80 text-slate-400 border-slate-700/50 hover:bg-slate-800"
                                      }`}
                                    >
                                      <Activity className="size-3.5" />
                                    </button>
                                  </div>

                                  <img
                                    src={frameUrl}
                                    alt={c.name}
                                    className="absolute inset-0 h-full w-full object-contain animate-fadeIn"
                                  />
                                  {/* Zones Overlay */}
                                  {(cameraFeatureMap[c.id]?.["zone_monitoring"] ?? false) && cameraZones[c.rtspUrl] && (
                                    <svg
                                      className="pointer-events-none absolute inset-0 h-full w-full"
                                      viewBox="0 0 1 1"
                                      preserveAspectRatio="none"
                                    >
                                      {cameraZones[c.rtspUrl].map((zone) => {
                                        const pathData = zone.points.length > 0
                                          ? `M ${zone.points[0].x} ${zone.points[0].y} ` +
                                            zone.points.map((p, i) => {
                                              const nextP = zone.points[(i + 1) % zone.points.length];
                                              if (p.curveControl) {
                                                return `Q ${p.curveControl.x} ${p.curveControl.y}, ${nextP.x} ${nextP.y}`;
                                              }
                                              return `L ${nextP.x} ${nextP.y}`;
                                            }).join(" ") + " Z"
                                          : "";

                                        return (
                                          <path
                                            key={zone.id}
                                            d={pathData}
                                            fill={`${zoneColors[zone.type]}33`}
                                            stroke={zoneColors[zone.type]}
                                            strokeWidth={0.004}
                                            className="pointer-events-none"
                                          />
                                        );
                                      })}
                                    </svg>
                                  )}

                                  {/* Tracking Overlay Layer (PPE / Zone Violations) */}
                                  {liveStream.cameraOverlays[c.rtspUrl] && (
                                    (() => {
                                      const overlayData = liveStream.cameraOverlays[c.rtspUrl];
                                      const isPpeEnabled = cameraFeatureMap[c.id]?.["ppe_detection"] ?? true;
                                      const isZoneEnabled = cameraFeatureMap[c.id]?.["zone_monitoring"] ?? false;
                                      // Filter frames based on global models enabled
                                      const filteredFrames = overlayData.frames.map((frame) => {
                                        const missing = isPpeEnabled ? frame.missing_equipment : [];
                                        const incursionType = isZoneEnabled ? frame.zone_type : null;
                                        const incursionName = isZoneEnabled ? frame.zone_name : null;
                                        const compliant = isPpeEnabled ? frame.compliant : true;
                                        
                                        return {
                                          ...frame,
                                          missing_equipment: missing,
                                          zone_type: incursionType,
                                          zone_name: incursionName,
                                          compliant: compliant,
                                        };
                                      }).filter((frame) => {
                                        return frame.missing_equipment.length > 0 || frame.zone_type !== null || isPpeEnabled;
                                      });

                                      const syntheticOverlay = {
                                        fps: 30,
                                        stride: 1,
                                        frame_width: overlayData.frameWidth,
                                        frame_height: overlayData.frameHeight,
                                        frames: filteredFrames.map((f) => ({ ...f, frame_index: 0 })),
                                      };

                                      return (
                                        <TrackingOverlayLayer
                                          overlay={syntheticOverlay}
                                          currentTime={0}
                                        />
                                      );
                                    })()
                                  )}

                                  {/* Fall Detection Overlay Layer */}
                                  {(cameraFeatureMap[c.id]?.["fall_detection"] ?? false) && liveStream.cameraOverlays[c.rtspUrl] && (
                                    <FallOverlayLayer
                                      detections={liveStream.cameraOverlays[c.rtspUrl].fallDetections}
                                      frameWidth={liveStream.cameraOverlays[c.rtspUrl].frameWidth}
                                      frameHeight={liveStream.cameraOverlays[c.rtspUrl].frameHeight}
                                    />
                                  )}
                                </>
                              ) : (
                                <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-slate-500 bg-slate-950/80">
                                  {c.active ? (
                                    <>
                                      <Loader2 className="size-6 animate-spin text-slate-400" />
                                      <span className="text-[10px] uppercase font-semibold tracking-wider text-slate-400">Connecting stream...</span>
                                    </>
                                  ) : (
                                    <span className="text-[10px] uppercase font-semibold tracking-wider text-slate-500">Camera Offline</span>
                                  )}
                                </div>
                              )}
                              {/* Overlay Badge */}
                              <div className="absolute top-2 left-2 flex items-center gap-1.5 rounded bg-slate-950/70 px-2 py-0.5 text-[10px] font-medium text-white backdrop-blur-sm">
                                <span className={`size-1.5 rounded-full ${c.active ? "bg-emerald-500" : "bg-slate-400"}`} />
                                {c.name}
                              </div>
                              {/* Tooltip on hover */}
                              <div className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity">
                                <span className="rounded bg-slate-900/90 border border-slate-700 px-2.5 py-1 text-xs font-semibold text-white shadow-lg">
                                  Switch to single view
                                </span>
                              </div>
                            </div>
                          );
                        })}
                    </div>
                  )}
                </div>
              ) : (
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
                      {currentZoneEnabled || zoneDrawing.isDrawing || zoneDrawing.pendingAutoZoneIds.size > 0 ? (
                        <ZoneOverlaySvg zoneDrawing={zoneDrawing} zoneColors={zoneColors} />
                      ) : null}
                      {!zoneDrawing.isDrawing ? (
                        <TrackingOverlayLayer
                          overlay={visibleTrackingOverlay}
                          currentTime={liveStream.currentVideoTime}
                        />
                      ) : null}
                      {currentFallEnabled && !zoneDrawing.isDrawing ? (
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
                        suggestions={currentPpeEnabled ? [] : Object.values(autoZone.ppeSuggestions)}
                        onEnable={autoZone.handleEnablePPESuggestion}
                        onDismiss={autoZone.handleDismissPPESuggestion}
                      />
                    </div>
                    {zoneDrawing.isDrawing ? (
                      <p className="mt-2 text-xs text-slate-400">
                        Click the video frame to add zone polygon points. Video controls are disabled during drawing.
                      </p>
                    ) : currentZoneEnabled ? (
                      <p className="mt-2 text-xs text-slate-400">
                        Viewing saved zones. Click &quot;Start draw zone&quot; to add new areas.
                      </p>
                    ) : null}
                  </div>

                  <div className="grid gap-4">
                    <ZoneConfigPanel
                      zoneDrawing={zoneDrawing}
                      zoneEnabled={currentZoneEnabled}
                      phase={phase}
                      isStreaming={liveStream.isStreaming}
                      isVideo={isVideo}
                      isLive={liveStream.isLive}
                      isPlaying={liveStream.isPlaying}
                      togglePlayback={liveStream.togglePlayback}
                    />
                    <FallStatusPanel
                      enabled={currentFallEnabled}
                      summary={liveStream.streamData.fall_summary}
                      unavailable={liveStream.streamData.fall_unavailable}
                      latestIncident={liveStream.streamData.behavior_incidents.at(-1)}
                    />
                  </div>
                </div>
              )
            ) : null}
          </div>
        ) : null}

        {phase === "loading" && (currentPpeEnabled || currentZoneEnabled || currentFallEnabled) ? <LoadingState text="Running inference..." /> : null}
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
            ppeEnabled={currentPpeEnabled}
            zoneEnabled={currentZoneEnabled}
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

const SAFETY_EVENTS_QUERY_KEY = ["safety-events"] as const;
const INCIDENTS_PAGE_SIZE = 6;

/** Page numbers to render around `current`, with "…" gap markers — first
 * and last page always shown so long lists don't need to be scrolled
 * through one button at a time. */
function getPageWindow(current: number, total: number): (number | "…")[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const pages = [...new Set([1, total, current - 1, current, current + 1])]
    .filter((p) => p >= 1 && p <= total)
    .sort((a, b) => a - b);
  const windowed: (number | "…")[] = [];
  pages.forEach((p, i) => {
    if (i > 0 && p - pages[i - 1] > 1) windowed.push("…");
    windowed.push(p);
  });
  return windowed;
}

function IncidentPanel() {
  const queryClient = useQueryClient();
  const eventsQuery = useQuery({
    queryKey: SAFETY_EVENTS_QUERY_KEY,
    queryFn: getSafetyEvents,
  });
  const events = eventsQuery.data ?? [];
  const [deleteAllError, setDeleteAllError] = useState("");
  const [deletingAll, setDeletingAll] = useState(false);
  const [selectedIncident, setSelectedIncident] = useState<{
    category: IncidentCategory;
    id: number;
  } | null>(null);
  const [page, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(events.length / INCIDENTS_PAGE_SIZE));
  // Clamp during render (not an effect) if the page count shrinks out from
  // under the current page — e.g. after deleting the last item on the last page.
  const currentPage = Math.min(page, totalPages);
  if (currentPage !== page) setPage(currentPage);
  const pageEvents = events.slice(
    (currentPage - 1) * INCIDENTS_PAGE_SIZE,
    currentPage * INCIDENTS_PAGE_SIZE,
  );

  // isFetching (not isPending) so the spinner also shows on the manual
  // refresh button click below, matching the previous setLoading(true)-on-
  // every-call behavior.
  const loading = eventsQuery.isFetching || deletingAll;
  const error =
    deleteAllError ||
    (eventsQuery.isError
      ? eventsQuery.error instanceof Error
        ? eventsQuery.error.message
        : "Could not load violations"
      : "");

  const removeEventFromCache = (id: number) => {
    queryClient.setQueryData<(ViolationReport | ZoneViolation | BehaviorIncident)[]>(
      SAFETY_EVENTS_QUERY_KEY,
      (current) => current?.filter((item) => item.id !== id),
    );
  };

  const deleteEvent = async (event: ViolationReport | ZoneViolation | BehaviorIncident) => {
    if (!event.id || !confirm(CONFIRM_DELETE_INCIDENT)) {
      return;
    }
    const category: IncidentCategory =
      "violation_type" in event ? "ppe" : "behavior_type" in event ? "behavior" : "zone";
    await deleteIncident(category, event.id);
    removeEventFromCache(event.id);
  };

  const deleteAllEvents = async () => {
    if (!confirm(CONFIRM_DELETE_ALL_INCIDENTS)) return;
    setDeletingAll(true);
    setDeleteAllError("");
    try {
      await deleteAllIncidents();
      queryClient.setQueryData(SAFETY_EVENTS_QUERY_KEY, []);
    } catch (err) {
      setDeleteAllError(err instanceof Error ? err.message : "Could not delete incidents");
    } finally {
      setDeletingAll(false);
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
            onClick={() => void eventsQuery.refetch()}
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
        {pageEvents.map((event, index) => (
          <IncidentCard
            key={`${event.id ?? index}-${event.timestamp}`}
            event={event}
            onDelete={() => void deleteEvent(event)}
            onOpenDetail={(category, id) => setSelectedIncident({ category, id })}
          />
        ))}
      </div>
      {totalPages > 1 ? (
        <div className="mt-3 flex items-center justify-between gap-2 border-t border-slate-200 pt-3">
          <p className="text-xs text-slate-500">
            Page {currentPage} of {totalPages} · {events.length} incident{events.length === 1 ? "" : "s"}
          </p>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={currentPage === 1}
              aria-label="Previous page"
              className="rounded-md border border-slate-200 bg-white p-1.5 text-slate-500 transition hover:border-slate-300 hover:text-slate-950 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ChevronLeft className="size-3.5" aria-hidden="true" />
            </button>
            {getPageWindow(currentPage, totalPages).map((p, i) =>
              p === "…" ? (
                <span key={`ellipsis-${i}`} className="px-1 text-xs text-slate-400">
                  …
                </span>
              ) : (
                <button
                  key={p}
                  type="button"
                  onClick={() => setPage(p)}
                  aria-current={p === currentPage ? "page" : undefined}
                  className={`min-w-[1.75rem] rounded-md px-2 py-1 text-xs font-semibold transition ${
                    p === currentPage ? "bg-slate-950 text-lime-200" : "text-slate-500 hover:bg-slate-100"
                  }`}
                >
                  {p}
                </button>
              ),
            )}
            <button
              type="button"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={currentPage === totalPages}
              aria-label="Next page"
              className="rounded-md border border-slate-200 bg-white p-1.5 text-slate-500 transition hover:border-slate-300 hover:text-slate-950 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ChevronRight className="size-3.5" aria-hidden="true" />
            </button>
          </div>
        </div>
      ) : null}
      {selectedIncident ? (
        <IncidentDetailModal
          key={`${selectedIncident.category}-${selectedIncident.id}`}
          category={selectedIncident.category}
          incidentId={selectedIncident.id}
          onClose={() => setSelectedIncident(null)}
          onDeleted={() => removeEventFromCache(selectedIncident.id)}
        />
      ) : null}
    </section>
  );
}

const DASHBOARD_VIEWS: DashboardView[] = ["feeds", "violations", "factory3d", "analytics"];

export function DashboardShell() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [activeView, setActiveViewState] = useState<DashboardView>(() => {
    const fromUrl = searchParams.get("view");
    return DASHBOARD_VIEWS.includes(fromUrl as DashboardView) ? (fromUrl as DashboardView) : "feeds";
  });
  // Mount once on first visit, then keep mounted (only hidden) so switching
  // back to this tab doesn't re-fetch the summary/trend/compare/feed from
  // scratch every time — same reasoning as CameraPanel below.
  const [analyticsMounted, setAnalyticsMounted] = useState(() => searchParams.get("view") === "analytics");
  const setActiveView = useCallback(
    (view: DashboardView) => {
      setActiveViewState(view);
      if (view === "analytics") setAnalyticsMounted(true);
      const params = new URLSearchParams(searchParams.toString());
      if (view === "feeds") {
        params.delete("view");
      } else {
        params.set("view", view);
      }
      const qs = params.toString();
      router.replace(qs ? `/?${qs}` : "/", { scroll: false });
    },
    [router, searchParams],
  );
  const [activeCameraId, setActiveCameraId] = useState<number>(1);
  const [cameras, setCameras] = useState<CameraConfig[]>([]);
  const [physicalZones, setPhysicalZones] = useState<PhysicalZone[]>([]);
  // Same hook and default range/zone (7D, all zones) as the Incident
  // Analytics tab's KPI row, so the two stay in sync instead of this one
  // showing static placeholder data.
  const { kpis } = useSafetyKpis();

  useEffect(() => {
    const stored = localStorage.getItem("ppe_demo_cameras");
    let loaded: CameraConfig[] = DEFAULT_CAMERAS;
    if (stored) {
      try {
        loaded = JSON.parse(stored);
      } catch (e) {
        loaded = DEFAULT_CAMERAS;
      }
    } else {
      localStorage.setItem("ppe_demo_cameras", JSON.stringify(DEFAULT_CAMERAS));
    }
    // Backfill homeZoneId for configs saved before this field existed.
    loaded = loaded.map((c) => ({ ...c, homeZoneId: c.homeZoneId ?? null }));
    setCameras(loaded);
    const active = loaded.find((c) => c.active) || loaded[0];
    setActiveCameraId(active.id);

    void getPhysicalZones().then(setPhysicalZones).catch(() => {});

    // Reconcile home-zone assignment from the backend (source of truth), so
    // it survives a reload even though the rest of the camera config is
    // still cached in localStorage only.
    void getCameras()
      .then((backendCameras) => {
        setCameras((prev) => {
          const reconciled = prev.map((cam) => {
            const match = backendCameras.find((bc) => bc.source_key === cam.rtspUrl);
            return match ? { ...cam, id: match.id, homeZoneId: match.home_zone_id } : cam;
          });
          localStorage.setItem("ppe_demo_cameras", JSON.stringify(reconciled));
          
          // Sync activeCameraId to match the reconciled database camera ID
          const active = reconciled.find((c) => c.active) || reconciled[0];
          if (active) {
            setActiveCameraId(active.id);
          }
          return reconciled;
        });
      })
      .catch(() => {});
  }, []);
  const cameraCountsByZone = useMemo(() => {
    const counts: Record<number, number> = {};
    for (const cam of cameras) {
      if (cam.homeZoneId != null) {
        counts[cam.homeZoneId] = (counts[cam.homeZoneId] ?? 0) + 1;
      }
    }
    return counts;
  }, [cameras]);

  const pageTitle =
    activeView === "violations"
      ? "Incident Log"
      : activeView === "factory3d"
        ? "Factory 3D Map"
        : activeView === "analytics"
          ? "Incident Analytics"
          : "Packaging Line 1";
  const pageDescription =
    activeView === "violations"
      ? "Review PPE, zone, and behavior incidents recorded by the backend stores."
      : activeView === "factory3d"
        ? "Explore the factory blueprint in 3D and drill into a zone's incident log."
        : activeView === "analytics"
          ? "Trends, zone comparisons, and live incident feed across all cameras."
          : "Upload a camera simulation file, choose which detection models are enabled, and review the model outputs in one place.";

  return (
    <div className="min-h-screen bg-slate-100 text-slate-950">
      <TopBar activeView={activeView} onViewChange={setActiveView} />
      <div className="lg:flex">
        <ZoneSidebar
          physicalZones={physicalZones}
          cameraCounts={cameraCountsByZone}
          onPhysicalZonesUpdate={setPhysicalZones}
        />
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
              {kpis.map((metric) => (
                <MetricCard key={metric.label} metric={metric} />
              ))}
            </section>

            <div className="grid items-start gap-4">
              <div className="grid h-fit gap-4">
                {activeView === "violations" ? <IncidentPanel /> : null}
                {/* Stays mounted (only hidden) once visited so its polling keeps the
                    data warm and switching back doesn't refetch from scratch. */}
                {analyticsMounted ? (
                  <div className={activeView === "analytics" ? "grid gap-4" : "hidden"}>
                    <AnalyticsDashboard embedded />
                  </div>
                ) : null}
                {activeView === "factory3d" ? (
                  <Factory3DView
                    cameras={cameras}
                    activeCameraId={activeCameraId}
                    onSelectActiveCamera={(cam) => {
                      setActiveCameraId(cam.id);
                      setActiveView("feeds");
                    }}
                  />
                ) : null}
                {/* CameraPanel stays mounted (only hidden) when on other tabs so
                    its WebSocket keeps streaming instead of disconnecting on tab switch. */}
                <div className={activeView === "feeds" ? "grid gap-4" : "hidden"}>
                  <CameraPanel
                    cameras={cameras}
                    activeCameraId={activeCameraId}
                    physicalZones={physicalZones}
                    onCameraChange={setActiveCameraId}
                    onCamerasUpdate={setCameras}
                    onPhysicalZonesUpdate={setPhysicalZones}
                  />
                </div>
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

function zoneFromBackend(zone: any): DraftZone {
  try {
    const uiData = JSON.parse(zone.ui_shape_data);
    if (uiData && Array.isArray(uiData.points)) {
      return {
        id: String(zone.id ?? crypto.randomUUID()),
        name: zone.zone_name,
        type: zone.zone_type,
        dwellThresholdSeconds: zone.dwell_threshold_seconds,
        points: uiData.points,
      };
    }
  } catch {}
  return {
    id: String(zone.id ?? crypto.randomUUID()),
    name: zone.zone_name,
    type: zone.zone_type,
    dwellThresholdSeconds: zone.dwell_threshold_seconds,
    points: safeParsePoints(zone.flattened_coordinates),
  };
}

function safeParsePoints(value: string) {
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function StatusPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 font-semibold text-slate-950">{value}</p>
    </div>
  );
}
