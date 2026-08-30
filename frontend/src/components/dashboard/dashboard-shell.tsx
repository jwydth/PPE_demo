"use client";

import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock,
  HardHat,
  Loader2,
  Maximize2,
  RefreshCw,
  Settings,
  ShieldAlert,
  SlidersHorizontal,
  Trash2,
  MapPinned,
  PersonStanding,
  X,
} from "lucide-react";
import dynamic from "next/dynamic";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createPhysicalZone,
  deleteAllIncidents,
  deleteCamera,
  deleteIncident,
  ensureCamera,
  getCameras,
  getPhysicalZones,
  getSafetyEventsPage,
  type SafetyEventsPage,
  getZones,
  setCameraHomeZone,
  getCameraFeatures,
  updateCameraFeatures,
} from "@/lib/ppe-api";
import { BoundingBoxView } from "@/components/ppe/bounding-box-view";
import { FileUpload } from "@/components/ppe/file-upload";
import { LlHlsVideo } from "@/components/ppe/ll-hls-video";
import {
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
import { CONFIRM_DELETE_INCIDENT } from "@/lib/messages";
import { ConfirmDeleteAllDialog } from "./confirm-delete-all-dialog";
import { MonitoringStatus } from "./monitoring-status";
import {
  Severity,
  SEVERITY_ACTIVE_CLASS,
  SEVERITY_DOT_CLASS,
  VALID_SEVERITIES,
} from "@/lib/incident-severity";
import { TrackingOverlay, ViolationReport } from "@/types/detection";
import { PhysicalZone, ZoneType, ZoneViolation } from "@/types/zone";
import { useDetectionUpload } from "@/hooks/useDetectionUpload";
import { useZoneDrawing } from "@/hooks/useZoneDrawing";
import { useLiveStream } from "@/hooks/useLiveStream";
import { useAutoZoneSuggestions } from "@/hooks/useAutoZoneSuggestions";
import { AnalysisPhase, DraftZone } from "@/hooks/camera-panel-types";
import { BehaviorIncident } from "@/types/behavior";
import { useSafetyKpis } from "@/hooks/useSafetyKpis";
import { TopBar, type DashboardView } from "./top-bar";
import { ZoneSidebar } from "./zone-sidebar";
import { MetricCard } from "./metric-card";
import { ZoneOverlaySvg } from "./zone-overlay-svg";
import { ZoneConfigPanel } from "./zone-config-panel";
import { AnalysisResultPanel } from "./analysis-result-panel";
import { CATEGORY_LABEL, IncidentCategory, IncidentDetailModal } from "./incident-detail-modal";

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
  IGNORE: "#64748b",
};

function SafetyVestIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.25"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="m9 3-2 4-3 2v12h16V9l-3-2-2-4" />
      <path d="M9 3v4l3 2 3-2V3" />
      <path d="M12 9v12" />
      <path d="M5 13h14" />
      <path d="M5 17h14" />
    </svg>
  );
}

function filterTrackingOverlay(
  overlay: TrackingOverlay | undefined,
  {
    showPpe,
    showZone,
    showBehavior,
  }: {
    showPpe: boolean;
    showZone: boolean;
    showBehavior: boolean;
  },
): TrackingOverlay | undefined {
  // Behavior needs the same person boxes as PPE/Zone, including during its
  // 60-frame warm-up where every person is labelled Behavior: Unknown.
  if (!overlay || (!showPpe && !showZone && !showBehavior)) return undefined;

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
  { id: 2, name: "Production Area", rtspUrl: "rtsp://127.0.0.1:8554/stream1", zoneId: "Z01", homeZoneId: null, active: true },
  { id: 1, name: "Warehouse Intake", rtspUrl: "rtsp://127.0.0.1:8554/stream2", zoneId: "Z02", homeZoneId: null, active: false },
  { id: 3, name: "Packing Area", rtspUrl: "rtsp://127.0.0.1:8554/stream3", zoneId: "Z03", homeZoneId: null, active: false },
];

function normalizeCameraSourceKey(value: string): string {
  const normalized = value.trim();
  try {
    const parsed = new URL(normalized);
    if (
      (parsed.protocol === "rtsp:" || parsed.protocol === "rtsps:") &&
      (parsed.hostname === "localhost" || parsed.hostname === "[::1]")
    ) {
      parsed.hostname = "127.0.0.1";
    }
    return parsed.toString();
  } catch {
    return normalized;
  }
}

function normalizeConfiguredCameras(cameras: CameraConfig[]): CameraConfig[] {
  const sourceKeys = new Set<string>();
  return cameras
    .map((camera) => ({
      ...camera,
      rtspUrl: normalizeCameraSourceKey(camera.rtspUrl),
      homeZoneId: camera.homeZoneId ?? null,
    }))
    .filter((camera) => {
      if (!camera.rtspUrl || sourceKeys.has(camera.rtspUrl)) return false;
      sourceKeys.add(camera.rtspUrl);
      return true;
    });
}

// Synchronously seeds initial state from whatever camera list (with backend
// IDs) a previous session already resolved, so a reload can start streaming
// immediately instead of showing "Connecting to cameras..." while the
// mount effect below re-resolves IDs it already resolved last time. Guarded
// for SSR since this runs inside a useState lazy initializer, which executes
// during the server render too.
function loadCachedCameras(): CameraConfig[] {
  if (typeof window === "undefined") return [];
  try {
    const stored = localStorage.getItem("ppe_demo_cameras");
    if (!stored) return [];
    return normalizeConfiguredCameras(JSON.parse(stored) as CameraConfig[]);
  } catch {
    return [];
  }
}

// Camera feed layout (single vs. matrix, and which cameras the matrix shows)
// persisted across reloads. `seen` records the cameras that have already been
// auto-selected once, so the sync effect below can tell a brand new camera
// (auto-show it) from one the user deliberately unchecked (leave it
// unchecked) — without it, every reload would re-check everything.
//
// Cameras are keyed by source URL, not by backend id: ids are re-resolved
// against the backend on every load and legitimately change (a camera
// recreated server-side, or a failed reconcile falling back to the cached
// list), and an id that changes under a stored selection reads as "that
// camera is gone" plus "this one is already seen" — which silently emptied
// the matrix and, because the ids were remembered as seen, never refilled it.
// The source URL is the identity the camera config itself is keyed on.
const VIEW_MODE_STORAGE_KEY = "ppe_camera_view_mode";
const MATRIX_SELECTION_STORAGE_KEY = "ppe_camera_matrix_selection";

interface StoredMatrixSelection {
  selected: string[];
  seen: string[];
}

const FILTERS_EXPANDED_STORAGE_KEY = "ppe_incident_filters_expanded";

// Collapsed by default (see loadStoredFiltersExpanded's caller): most visits
// to this tab are "check the list", not "narrow it down", so the filter
// controls start tucked away rather than always occupying panel space.
function loadStoredFiltersExpanded(): boolean | null {
  try {
    const stored = localStorage.getItem(FILTERS_EXPANDED_STORAGE_KEY);
    return stored === "true" ? true : stored === "false" ? false : null;
  } catch {
    return null;
  }
}

function loadStoredViewMode(): "single" | "matrix" | null {
  try {
    const stored = localStorage.getItem(VIEW_MODE_STORAGE_KEY);
    return stored === "matrix" || stored === "single" ? stored : null;
  } catch {
    return null;
  }
}

function loadStoredMatrixSelection(): StoredMatrixSelection | null {
  try {
    const stored = localStorage.getItem(MATRIX_SELECTION_STORAGE_KEY);
    if (!stored) return null;
    const parsed = JSON.parse(stored) as Partial<StoredMatrixSelection>;
    // Anything not a string is from the earlier id-keyed format; dropping it
    // leaves `seen` empty, so every active camera is auto-selected once again.
    const sources = (value: unknown) =>
      Array.isArray(value) ? value.filter((src): src is string => typeof src === "string") : [];
    return { selected: sources(parsed?.selected), seen: sources(parsed?.seen) };
  } catch {
    return null;
  }
}

interface CameraPanelProps {
  cameras: CameraConfig[];
  camerasLoading: boolean;
  activeCameraId: number;
  physicalZones: PhysicalZone[];
  onCameraChange: (id: number) => void;
  onCamerasUpdate: (updated: CameraConfig[]) => void;
  onPhysicalZonesUpdate: (updated: PhysicalZone[]) => void;
}

function CameraPanel({
  cameras,
  camerasLoading,
  activeCameraId,
  physicalZones,
  onCameraChange,
  onCamerasUpdate,
  onPhysicalZonesUpdate,
}: CameraPanelProps) {
  const [phase, setPhase] = useState<AnalysisPhase>("idle");
  const [error, setError] = useState("");
  const [viewMode, setViewModeState] = useState<"single" | "matrix">("single");
  const [selectedCameraSources, setSelectedCameraSources] = useState<string[]>([]);
  const [cameraZones, setCameraZones] = useState<Record<string, DraftZone[]>>({});
  // Cameras already auto-added to the matrix selection at least once, by
  // source URL. Kept in a ref (not state) because it only ever feeds the sync
  // effect's decision and must not itself trigger a re-render.
  const autoSelectedSourcesRef = useRef<Set<string>>(new Set());
  const selectionRestoredRef = useRef(false);

  // Restores the saved layout before the browser paints. Deliberately a layout
  // effect rather than a useState lazy initializer, for the same reason as the
  // cached-camera seeding in DashboardShell: this component is SSR'd, so
  // reading localStorage during the initial render would produce different
  // server and client markup and trip a hydration mismatch.
  useLayoutEffect(() => {
    const storedViewMode = loadStoredViewMode();
    if (storedViewMode) setViewModeState(storedViewMode);

    const storedSelection = loadStoredMatrixSelection();
    if (storedSelection) {
      autoSelectedSourcesRef.current = new Set(storedSelection.seen);
      setSelectedCameraSources(storedSelection.selected);
    }
    selectionRestoredRef.current = true;
  }, []);

  const setViewMode = useCallback((mode: "single" | "matrix") => {
    setViewModeState(mode);
    try {
      localStorage.setItem(VIEW_MODE_STORAGE_KEY, mode);
    } catch {
      // Storage unavailable (private window, quota) — the layout just won't persist.
    }
  }, []);

  // Auto-shows each camera the first time it is seen active, prunes cameras
  // that no longer exist, and otherwise leaves the selection alone so a
  // restored (or hand-edited) one survives. Cameras that go offline stay
  // selected and render as "Camera Offline" tiles rather than silently
  // dropping out of the user's selection.
  useEffect(() => {
    if (cameras.length === 0) return;
    const knownSources = new Set(cameras.map((c) => c.rtspUrl));
    const newlyActive = cameras
      .filter((c) => c.active && !autoSelectedSourcesRef.current.has(c.rtspUrl))
      .map((c) => c.rtspUrl);
    newlyActive.forEach((src) => autoSelectedSourcesRef.current.add(src));

    setSelectedCameraSources((prev) => {
      const kept = prev.filter((src) => knownSources.has(src));
      const added = newlyActive.filter((src) => !kept.includes(src));
      if (added.length === 0 && kept.length === prev.length) return prev;
      return [...kept, ...added];
    });
  }, [cameras]);

  useEffect(() => {
    // Skips the pre-restore render so an empty initial selection can't
    // overwrite the saved one before the layout effect above has read it.
    if (!selectionRestoredRef.current) return;
    try {
      localStorage.setItem(
        MATRIX_SELECTION_STORAGE_KEY,
        JSON.stringify({
          selected: selectedCameraSources,
          seen: [...autoSelectedSourcesRef.current],
        } satisfies StoredMatrixSelection),
      );
    } catch {
      // Storage unavailable (private window, quota) — the selection just won't persist.
    }
  }, [selectedCameraSources]);
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
    ? (cameraFeatureMap[activeCameraId]?.["behavior_detection"] ?? cameraFeatureMap[activeCameraId]?.["fall_detection"] ?? fallEnabled)
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
    selectedCameraSources,
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

  // Keyed by the camera the zones were actually loaded for, not by whichever
  // camera is being viewed when this runs. A camera switch updates those two
  // in separate steps (loadSavedZones resolves, then setViewedCamera — or the
  // reverse, in handleSaveCameraConfig), so keying on liveUrl filed the new
  // camera's zones under the old camera's URL and left them there: opening
  // Warehouse Intake then Packing Area drew each one's zones on the other.
  useEffect(() => {
    const key = zoneDrawing.zonesSourceKey;
    if (!key) return;
    setCameraZones((prev) => ({ ...prev, [key]: zoneDrawing.zonesForVideo }));
  }, [zoneDrawing.zonesForVideo, zoneDrawing.zonesSourceKey]);

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
    const camerasToSave = normalizeConfiguredCameras(updatedCameras);
    const currentSource = cameras.find(
      (camera) => camera.id === activeCameraId,
    )?.rtspUrl;
    // Identify cameras that were deleted
    const deletedCameras = cameras.filter(
      (camera) => !camerasToSave.some((candidate) => candidate.id === camera.id),
    );

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
        camerasToSave.map(async (cam) => {
          const backendCamera = await ensureCamera(cam.name, cam.rtspUrl);
          await setCameraHomeZone(backendCamera.id, cam.homeZoneId);
        }),
      );

      // Re-fetch backend cameras to sync any homeZoneId changes
      const latestBackendCameras = await getCameras();
      const reconciled = camerasToSave.map((cam) => {
        const match = latestBackendCameras.find(
          (backendCamera) =>
            normalizeCameraSourceKey(backendCamera.source_key) === cam.rtspUrl,
        );
        return match ? { ...cam, id: match.id, homeZoneId: match.home_zone_id } : cam;
      });
      onCamerasUpdate(reconciled);
      localStorage.setItem("ppe_demo_cameras", JSON.stringify(reconciled));
      setIsConfiguringCameras(false);
      liveStream.syncCameraConnections(reconciled);

      const retainedCamera = reconciled.find(
        (camera) =>
          camera.active &&
          normalizeCameraSourceKey(camera.rtspUrl) ===
            normalizeCameraSourceKey(currentSource ?? ""),
      );
      const nextCamera =
        retainedCamera || reconciled.find((camera) => camera.active) || reconciled[0];
      if (nextCamera) {
        onCameraChange(nextCamera.id);
        liveStream.setViewedCamera(nextCamera.rtspUrl);
        void zoneDrawing.loadSavedZones(nextCamera.rtspUrl);
      }
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
          showBehavior: currentFallEnabled,
        },
      ),
    [
      liveStream.isStreaming,
      currentPpeEnabled,
      liveStream.streamData.summary,
      liveStream.streamData.tracking_overlay,
      upload.videoResult?.tracking_overlay,
      currentZoneEnabled,
      currentFallEnabled,
    ],
  );
  // Unified identifier for the current camera source: the RTSP URL for a live
  // feed, otherwise the uploaded file name. Used as the zone storage key so
  // auto-zone save/load/clear works identically for live and uploaded sources.
  const feedWidth = visibleTrackingOverlay?.frame_width ?? 16;
  const feedHeight = visibleTrackingOverlay?.frame_height ?? 9;
  const feedAspectRatio = feedWidth > 0 && feedHeight > 0
    ? feedWidth / feedHeight
    : 16 / 9;
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
        frame_width: 16,
        frame_height: 9,
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
          const allowsOverlap =
            typeA === "IGNORE" ||
            typeB === "IGNORE" ||
            (typeA === "WALKWAY" && typeB === "SLIPPERY") ||
            (typeA === "SLIPPERY" && typeB === "WALKWAY");

          if (
            typeA !== typeB &&
            !allowsOverlap &&
            doPolygonsOverlap(zoneDrawing.zonesReadyToSave[i].points, zoneDrawing.zonesReadyToSave[j].points)
          ) {
            hasOverlap = true;
            break;
          }
        }
        if (hasOverlap) break;
      }

      if (hasOverlap) {
        setError("Cannot run analysis: Only Exclusion Zones and Walkway/Slippery zones may overlap. Please adjust the vertices to avoid overlap.");
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

  // The cameras the matrix grid is actually showing. Deliberately NOT gated on
  // liveStream.liveFrames: live cameras connect with metadata_only=true (see
  // startStreaming in useLiveStream) and render through HLS, so no preview JPEG
  // ever arrives for them and a liveFrames check would leave the header stuck
  // on "Connecting…" forever. Matches the single-view line below, which names
  // the viewed camera without waiting on frames either.
  const matrixCameraNames = cameras
    .filter((c) => c.active && selectedCameraSources.includes(c.rtspUrl))
    .map((c) => c.name);

  const viewedCameraName = cameras.find((c) => c.rtspUrl === liveStream.liveUrl)?.name;

  const displayedCameraNames = viewMode === "single"
    ? (viewedCameraName ? [viewedCameraName] : [])
    : matrixCameraNames;

  // In matrix view an empty name list means "you unchecked everything", not
  // "still connecting" — the grid says as much, so the header should agree.
  const noCameraNamesLabel = viewMode === "matrix" && selectedCameraSources.length === 0
    ? "No cameras selected"
    : "Connecting…";

  return (
    <section className="h-fit overflow-hidden rounded-md border border-slate-300 bg-slate-950 shadow-md">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
        <div>
          <p className="text-sm font-semibold text-white">
            {camerasLoading || liveStream.isLive ? "Live Camera Feed" : "Packaging Line 1 - Uploaded Feed"}
          </p>
          <p className="text-xs text-slate-400">
            {camerasLoading
              ? "Connecting…"
              : liveStream.isLive
                ? displayedCameraNames.length > 0
                  ? `Connected to ${displayedCameraNames.join(", ")}`
                  : noCameraNamesLabel
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
                      <label className="grid gap-0.5 text-xs text-slate-400 flex-1 min-w-0">
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
                        <label className="flex items-center gap-1 cursor-pointer text-xs text-slate-300">
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
                    <label className="grid gap-0.5 text-xs text-slate-400">
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
                      <label className="grid gap-0.5 text-xs text-slate-400">
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
                      <label className="grid gap-0.5 text-xs text-slate-400">
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
                        {newZoneError && <p className="text-xs text-red-400">{newZoneError}</p>}
                        <div className="flex justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => setCreatingZoneForIdx(null)}
                            className="rounded px-2 py-1 text-xs font-semibold text-slate-400 hover:text-white transition cursor-pointer"
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleCreateZone(idx)}
                            className="rounded bg-lime-600 hover:bg-lime-500 px-2 py-1 text-xs font-semibold text-white transition cursor-pointer"
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
        {camerasLoading ? (
          <div className="grid place-items-center rounded-md border border-slate-800 bg-slate-900 p-8 text-sm text-slate-400">
            Connecting to cameras…
          </div>
        ) : !liveStream.isLive && !upload.file ? (
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
                      : noCameraNamesLabel
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
                  label="Behavior Detection"
                  description="Live pose risk and incident capture"
                  enabled={currentFallEnabled && (liveStream.isLive || isVideo)}
                  disabled={!liveStream.isLive && !isVideo}
                  onToggle={() => {
                    if (viewMode === "single" && activeCameraId) {
                      void handleToggleCameraFeature(activeCameraId, "behavior_detection");
                    } else {
                      setFallEnabled((current) => !current);
                    }
                  }}
                />
              </div>
            )}

            {currentFallEnabled && liveStream.streamData.fall_unavailable ? (
              <p className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-xs text-red-300">
                Behavior detection unavailable: {liveStream.streamData.fall_unavailable}
              </p>
            ) : null}

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
                            checked={selectedCameraSources.includes(c.rtspUrl)}
                            onChange={(e) => {
                              if (e.target.checked) {
                                setSelectedCameraSources([...selectedCameraSources, c.rtspUrl]);
                              } else {
                                setSelectedCameraSources(
                                  selectedCameraSources.filter((src) => src !== c.rtspUrl),
                                );
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
                  {selectedCameraSources.length === 0 ? (
                    <div className="flex flex-col items-center justify-center rounded-md border border-dashed border-slate-800 py-16 text-center text-slate-400">
                      <p className="text-sm font-medium">No cameras selected</p>
                      <p className="text-xs text-slate-500 mt-1">Check at least one camera above to view its stream feed.</p>
                    </div>
                  ) : (
                    <div
                      className={`grid gap-4 ${
                        selectedCameraSources.length === 1
                          ? "grid-cols-1"
                          : selectedCameraSources.length === 2
                            ? "grid-cols-2"
                            : selectedCameraSources.length <= 4
                              ? "grid-cols-2"
                              : "grid-cols-3"
                      }`}
                    >
                      {cameras
                        .filter((c) => selectedCameraSources.includes(c.rtspUrl))
                        .map((c) => {
                          return (
                            <div
                              key={c.id}
                              onClick={() => {
                                void handleCameraChange(c.id);
                                setViewMode("single");
                              }}
                              className="group relative aspect-video overflow-hidden rounded-md border border-slate-800 bg-black cursor-pointer hover:border-slate-500 transition-all shadow-md"
                            >
                              {c.active ? (
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
                                      <SafetyVestIcon className="size-4" />
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
                                      <MapPinned className="size-3.5" />
                                    </button>
                                    <button
                                      type="button"
                                      title="Toggle Behavior Detection"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        void handleToggleCameraFeature(c.id, "behavior_detection");
                                      }}
                                      className={`rounded-md p-1.5 shadow-md backdrop-blur transition-all duration-200 border cursor-pointer ${
                                        (cameraFeatureMap[c.id]?.["behavior_detection"] ?? cameraFeatureMap[c.id]?.["fall_detection"] ?? false)
                                          ? "bg-red-500/90 text-slate-950 border-red-400 hover:bg-red-500"
                                          : "bg-slate-900/80 text-slate-400 border-slate-700/50 hover:bg-slate-800"
                                      }`}
                                    >
                                      <PersonStanding className="size-3.5" />
                                    </button>
                                  </div>

                                  <LlHlsVideo
                                    source={c.rtspUrl}
                                    annotated
                                    className="absolute inset-0 h-full w-full object-contain"
                                    onTimeline={(timeline) => liveStream.handleHlsTimeline(c.rtspUrl, timeline)}
                                    onPlaybackMetrics={(metrics) => liveStream.handlePlaybackMetrics(c.rtspUrl, metrics)}
                                  >
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
                                  </LlHlsVideo>

                                </>
                              ) : (
                                <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-slate-500 bg-slate-950/80">
                                  {c.active ? (
                                    <>
                                      <Loader2 className="size-6 animate-spin text-slate-400" />
                                      <span className="text-xs uppercase font-semibold tracking-wider text-slate-400">Connecting stream...</span>
                                    </>
                                  ) : (
                                    <span className="text-xs uppercase font-semibold tracking-wider text-slate-500">Camera Offline</span>
                                  )}
                                </div>
                              )}
                              {/* Overlay Badge */}
                              <div className="absolute top-2 left-2 flex items-center gap-1.5 rounded bg-slate-950/70 px-2 py-0.5 text-xs font-medium text-white backdrop-blur-sm">
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
                <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.4fr)_340px]">
                  <div>
                    <div
                      ref={liveStream.isLive ? undefined : zoneDrawing.setSurfaceElement}
                      onMouseMove={zoneDrawing.handleMouseMove}
                      onMouseUp={zoneDrawing.handleMouseUp}
                      onMouseLeave={zoneDrawing.handleMouseUp}
                      className="relative aspect-video overflow-hidden rounded-md border border-slate-800 bg-black"
                      style={{ aspectRatio: feedAspectRatio }}
                    >
                      {liveStream.isLive ? (
                        <LlHlsVideo
                          source={liveStream.liveUrl}
                          annotated
                          aspectRatio={feedAspectRatio}
                          className="absolute inset-0 h-full w-full object-contain"
                          onTimeline={(timeline) => liveStream.handleHlsTimeline(liveStream.liveUrl, timeline)}
                          onPlaybackMetrics={(metrics) => liveStream.handlePlaybackMetrics(liveStream.liveUrl, metrics)}
                          onVideoElement={liveStream.setVideoElement}
                          onSurfaceElement={zoneDrawing.setSurfaceElement}
                        >
                          {currentZoneEnabled || zoneDrawing.isDrawing || zoneDrawing.pendingAutoZoneIds.size > 0 ? (
                            <ZoneOverlaySvg zoneDrawing={zoneDrawing} zoneColors={zoneColors} />
                          ) : null}
                        </LlHlsVideo>
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
                      {!liveStream.isLive && (currentZoneEnabled || zoneDrawing.isDrawing || zoneDrawing.pendingAutoZoneIds.size > 0) ? (
                        <ZoneOverlaySvg zoneDrawing={zoneDrawing} zoneColors={zoneColors} />
                      ) : null}
                      {!zoneDrawing.isDrawing && !liveStream.isLive ? (
                        <TrackingOverlayLayer
                          overlay={visibleTrackingOverlay}
                          currentTime={liveStream.currentVideoTime}
                          behaviorDetections={currentFallEnabled ? liveStream.streamData.fall_detections : []}
                          behaviorEnabled={currentFallEnabled}
                          currentFrameIndex={currentFrameIndex}
                          currentSourceTimeMs={liveStream.currentSourceTimeMs}
                          onPresentation={(selection) =>
                            liveStream.handleOverlayPresentation(liveStream.liveUrl, selection)
                          }
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
                  </div>
                </div>
              )
            ) : null}
          </div>
        ) : null}

        {/* surface="dark": these sit inside the camera panel, not on the page body. */}
        {phase === "loading" && (currentPpeEnabled || currentZoneEnabled || currentFallEnabled) ? <LoadingState text="Running inference..." surface="dark" /> : null}
        {phase === "error" ? <ErrorState text={error} surface="dark" /> : null}
        {status ? <EmptyState text={status} surface="dark" /> : null}

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

const SAFETY_EVENTS_QUERY_KEY = ["safety-events"] as const;
const INCIDENTS_PAGE_SIZE = 6;
// Minimum time the paging indicator stays up. The request itself is usually
// faster than this, and a flash too brief to read is the same as no feedback at
// all — so the indicator is held for this long even once the data has arrived.
const PAGE_INDICATOR_MIN_MS = 2500;

const CATEGORY_FILTER_OPTIONS: IncidentCategory[] = ["ppe", "zone", "behavior"];
const CATEGORY_FILTER_ICON: Record<IncidentCategory, ReactNode> = {
  ppe: <HardHat className="size-3.5" aria-hidden="true" />,
  zone: <MapPinned className="size-3.5" aria-hidden="true" />,
  behavior: <PersonStanding className="size-3.5" aria-hidden="true" />,
};

type TimePreset = "all" | "24h" | "7d" | "30d" | "custom";
const TIME_PRESET_OPTIONS: { value: TimePreset; label: string }[] = [
  { value: "all", label: "All time" },
  { value: "24h", label: "24 hours" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "custom", label: "Custom" },
];
const TIME_PRESET_HOURS: Record<"24h" | "7d" | "30d", number> = {
  "24h": 24,
  "7d": 24 * 7,
  "30d": 24 * 30,
};

/** <input type="date"> yields "YYYY-MM-DD", and the backend expects an ISO
 * instant — this is the one spot that conversion happens. Blank/unparseable
 * input means that bound isn't set.
 *
 * Built from parts rather than `new Date(value)` on purpose: the spec parses a
 * bare date string as *UTC* midnight, which would shift both bounds by the
 * local offset and silently drop incidents near either edge of the range.
 *
 * `end` resolves to the last millisecond of the chosen day so the To bound is
 * inclusive — picking the same day for From and To means "that whole day",
 * not an empty zero-width range. */
function localDayToIso(value: string, bound: "start" | "end"): string | undefined {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return undefined;
  const [, year, month, day] = match;
  const parsed =
    bound === "start"
      ? new Date(Number(year), Number(month) - 1, Number(day), 0, 0, 0, 0)
      : new Date(Number(year), Number(month) - 1, Number(day), 23, 59, 59, 999);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString();
}

function toggleInList<T>(list: T[], value: T, canonicalOrder: readonly T[]): T[] {
  const next = list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
  return canonicalOrder.filter((v) => next.includes(v));
}

/** Kept as a plain function (not inlined into the component) so the
 * Date.now() read isn't attributed to render — this is the boundary the
 * purity lint rule treats as opaque. */
function computeIncidentDateRange(
  timePreset: TimePreset,
  customFrom: string,
  customTo: string,
): { dateFrom: string | undefined; dateTo: string | undefined } {
  if (timePreset === "all") return { dateFrom: undefined, dateTo: undefined };
  if (timePreset === "custom") {
    return {
      dateFrom: localDayToIso(customFrom, "start"),
      dateTo: localDayToIso(customTo, "end"),
    };
  }
  const since = new Date(Date.now() - TIME_PRESET_HOURS[timePreset] * 3_600_000);
  return { dateFrom: since.toISOString(), dateTo: undefined };
}

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

function FilterRow({
  label,
  icon,
  children,
}: {
  label: string;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 py-2.5">
      {/* w-24, not w-20: at the 12px floor "SEVERITY" plus its icon no longer
          fits 5rem, and the pills would lose their shared left edge. */}
      <span className="flex w-24 shrink-0 items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {icon}
        {label}
      </span>
      <div className="flex flex-1 flex-wrap items-center gap-1.5">{children}</div>
    </div>
  );
}

function FilterPill({
  active,
  onClick,
  children,
  icon,
  dotClassName,
  activeClassName,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
  icon?: ReactNode;
  /** Small color dot shown while inactive — used on severity chips so the
   * severity -> color mapping reads even before a chip is selected. */
  dotClassName?: string;
  /** Overrides the default neutral slate/lime active fill — used on
   * severity chips so "active" also communicates which severity. */
  activeClassName?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-semibold ring-1 ring-inset transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lime-400 focus-visible:ring-offset-1 ${
        active
          ? (activeClassName ?? "bg-slate-950 text-lime-200 ring-slate-950") + " shadow-sm"
          : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50 hover:text-slate-900 hover:ring-slate-300"
      }`}
    >
      {dotClassName ? (
        <span className={`size-1.5 shrink-0 rounded-full ${dotClassName}`} aria-hidden="true" />
      ) : null}
      {icon}
      {children}
    </button>
  );
}

function IncidentPanel() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  // False only while the indicator is serving out PAGE_INDICATOR_MIN_MS. The
  // busy state is derived from this *and* the real request below, so a slow
  // response extends it rather than being cut short by the timer.
  const [floorElapsed, setFloorElapsed] = useState(true);
  // The page that stays rendered while a change is in flight — see shownPage
  // below. State, not a ref: it decides what the list renders, and reading a
  // ref during render can leave that list stale when React re-renders for an
  // unrelated reason.
  const [heldPage, setHeldPage] = useState<SafetyEventsPage | null>(null);

  // Filters. Empty categoryFilter/severityFilter means "all" — matches the
  // backend's own semantics (see FeedFilters in incident_feed_service.py), so
  // there's no separate "all selected" state to keep in sync.
  const [categoryFilter, setCategoryFilter] = useState<IncidentCategory[]>([]);
  const [severityFilter, setSeverityFilter] = useState<Severity[]>([]);
  const [timePreset, setTimePreset] = useState<TimePreset>("all");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const activeFilterCount =
    (categoryFilter.length > 0 ? 1 : 0) +
    (severityFilter.length > 0 ? 1 : 0) +
    (timePreset !== "all" ? 1 : 0);
  const hasActiveFilters = activeFilterCount > 0;

  // Collapsed on first render (SSR-safe default), then reconciled with the
  // visitor's last choice after mount — same pattern as loadStoredViewMode
  // below. Most tab visits don't touch filters, so starting open would cost
  // everyone panel space for a feature most sessions never use.
  const [filtersExpanded, setFiltersExpanded] = useState(false);
  useLayoutEffect(() => {
    const stored = loadStoredFiltersExpanded();
    if (stored !== null) setFiltersExpanded(stored);
  }, []);
  const toggleFiltersExpanded = () => {
    setFiltersExpanded((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(FILTERS_EXPANDED_STORAGE_KEY, String(next));
      } catch {
        // Storage unavailable (private browsing, quota) — the toggle still
        // works for this render, it just won't be remembered next visit.
      }
      return next;
    });
  };

  const { dateFrom, dateTo } = useMemo(
    () => computeIncidentDateRange(timePreset, customFrom, customTo),
    [timePreset, customFrom, customTo],
  );

  // Any filter change starts back at page 1 — a filter narrowing the result
  // set can easily leave the current page past the new last page.
  const applyFilterChange = (update: () => void) => {
    update();
    setPage(1);
  };

  const eventsQuery = useQuery({
    // Page and every filter are part of the key, so each combination is
    // cached separately and revisiting one already seen is instant.
    queryKey: [
      ...SAFETY_EVENTS_QUERY_KEY,
      page,
      INCIDENTS_PAGE_SIZE,
      categoryFilter,
      severityFilter,
      dateFrom,
      dateTo,
    ],
    queryFn: () =>
      getSafetyEventsPage(page, INCIDENTS_PAGE_SIZE, {
        categories: categoryFilter,
        severities: severityFilter,
        dateFrom,
        dateTo,
      }),
    // Keeps the current page rendered while the next one loads, instead of
    // collapsing to the empty/loading state on every page or filter change.
    placeholderData: keepPreviousData,
  });
  // Busy while either the request is running or the indicator's minimum has
  // not elapsed — so a slow response extends the state instead of the timer
  // cutting it short.
  const isPaging = !floorElapsed || eventsQuery.isFetching;
  // Paging state comes from the response, not from local arithmetic: the server
  // clamps an out-of-range page (rows deleted since the controls were rendered)
  // and owns the true total, so trusting its numbers keeps the controls honest
  // without a second round of clamping here.
  //
  // While a page change is in flight the *outgoing* page stays rendered
  // (heldPage, captured on click). Without that the new rows appeared as
  // soon as the request resolved while the indicator was still counting out
  // its minimum, which reads as a lie — results on screen under a "fetching"
  // banner. Now the swap and the indicator end together.
  const shownPage = isPaging ? heldPage ?? eventsQuery.data : eventsQuery.data;
  const pageEvents = shownPage?.items ?? [];
  const totalEvents = shownPage?.total ?? 0;
  const totalPages = shownPage?.total_pages ?? 1;
  const currentPage = shownPage?.page ?? page;
  // The banner names where you are *going*, so it reads the requested page
  // rather than the response's — with keepPreviousData the response still holds
  // the outgoing page for the whole fetch, which had the banner announcing the
  // page you were leaving. Clamped because the request may overshoot the end.
  const pendingPage = Math.min(page, eventsQuery.data?.total_pages ?? totalPages);
  const camerasQuery = useQuery({
    queryKey: ["cameras"],
    queryFn: getCameras,
    staleTime: 60_000,
  });
  const cameraNameBySource = useMemo(
    () => new Map((camerasQuery.data ?? []).map((camera) => [camera.source_key, camera.name])),
    [camerasQuery.data],
  );
  const [deleteAllError, setDeleteAllError] = useState("");
  const [confirmDeleteAll, setConfirmDeleteAll] = useState(false);
  const [deletingAll, setDeletingAll] = useState(false);
  const [selectedIncident, setSelectedIncident] = useState<{
    category: IncidentCategory;
    id: number;
  } | null>(null);
  // The pagination controls sit below a full page of incidents, so paging from
  // there leaves the viewport at the bottom of the list — showing the middle of
  // the new page. Scroll the panel heading back into view on every page change.
  const panelRef = useRef<HTMLElement | null>(null);
  // Which control started the move, so the spinner replaces the icon on the
  // button that was actually pressed rather than on all of them at once.
  const [pagingControl, setPagingControl] = useState<"prev" | "next" | number | null>(null);
  const pagingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (pagingTimerRef.current) clearTimeout(pagingTimerRef.current);
  }, []);
  const goToPage = (next: number, control: "prev" | "next" | number) => {
    setPage(next);
    setPagingControl(control);
    // Reduced motion suppresses the travel, not the feedback: the scroll jumps
    // instead of animating, but the indicator (and its spinners) still run —
    // a busy spinner is feedback, and suppressing it left the loading state
    // looking like a frozen icon.
    const reducedMotion =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    panelRef.current?.scrollIntoView({
      behavior: reducedMotion ? "auto" : "smooth",
      block: "start",
    });
    // Snapshot what is on screen now; it keeps rendering until the indicator
    // clears, so the reader never sees new rows under a "fetching" banner.
    setHeldPage(shownPage ?? null);
    setFloorElapsed(false);
    if (pagingTimerRef.current) clearTimeout(pagingTimerRef.current);
    pagingTimerRef.current = setTimeout(() => {
      setFloorElapsed(true);
      setPagingControl(null);
      // Releases the snapshot so the incoming page takes over. If the request
      // is somehow still running, keepPreviousData keeps the same rows on
      // screen anyway, so the handover is seamless either way.
      setHeldPage(null);
    }, PAGE_INDICATOR_MIN_MS);
  };
  // isFetching (not isPending) so the refresh icon still spins on a manual
  // refresh click, and on the background refetch React Query fires every
  // time this panel remounts (switching tabs away and back). That refetch
  // keeps the previous page's cached data on screen the whole time — so the
  // big "Loading recent incidents..." message below is deliberately gated
  // on having no data yet, rather than on `loading` alone, or it would
  // reappear over already-populated content on every single tab switch.
  const loading = eventsQuery.isFetching || deletingAll;
  // Gated on already having data so the very first load keeps its own
  // "Loading recent incidents…" state instead of showing both at once.
  const showPagingIndicator = Boolean(shownPage) && isPaging;
  const error =
    deleteAllError ||
    (eventsQuery.isError
      ? eventsQuery.error instanceof Error
        ? eventsQuery.error.message
        : "Could not load violations"
      : "");

  // Every page is its own cache entry now, and removing a row reflows all of
  // them (and changes the total), so the whole feed is refetched rather than
  // patched in place.
  const invalidateEvents = () => {
    void queryClient.invalidateQueries({ queryKey: SAFETY_EVENTS_QUERY_KEY });
  };

  const deleteEvent = async (event: ViolationReport | ZoneViolation | BehaviorIncident) => {
    if (!event.id || !confirm(CONFIRM_DELETE_INCIDENT)) {
      return;
    }
    const category: IncidentCategory =
      "violation_type" in event ? "ppe" : "behavior_type" in event ? "behavior" : "zone";
    await deleteIncident(category, event.id);
    invalidateEvents();
  };

  // Shared by the filter bar's "Clear filters" and the filtered empty state,
  // so the two can't drift apart on what "clear" means.
  const clearFilters = () =>
    applyFilterChange(() => {
      setCategoryFilter([]);
      setSeverityFilter([]);
      setTimePreset("all");
      setCustomFrom("");
      setCustomTo("");
    });

  const deleteAllEvents = async () => {
    setDeletingAll(true);
    setDeleteAllError("");
    try {
      await deleteAllIncidents();
      setPage(1);
      invalidateEvents();
      setConfirmDeleteAll(false);
    } catch (err) {
      setDeleteAllError(err instanceof Error ? err.message : "Could not delete incidents");
      setConfirmDeleteAll(false);
    } finally {
      setDeletingAll(false);
    }
  };

  return (
    <section
      ref={panelRef}
      className="scroll-mt-20 rounded-md border border-slate-200 bg-slate-50 p-3 shadow-sm"
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-950">Recent Incidents</h2>
          <p className="text-xs text-slate-500">Loaded from PPE, zone, and behavior incident stores.</p>
        </div>
        {/* Refresh sits alone here. Delete All used to be its immediate
            neighbour, which put an irreversible action one slipped click from
            the control people press most — it now lives at the foot of the
            panel, away from the routine controls. */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => void eventsQuery.refetch()}
            className="rounded-md border border-slate-200 bg-white p-2 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
            type="button"
            aria-label="Refresh incidents"
          >
            <RefreshCw className={`size-3.5 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
          </button>
        </div>
      </div>
      <div className="mb-3 rounded-lg border border-slate-200 bg-white shadow-sm">
        <div className="flex items-center justify-between gap-3 px-3 py-2">
          <button
            type="button"
            onClick={toggleFiltersExpanded}
            aria-expanded={filtersExpanded}
            aria-controls="incident-filters-body"
            className="flex items-center gap-1.5 rounded-md text-xs font-semibold uppercase tracking-wide text-slate-500 transition-colors duration-150 hover:text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lime-400 focus-visible:ring-offset-1"
          >
            <SlidersHorizontal className="size-3.5 text-slate-500" aria-hidden="true" />
            Filters
            {hasActiveFilters ? (
              <span className="rounded-full bg-lime-100 px-1.5 py-0.5 text-xs font-bold normal-case tracking-normal text-lime-800">
                {activeFilterCount} active
              </span>
            ) : null}
            <ChevronDown
              className={`size-3.5 text-slate-500 transition-transform duration-200 motion-reduce:transition-none ${filtersExpanded ? "rotate-180" : ""}`}
              aria-hidden="true"
            />
          </button>
          {hasActiveFilters ? (
            <button
              type="button"
              onClick={clearFilters}
              className="flex items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold text-slate-500 transition-colors duration-150 hover:bg-slate-100 hover:text-slate-950"
            >
              <X className="size-3" aria-hidden="true" />
              Clear filters
            </button>
          ) : null}
        </div>
        <div
          id="incident-filters-body"
          className={`grid transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none ${filtersExpanded ? "grid-rows-[1fr]" : "grid-rows-[0fr]"}`}
        >
          <div className="overflow-hidden">
            <div className="divide-y divide-slate-100 border-t border-slate-100 px-3">
              <FilterRow label="Type" icon={<HardHat className="size-3.5" aria-hidden="true" />}>
                <FilterPill
                  active={categoryFilter.length === 0}
                  onClick={() => applyFilterChange(() => setCategoryFilter([]))}
                >
                  All
                </FilterPill>
                {CATEGORY_FILTER_OPTIONS.map((cat) => (
                  <FilterPill
                    key={cat}
                    active={categoryFilter.includes(cat)}
                    icon={CATEGORY_FILTER_ICON[cat]}
                    onClick={() =>
                      applyFilterChange(() =>
                        setCategoryFilter((prev) =>
                          toggleInList(prev, cat, CATEGORY_FILTER_OPTIONS),
                        ),
                      )
                    }
                  >
                    {CATEGORY_LABEL[cat]}
                  </FilterPill>
                ))}
              </FilterRow>
              <FilterRow
                label="Severity"
                icon={<ShieldAlert className="size-3.5" aria-hidden="true" />}
              >
                <FilterPill
                  active={severityFilter.length === 0}
                  onClick={() => applyFilterChange(() => setSeverityFilter([]))}
                >
                  All
                </FilterPill>
                {VALID_SEVERITIES.map((sev) => (
                  <FilterPill
                    key={sev}
                    active={severityFilter.includes(sev)}
                    dotClassName={
                      severityFilter.includes(sev) ? undefined : SEVERITY_DOT_CLASS[sev]
                    }
                    activeClassName={SEVERITY_ACTIVE_CLASS[sev]}
                    onClick={() =>
                      applyFilterChange(() =>
                        setSeverityFilter((prev) => toggleInList(prev, sev, VALID_SEVERITIES)),
                      )
                    }
                  >
                    {sev}
                  </FilterPill>
                ))}
              </FilterRow>
              <FilterRow label="Time" icon={<Clock className="size-3.5" aria-hidden="true" />}>
                {TIME_PRESET_OPTIONS.map((opt) => (
                  <FilterPill
                    key={opt.value}
                    active={timePreset === opt.value}
                    onClick={() => applyFilterChange(() => setTimePreset(opt.value))}
                  >
                    {opt.label}
                  </FilterPill>
                ))}
              </FilterRow>
            </div>
            {timePreset === "custom" ? (
              <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 bg-slate-50/80 px-3 py-2.5">
                <span className="w-20 shrink-0" aria-hidden="true" />
                <div className="flex flex-wrap items-center gap-4">
                  <label className="flex items-center gap-2 text-xs font-medium text-slate-600">
                    From
                    <input
                      type="date"
                      value={customFrom}
                      max={customTo || undefined}
                      onChange={(e) => applyFilterChange(() => setCustomFrom(e.target.value))}
                      className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-800 shadow-sm transition-colors focus:border-slate-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-lime-400"
                    />
                  </label>
                  <label className="flex items-center gap-2 text-xs font-medium text-slate-600">
                    To
                    <input
                      type="date"
                      value={customTo}
                      min={customFrom || undefined}
                      onChange={(e) => applyFilterChange(() => setCustomTo(e.target.value))}
                      className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-800 shadow-sm transition-colors focus:border-slate-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-lime-400"
                    />
                  </label>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </div>
      {showPagingIndicator ? (
        <div
          className="mb-3 flex items-center gap-3 rounded-md border-2 border-lime-300 bg-lime-50 px-4 py-4 text-sm font-semibold text-slate-800 shadow-sm"
          role="status"
          aria-live="polite"
        >
          <Loader2
            className="size-5 shrink-0 animate-spin text-lime-600"
            aria-hidden="true"
          />
          <span>
            Fetching results · page {pendingPage} of{" "}
            {eventsQuery.data?.total_pages ?? totalPages}
          </span>
        </div>
      ) : null}
      {loading && !eventsQuery.data ? <LoadingState text="Loading recent incidents..." /> : null}
      {error ? <ErrorState text={error} onRetry={() => void eventsQuery.refetch()} /> : null}
      {!loading && !error && totalEvents === 0 ? (
        <EmptyState
          text={
            hasActiveFilters
              ? "No incidents match the selected filters."
              : "No incidents have been recorded yet."
          }
          action={
            hasActiveFilters ? { label: "Clear filters", onClick: clearFilters } : undefined
          }
        />
      ) : null}
      <div className="grid gap-2">
        {pageEvents.map((event, index) => (
          <IncidentCard
            key={`${event.id ?? index}-${event.timestamp}`}
            event={event}
            cameraName={cameraNameBySource.get(event.video_name ?? "") ?? "Unknown camera"}
            showMetadata={false}
            onDelete={() => void deleteEvent(event)}
            onOpenDetail={(category, id) => setSelectedIncident({ category, id })}
          />
        ))}
      </div>
      {totalPages > 1 ? (
        <div className="mt-3 flex items-center justify-between gap-2 border-t border-slate-200 pt-3">
          <p className="text-xs text-slate-500">
            Page {currentPage} of {totalPages} · {totalEvents} incident
            {totalEvents === 1 ? "" : "s"}
          </p>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => goToPage(Math.max(1, currentPage - 1), "prev")}
              disabled={currentPage === 1 || isPaging}
              aria-label="Previous page"
              aria-busy={isPaging && pagingControl === "prev"}
              className="rounded-md border border-slate-200 bg-white p-1.5 text-slate-500 transition hover:border-slate-300 hover:text-slate-950 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {isPaging && pagingControl === "prev" ? (
                <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <ChevronLeft className="size-3.5" aria-hidden="true" />
              )}
            </button>
            {getPageWindow(currentPage, totalPages).map((p, i) =>
              p === "…" ? (
                <span key={`ellipsis-${i}`} className="px-1 text-xs text-slate-500">
                  …
                </span>
              ) : (
                <button
                  key={p}
                  type="button"
                  onClick={() => goToPage(p, p)}
                  disabled={isPaging}
                  aria-current={p === currentPage ? "page" : undefined}
                  aria-busy={isPaging && pagingControl === p}
                  className={`flex min-w-[1.75rem] items-center justify-center rounded-md px-2 py-1 text-xs font-semibold transition disabled:cursor-not-allowed ${
                    p === currentPage ? "bg-slate-950 text-lime-200" : "text-slate-500 hover:bg-slate-100"
                  }`}
                >
                  {isPaging && pagingControl === p ? (
                    <Loader2 className="size-3 animate-spin" aria-hidden="true" />
                  ) : (
                    p
                  )}
                </button>
              ),
            )}
            <button
              type="button"
              onClick={() => goToPage(Math.min(totalPages, currentPage + 1), "next")}
              disabled={currentPage === totalPages || isPaging}
              aria-label="Next page"
              aria-busy={isPaging && pagingControl === "next"}
              className="rounded-md border border-slate-200 bg-white p-1.5 text-slate-500 transition hover:border-slate-300 hover:text-slate-950 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {isPaging && pagingControl === "next" ? (
                <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <ChevronRight className="size-3.5" aria-hidden="true" />
              )}
            </button>
          </div>
        </div>
      ) : null}
      {totalEvents > 0 ? (
        <div className="mt-4 flex items-center justify-between gap-3 border-t border-slate-200 pt-3">
          <p className="text-xs text-slate-500">
            Clearing the log removes your compliance history. This cannot be undone.
          </p>
          <button
            onClick={() => setConfirmDeleteAll(true)}
            disabled={loading || deletingAll}
            className="flex shrink-0 items-center gap-1.5 rounded-md border border-red-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-red-700 transition hover:border-red-300 hover:bg-red-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-400"
            type="button"
          >
            <Trash2 className="size-3.5" aria-hidden="true" />
            <span>Delete all incidents</span>
          </button>
        </div>
      ) : null}
      {confirmDeleteAll ? (
        <ConfirmDeleteAllDialog
          count={totalEvents}
          deleting={deletingAll}
          onConfirm={() => void deleteAllEvents()}
          onCancel={() => setConfirmDeleteAll(false)}
        />
      ) : null}
      {selectedIncident ? (
        <IncidentDetailModal
          key={`${selectedIncident.category}-${selectedIncident.id}`}
          category={selectedIncident.category}
          incidentId={selectedIncident.id}
          onClose={() => setSelectedIncident(null)}
          onDeleted={invalidateEvents}
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
  const [activeCameraId, setActiveCameraId] = useState<number>(0);
  const [cameras, setCameras] = useState<CameraConfig[]>([]);
  // Gates the FileUpload/"Uploaded Feed" panel so it doesn't flash on screen
  // while camera IDs are being resolved, before the live stream WebSockets
  // have had a chance to connect and flip liveStream.isLive.
  const [camerasLoading, setCamerasLoading] = useState(true);

  // Seeds `cameras` from last session's already-resolved list (if any)
  // *before the browser paints*, so a reload can start streaming on the
  // first visible frame instead of showing "Connecting to cameras..." while
  // the mount effect below re-resolves IDs it already resolved before.
  // Deliberately a layout effect, not a useState lazy initializer: this
  // component is SSR'd (no localStorage on the server), so seeding via the
  // initializer would render different content server- vs client-side on
  // the very first pass and trip a hydration mismatch. A layout effect runs
  // after the (SSR-matching) first paint is committed but before the
  // browser actually paints it, so the correction is invisible.
  useLayoutEffect(() => {
    const cached = loadCachedCameras();
    if (cached.length === 0) return;
    setCameras(cached);
    const active = cached.find((camera) => camera.active) || cached[0];
    setActiveCameraId(active?.id ?? 0);
    setCamerasLoading(false);
  }, []);
  const [physicalZones, setPhysicalZones] = useState<PhysicalZone[]>([]);
  const [zonesLoaded, setZonesLoaded] = useState(false);
  // Same hook and default range/zone (7D, all zones) as the Incident
  // Analytics tab's KPI row, so the two stay in sync instead of this one
  // showing static placeholder data.
  const { kpis, summary: kpiSummary, isError: kpiError } = useSafetyKpis();

  useEffect(() => {
    let cancelled = false;
    const stored = localStorage.getItem("ppe_demo_cameras");
    let loaded: CameraConfig[] = DEFAULT_CAMERAS;
    if (stored) {
      try {
        loaded = JSON.parse(stored);
      } catch {
        loaded = DEFAULT_CAMERAS;
      }
    } else {
      localStorage.setItem("ppe_demo_cameras", JSON.stringify(DEFAULT_CAMERAS));
    }
    loaded = normalizeConfiguredCameras(loaded);

    // `zonesLoaded` gates the sidebar's empty state. Without it an unresolved
    // fetch looks identical to "this factory has no zones", and the sidebar
    // told the user to go create zones that already existed.
    void getPhysicalZones()
      .then(setPhysicalZones)
      .catch(() => {})
      .finally(() => setZonesLoaded(true));

    // Re-resolve every source to its database camera in the background. When
    // `cameras` was already seeded from a cached, previously-resolved list
    // (loadCachedCameras above), streaming has already started by the time
    // this resolves — this call exists to self-heal drift (a camera
    // renamed/recreated server-side, changing its id) rather than gate the
    // first paint. On a true first-ever visit (no cache yet), `cameras`
    // starts empty and this is what populates it.
    void Promise.all(
      loaded.map(async (camera) => {
        const backendCamera = await ensureCamera(camera.name, camera.rtspUrl);
        return {
          ...camera,
          id: backendCamera.id,
          homeZoneId: backendCamera.home_zone_id,
        };
      }),
    )
      .then((reconciled) => {
        if (cancelled) return;
        localStorage.setItem("ppe_demo_cameras", JSON.stringify(reconciled));
        setCameras(reconciled);
        const active = reconciled.find((camera) => camera.active) || reconciled[0];
        setActiveCameraId(active?.id ?? 0);
        setCamerasLoading(false);
      })
      .catch((error) => {
        if (cancelled) return;
        console.error("Could not reconcile camera IDs with the backend", error);
        localStorage.setItem("ppe_demo_cameras", JSON.stringify(loaded));
        setCameras(loaded);
        const active = loaded.find((camera) => camera.active) || loaded[0];
        setActiveCameraId(active?.id ?? 0);
        setCamerasLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);
  const cameraCountsByZone = useMemo(() => {
    const counts: Record<number, number> = {};
    for (const cam of cameras) {
      // Only active cameras count here — the sidebar's green/gray dot and
      // camera count are meant to reflect what's actually watching the
      // zone right now, not just what's assigned to it.
      if (cam.homeZoneId != null && cam.active) {
        counts[cam.homeZoneId] = (counts[cam.homeZoneId] ?? 0) + 1;
      }
    }
    return counts;
  }, [cameras]);

  const pageTitle =
    activeView === "violations"
      ? "Live Incident Panel"
      : activeView === "factory3d"
        ? "Factory 3D Map"
        : activeView === "analytics"
          ? "Incident Analytics"
          : "Safety Cameras Feed";

  return (
    <div className="min-h-screen bg-slate-100 text-slate-950">
      <TopBar activeView={activeView} onViewChange={setActiveView} />
      <div className="lg:flex">
        <ZoneSidebar
          physicalZones={physicalZones}
          zonesLoaded={zonesLoaded}
          cameraCounts={cameraCountsByZone}
          onPhysicalZonesUpdate={setPhysicalZones}
        />
        <main className="min-w-0 flex-1 p-3 lg:p-4">
          <div className="flex w-full flex-col gap-4">
            {/* Title and KPI tiles share one row on wide screens. Stacked,
                they were two near-empty bands: a title bar holding one line of
                text, then two cards stretched to half the page each with the
                number in the top-left corner. The tiles take a fixed 15rem
                track so they stay dense as more metrics arrive, and the title
                bar absorbs the remaining width. Everything here is p-3 to keep
                one left edge down the page. */}
            <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[minmax(16rem,1fr)_repeat(2,15rem)]">
              <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-md border border-slate-200 bg-white p-3 shadow-sm sm:col-span-2 lg:col-span-1">
                <h2 className="text-2xl font-semibold tracking-normal text-slate-950 md:text-3xl">
                  {pageTitle}
                </h2>
                <MonitoringStatus summary={kpiSummary} isError={kpiError} />
              </div>
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
                    <AnalyticsDashboard embedded isVisible={activeView === "analytics"} />
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
                    camerasLoading={camerasLoading}
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

