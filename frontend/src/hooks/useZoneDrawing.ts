import { useEffect, useMemo, useState } from "react";
import { deleteZonesForVideo, getZones, saveZone } from "@/lib/ppe-api";
import { doPolygonsOverlap } from "@/lib/spatial-utils";
import { Point2D, ZoneConfiguration, ZoneType } from "@/types/zone";
import { AnalysisPhase, DraftZone } from "./camera-panel-types";

type DragState = {
  type: "point" | "zone" | "curve";
  zoneId: string;
  pointIndex?: number;
  startPos: Point2D;
} | null;

export function clamp01(value: number) {
  return Math.max(0, Math.min(1, value));
}

function zoneFromBackend(zone: ZoneConfiguration): DraftZone {
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
  } catch {
    // Fallback to flattened coordinates
  }

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
    flattened_coordinates: JSON.stringify(flattenPoints(zone.points)),
  };
}

function flattenPoints(points: Point2D[]): Point2D[] {
  const result: Point2D[] = [];
  for (let i = 0; i < points.length; i++) {
    const p1 = points[i];
    const p2 = points[(i + 1) % points.length];
    result.push({ x: p1.x, y: p1.y });

    if (p1.curveControl) {
      // Approximate Quadratic Bezier with 8 segments
      for (let t = 0.125; t < 1; t += 0.125) {
        const invT = 1 - t;
        const x = invT * invT * p1.x + 2 * invT * t * p1.curveControl.x + t * t * p2.x;
        const y = invT * invT * p1.y + 2 * invT * t * p1.curveControl.y + t * t * p2.y;
        result.push({ x, y });
      }
    }
  }
  return result;
}

function safeParsePoints(value: string): Point2D[] {
  try {
    const parsed = JSON.parse(value) as Point2D[];
    return Array.isArray(parsed)
      ? parsed.filter((point) => point && typeof point.x === "number" && typeof point.y === "number")
      : [];
  } catch {
    return [];
  }
}

export function useZoneDrawing({
  sourceKey,
  phase,
  isStreaming,
  onZonesPersisted,
  setStatus,
  setPhase,
  setError,
}: {
  sourceKey: string | undefined;
  phase: AnalysisPhase;
  isStreaming: boolean;
  onZonesPersisted?: () => void;
  setStatus: (status: string) => void;
  setPhase: (phase: AnalysisPhase) => void;
  setError: (message: string) => void;
}) {
  const [isDrawing, setIsDrawing] = useState(false);
  const [configMode, setConfigMode] = useState<"draw" | "modify">("draw");
  const [isAddingPoint, setIsAddingPoint] = useState(false);
  const [isDrawingCurve, setIsDrawingCurve] = useState(false);
  const [selectedZoneId, setSelectedZoneId] = useState<string | null>(null);
  const [dragState, setDragState] = useState<DragState>(null);
  const [zonesForVideo, setZonesForVideo] = useState<DraftZone[]>([]);
  const [draftPoints, setDraftPoints] = useState<Point2D[]>([]);
  const [zoneName, setZoneName] = useState("Restricted Area");
  const [zoneType, setZoneType] = useState<ZoneType>("RESTRICTED");
  const [dwellThresholdSeconds, setDwellThresholdSeconds] = useState(1.5);
  const [pendingAutoZoneIds, setPendingAutoZoneIds] = useState<Set<string>>(new Set());
  const [zoneActionState, setZoneActionState] = useState<
    "idle" | "saving" | "saved" | "clearing" | "cleared" | "error"
  >("idle");
  const [surfaceElement, setSurfaceElement] = useState<HTMLDivElement | null>(null);

  useEffect(() => {
    // WALKWAY tolerates longer presence; RESTRICTED and SLIPPERY are hazard
    // zones monitored with the shorter dwell threshold.
    setDwellThresholdSeconds(zoneType === "WALKWAY" ? 3 : 1.5);
  }, [zoneType]);

  useEffect(() => {
    if (configMode === "modify" && selectedZoneId) {
      const selectedZone = zonesForVideo.find((z) => z.id === selectedZoneId);
      if (selectedZone) {
        setZoneName(selectedZone.name);
        setZoneType(selectedZone.type);
      }
    }
  }, [selectedZoneId, configMode, zonesForVideo]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (
        e.key === "Delete" &&
        selectedZoneId &&
        configMode === "modify" &&
        isDrawing
      ) {
        setZonesForVideo((prev) => prev.filter((z) => z.id !== selectedZoneId));
        setSelectedZoneId(null);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [selectedZoneId, configMode, isDrawing]);

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

  const resetZoneDrawing = () => {
    setZonesForVideo([]);
    setDraftPoints([]);
    setSelectedZoneId(null);
    setIsDrawing(false);
  };

  const addZonePoint = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!surfaceElement || (phase === "loading" && !isStreaming) || !isDrawing) return;
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
    if (!sourceKey) return;
    await deleteZonesForVideo(sourceKey);
    for (const zone of zonesToPersist) {
      await saveZone(toBackendZone(zone, sourceKey));
    }
    setZonesForVideo(
      zonesToPersist.map((zone) => ({
        ...zone,
        id: zone.id === "draft" ? crypto.randomUUID() : zone.id,
      })),
    );
    setDraftPoints([]);
    setPendingAutoZoneIds(new Set());
    setStatus(`Saved ${zonesToPersist.length} zone(s) for this camera video.`);
    onZonesPersisted?.();
  };

  const handleSaveZones = async () => {
    let hasOverlap = false;
    for (let i = 0; i < zonesReadyToSave.length; i++) {
      for (let j = i + 1; j < zonesReadyToSave.length; j++) {
        const typeA = zonesReadyToSave[i].type;
        const typeB = zonesReadyToSave[j].type;
        const isWalkwaySlipperyPair =
          (typeA === "WALKWAY" && typeB === "SLIPPERY") ||
          (typeA === "SLIPPERY" && typeB === "WALKWAY");

        if (
          typeA !== typeB &&
          !isWalkwaySlipperyPair &&
          doPolygonsOverlap(zonesReadyToSave[i].points, zonesReadyToSave[j].points)
        ) {
          hasOverlap = true;
          break;
        }
      }
      if (hasOverlap) break;
    }

    if (hasOverlap) {
      window.alert(
        "Cannot save zones: Zones of different types (excluding Walkway & Slippery) overlap. Please adjust the vertices to avoid overlap.",
      );
      setZoneActionState("error");
      setTimeout(() => setZoneActionState("idle"), 2000);
      return;
    }

    setZoneActionState("saving");
    try {
      await Promise.all([persistZones(), new Promise((r) => setTimeout(r, 600))]);
      setZoneActionState("saved");
    } catch {
      setZoneActionState("error");
    } finally {
      setTimeout(() => setZoneActionState("idle"), 2000);
    }
  };

  const clearSavedZones = async () => {
    if (!sourceKey) return;
    setPhase("loading");
    setZoneActionState("clearing");
    setError("");
    try {
      await Promise.all([
        (async () => {
          await deleteZonesForVideo(sourceKey);
          setZonesForVideo([]);
          setDraftPoints([]);
          setPendingAutoZoneIds(new Set());
          setStatus("Cleared saved zones for this camera video.");
        })(),
        new Promise((r) => setTimeout(r, 600)),
      ]);
      setZoneActionState("cleared");
    } catch (err) {
      setZoneActionState("error");
      setError(err instanceof Error ? err.message : "Could not clear saved zones");
    } finally {
      setPhase("idle");
      setTimeout(() => setZoneActionState("idle"), 2000);
    }
  };

  const onPointMouseDown = (e: React.MouseEvent, zoneId: string, pointIndex: number) => {
    if (configMode !== "modify") return;
    e.stopPropagation();
    if (!surfaceElement) return;
    const rect = surfaceElement.getBoundingClientRect();
    setDragState({
      type: "point",
      zoneId,
      pointIndex,
      startPos: {
        x: (e.clientX - rect.left) / rect.width,
        y: (e.clientY - rect.top) / rect.height,
      },
    });
    setSelectedZoneId(zoneId);
  };

  const onZoneMouseDown = (e: React.MouseEvent, zoneId: string) => {
    if (configMode !== "modify") return;
    e.stopPropagation();
    if (!surfaceElement) return;
    const rect = surfaceElement.getBoundingClientRect();
    setDragState({
      type: "zone",
      zoneId,
      startPos: {
        x: (e.clientX - rect.left) / rect.width,
        y: (e.clientY - rect.top) / rect.height,
      },
    });
    setSelectedZoneId(zoneId);
  };

  const onEdgeClick = (e: React.MouseEvent, zoneId: string, edgeIndex: number) => {
    if (!isAddingPoint || !surfaceElement) return;
    e.stopPropagation();
    const rect = surfaceElement.getBoundingClientRect();
    const newPoint = {
      x: (e.clientX - rect.left) / rect.width,
      y: (e.clientY - rect.top) / rect.height,
    };

    setZonesForVideo((prev) =>
      prev.map((z) => {
        if (z.id !== zoneId) return z;
        const newPoints = [...z.points];
        newPoints.splice(edgeIndex + 1, 0, newPoint);
        return { ...z, points: newPoints };
      }),
    );
    setIsAddingPoint(false);
  };

  const onCurveEdgeMouseDown = (e: React.MouseEvent, zoneId: string, edgeIndex: number) => {
    if (!isDrawingCurve || !surfaceElement) return;
    e.stopPropagation();
    const rect = surfaceElement.getBoundingClientRect();
    setDragState({
      type: "curve",
      zoneId,
      pointIndex: edgeIndex,
      startPos: {
        x: (e.clientX - rect.left) / rect.width,
        y: (e.clientY - rect.top) / rect.height,
      },
    });
    setSelectedZoneId(zoneId);
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!dragState || !surfaceElement) return;
    const rect = surfaceElement.getBoundingClientRect();
    const currentPos = {
      x: (e.clientX - rect.left) / rect.width,
      y: (e.clientY - rect.top) / rect.height,
    };
    const dx = currentPos.x - dragState.startPos.x;
    const dy = currentPos.y - dragState.startPos.y;

    if (dragState.type === "point") {
      setZonesForVideo((prev) =>
        prev.map((z) => {
          if (z.id !== dragState.zoneId) return z;
          const newPoints = [...z.points];
          newPoints[dragState.pointIndex!] = {
            ...newPoints[dragState.pointIndex!],
            x: clamp01(newPoints[dragState.pointIndex!].x + dx),
            y: clamp01(newPoints[dragState.pointIndex!].y + dy),
          };
          return { ...z, points: newPoints };
        }),
      );
    } else if (dragState.type === "curve") {
      setZonesForVideo((prev) =>
        prev.map((z) => {
          if (z.id !== dragState.zoneId) return z;
          const newPoints = [...z.points];
          const pt = newPoints[dragState.pointIndex!];
          const newControl = {
            x: clamp01((pt.curveControl?.x ?? (pt.x + z.points[(dragState.pointIndex! + 1) % z.points.length].x) / 2) + dx),
            y: clamp01((pt.curveControl?.y ?? (pt.y + z.points[(dragState.pointIndex! + 1) % z.points.length].y) / 2) + dy),
          };
          newPoints[dragState.pointIndex!] = {
            ...pt,
            curveControl: newControl,
          };
          return { ...z, points: newPoints };
        }),
      );
    } else {
      setZonesForVideo((prev) =>
        prev.map((z) => {
          if (z.id !== dragState.zoneId) return z;
          const canMove = z.points.every(
            (p) =>
              p.x + dx >= 0 && p.x + dx <= 1 && p.y + dy >= 0 && p.y + dy <= 1,
          );
          if (!canMove) return z;
          return {
            ...z,
            points: z.points.map((p) => ({
              ...p,
              x: p.x + dx,
              y: p.y + dy,
              curveControl: p.curveControl
                ? { x: p.curveControl.x + dx, y: p.curveControl.y + dy }
                : undefined,
            })),
          };
        }),
      );
    }
    setDragState((prev) => (prev ? { ...prev, startPos: currentPos } : null));
  };

  const handleMouseUp = () => {
    setDragState(null);
  };

  const handleSurfaceClick = (event: React.MouseEvent<unknown>) => {
    if (configMode === "draw") {
      addZonePoint(event as React.MouseEvent<HTMLDivElement>);
    } else {
      if (event.target === event.currentTarget) {
        setSelectedZoneId(null);
        setIsAddingPoint(false);
      }
    }
  };

  return {
    isDrawing,
    setIsDrawing,
    configMode,
    setConfigMode,
    isAddingPoint,
    setIsAddingPoint,
    isDrawingCurve,
    setIsDrawingCurve,
    selectedZoneId,
    setSelectedZoneId,
    zonesForVideo,
    setZonesForVideo,
    draftPoints,
    setDraftPoints,
    zoneName,
    setZoneName,
    zoneType,
    setZoneType,
    dwellThresholdSeconds,
    pendingAutoZoneIds,
    setPendingAutoZoneIds,
    zoneActionState,
    surfaceElement,
    setSurfaceElement,
    zonesReadyToSave,
    displayedZones,
    loadSavedZones,
    resetZoneDrawing,
    finishZone,
    persistZones,
    handleSaveZones,
    clearSavedZones,
    onPointMouseDown,
    onZoneMouseDown,
    onEdgeClick,
    onCurveEdgeMouseDown,
    handleMouseMove,
    handleMouseUp,
    handleSurfaceClick,
  };
}
