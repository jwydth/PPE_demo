"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fabric } from "fabric";
import { UploadZone } from "./UploadZone";
import { Point2D, ZoneConfiguration, ZoneType } from "@/types/zone";
import { analyzeVideo, deleteZonesForVideo, API_URL } from "@/lib/api";
import { VideoProcessingResponse, ViolationReport } from "@/types/detection";

// --- Fabric.js Polygon Editing Helpers ---
const polygonPositionHandler = function (this: any, dim: any, finalMatrix: any, fabricObject: any) {
  const x = fabricObject.points[this.pointIndex].x - fabricObject.pathOffset.x;
  const y = fabricObject.points[this.pointIndex].y - fabricObject.pathOffset.y;
  return fabric.util.transformPoint(
    { x, y },
    fabric.util.multiplyTransformMatrices(
      fabricObject.canvas.viewportTransform,
      fabricObject.calcTransformMatrix()
    )
  );
};

const actionHandler = function (
  eventData: any,
  transform: any,
  x: number,
  y: number,
) {
  const polygon = transform.target;
  const canvas = polygon.canvas;

  if (!canvas) return false;

  // Use the pointer coordinates directly as they are in the canvas space
  // and clamp them to ensure the mouse proposed position is within bounds.
  const clampedX = Math.max(0, Math.min(x, canvas.getWidth()));
  const clampedY = Math.max(0, Math.min(y, canvas.getHeight()));

  const currentControl = polygon.controls[polygon.__corner];
  const mouseLocalPosition = polygon.toLocalPoint(
    new fabric.Point(clampedX, clampedY),
    "center",
    "center",
  );
  const polygonBaseSize = polygon._getNonTransformedDimensions();
  const size = polygon._getTransformedDimensions(0, 0);
  const finalPointPosition = {
    x:
      (mouseLocalPosition.x * polygonBaseSize.x) / size.x + polygon.pathOffset.x,
    y:
      (mouseLocalPosition.y * polygonBaseSize.y) / size.y + polygon.pathOffset.y,
  };
  polygon.points[currentControl.pointIndex] = finalPointPosition;
  return true;
};

const anchorWrapper = function (anchorIndex: number, fn: any) {
  return function (eventData: any, transform: any, x: number, y: number) {
    const fabObj = transform.target;
    const absolutePoint = fabric.util.transformPoint(
      {
        x: fabObj.points[anchorIndex].x - fabObj.pathOffset.x,
        y: fabObj.points[anchorIndex].y - fabObj.pathOffset.y,
      },
      fabObj.calcTransformMatrix(),
    );
    const actionPerformed = fn(eventData, transform, x, y);
    // @ts-ignore
    fabObj._setPositionDimensions({});
    const polygonBaseSize = fabObj._getNonTransformedDimensions();
    const newX =
      (fabObj.points[anchorIndex].x - fabObj.pathOffset.x) / polygonBaseSize.x;
    const newY =
      (fabObj.points[anchorIndex].y - fabObj.pathOffset.y) / polygonBaseSize.y;
    fabObj.setPositionByOrigin(absolutePoint, newX + 0.5, newY + 0.5);

    return actionPerformed;
  };
};

const enablePolygonEditing = (poly: fabric.Polygon) => {
  const points = poly.points || [];
  const lastControl = points.length - 1;
  poly.cornerStyle = 'circle';
  poly.cornerColor = 'rgba(249, 115, 22, 0.8)';
  poly.cornerStrokeColor = '#f97316';
  poly.cornerSize = 8;
  poly.transparentCorners = false;
  poly.objectCaching = false;

  poly.controls = points.reduce((acc: any, point: any, index: number) => {
    // @ts-ignore
    acc['p' + index] = new fabric.Control({
      positionHandler: polygonPositionHandler,
      actionHandler: anchorWrapper(index > 0 ? index - 1 : lastControl, actionHandler),
      actionName: 'modifyPolygon',
      // @ts-ignore
      pointIndex: index,
    });
    return acc;
  }, {});

  (poly as any).isEditing = true;
  poly.hasBorders = false;
};

const disablePolygonEditing = (poly: fabric.Polygon) => {
  poly.controls = fabric.Object.prototype.controls;
  poly.cornerStyle = 'rect';
  poly.cornerColor = 'rgb(178,204,255)';
  poly.hasBorders = true;
  poly.objectCaching = true;
  (poly as any).isEditing = false;
};
// ------------------------------------------

type Tool = "select" | "draw" | "delete";

export function ZoneDrawingCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const bboxCanvasRef = useRef<HTMLCanvasElement>(null);
  const changeMediaInputRef = useRef<HTMLInputElement>(null);
  const [fabricCanvas, setFabricCanvas] = useState<fabric.Canvas | null>(null);
  const [tool, setTool] = useState<Tool>("select");
  const [selectedPolygon, setSelectedPolygon] = useState<fabric.Polygon | null>(
    null,
  );
  const [isAddPointMode, setIsAddPointMode] = useState(false);

  const [zoneType, setZoneType] = useState<ZoneType>("RESTRICTED");
  const [bgImage, setBgImage] = useState<File | null>(null);
  const [videoName, setVideoName] = useState<string>("");
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });

  // Monitoring state
  const [isMonitoring, setIsMonitoring] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysisResult, setAnalysisResult] =
    useState<VideoProcessingResponse | null>(null);
  const [currentViolations, setCurrentViolations] = useState<ViolationReport[]>(
    [],
  );
  const [activeZoneBreaches, setActiveZoneBreaches] = useState(false);
  const [playbackTime, setPlaybackTime] = useState(0);
  const [hasSavedConfiguration, setHasSavedConfiguration] = useState(false);
  const [isDirty, setIsDirty] = useState(false);
  const [saveStatus, setSaveStatus] = useState<
    "idle" | "saving" | "saved" | "failed"
  >("idle");

  // Drawing state
  const [points, setPoints] = useState<Point2D[]>([]);
  const [pathSegments, setPathSegments] = useState<string[]>([]);
  const [tempPath, setTempPath] = useState<fabric.Path | null>(null);
  const [activePoints, setActivePoints] = useState<fabric.Circle[]>([]);

  const isLoadingZonesRef = useRef(false);
  const drawingActive = tool === "draw";

  // Broadcast drawing activity to the rest of the app so global UI can lock.
  useEffect(() => {
    try {
      window.dispatchEvent(
        new CustomEvent("zone-drawing-active", { detail: drawingActive }),
      );
    } catch (e) {
      // ignore in non-browser environments
    }
  }, [drawingActive]);

  // Manage Video URL lifecycle
  useEffect(() => {
    if (!bgImage) {
      setVideoUrl(null);
      return;
    }
    const url = URL.createObjectURL(bgImage);
    setVideoUrl(url);
    return () => {
      URL.revokeObjectURL(url);
      setVideoUrl(null);
    };
  }, [bgImage]);

  // Initialize Fabric Canvas when canvas element enters the DOM (after bgImage is set)
  useEffect(() => {
    if (!canvasRef.current || fabricCanvas) return;

    const canvas = new fabric.Canvas(canvasRef.current, {
      width: dimensions.width,
      height: dimensions.height,
      backgroundColor: "transparent",
    });

    setFabricCanvas(canvas);

    return () => {
      canvas.dispose();
      setFabricCanvas(null);
    };
  }, [bgImage]);

  // Update canvas dimensions when they change
  useEffect(() => {
    if (fabricCanvas) {
      fabricCanvas.setDimensions({
        width: dimensions.width,
        height: dimensions.height,
      });
      fabricCanvas.renderAll();
    }
  }, [dimensions, fabricCanvas]);

  // Sync video playback with monitoring state
  useEffect(() => {
    if (videoRef.current && bgImage?.type.startsWith("video/")) {
      if (isMonitoring) {
        videoRef.current.play().catch(console.error);
      } else {
        videoRef.current.pause();
        videoRef.current.currentTime = 0.1;
      }
    }
  }, [isMonitoring, bgImage]);

  const zonesLoadedRef = useRef<string | null>(null);

  useEffect(() => {
    if (!bgImage || !fabricCanvas) return;
    // @ts-ignore - check if internal context is still valid before clearing
    if (fabricCanvas.getContext()) {
      fabricCanvas.clear();
    }
    zonesLoadedRef.current = null;
    setHasSavedConfiguration(false);
    setSaveStatus("idle");
    setIsDirty(false);
  }, [bgImage, fabricCanvas]);

  useEffect(() => {
    if (!fabricCanvas) return;

    const markUnsaved = () => {
      if (isLoadingZonesRef.current) return;
      setHasSavedConfiguration(false);
      setSaveStatus("idle");
      setIsDirty(true);
    };

    fabricCanvas.on("object:added", markUnsaved);
    fabricCanvas.on("object:removed", markUnsaved);
    fabricCanvas.on("object:modified", markUnsaved);

    const clampObject = (obj: fabric.Object) => {
      if (!obj || !fabricCanvas) return;

      obj.setCoords();
      const br = obj.getBoundingRect();
      const canvasWidth = fabricCanvas.getWidth();
      const canvasHeight = fabricCanvas.getHeight();

      let offsetX = 0;
      let offsetY = 0;

      if (br.left < 0) {
        offsetX = -br.left;
      } else if (br.left + br.width > canvasWidth) {
        offsetX = canvasWidth - (br.left + br.width);
      }

      if (br.top < 0) {
        offsetY = -br.top;
      } else if (br.top + br.height > canvasHeight) {
        offsetY = canvasHeight - (br.top + br.height);
      }

      if (offsetX !== 0 || offsetY !== 0) {
        obj.left! += offsetX;
        obj.top! += offsetY;
        obj.setCoords();
      }
    };

    fabricCanvas.on("object:moving", (e) => e.target && clampObject(e.target));
    fabricCanvas.on("object:scaling", (e) => e.target && clampObject(e.target));

    return () => {
      fabricCanvas.off("object:added", markUnsaved);
      fabricCanvas.off("object:removed", markUnsaved);
      fabricCanvas.off("object:modified", markUnsaved);
      fabricCanvas.off("object:moving");
      fabricCanvas.off("object:scaling");
    };
  }, [fabricCanvas]);

  const handleMediaLoad = (width: number, height: number) => {
    const maxWidth = 800;
    const ratio = height / width;
    const displayWidth = Math.min(maxWidth, width);
    const displayHeight = displayWidth * ratio;

    setDimensions({ width: displayWidth, height: displayHeight });

    if (bgImage && fabricCanvas && zonesLoadedRef.current !== bgImage.name) {
      loadZones(bgImage.name);
      zonesLoadedRef.current = bgImage.name;
    }
  };

  const loadZones = async (vName: string) => {
    isLoadingZonesRef.current = true;
    try {
      const res = await fetch(`${API_URL}/zones/${vName}`);
      if (!res.ok) {
        setHasSavedConfiguration(false);
        setSaveStatus("idle");
        setIsDirty(false);
        return;
      }
      const data: ZoneConfiguration[] = await res.json();

      if (fabricCanvas) {
        data.forEach((zone) => {
          const shape = JSON.parse(zone.ui_shape_data);
          let fabricObj: fabric.Object | undefined;

          if (shape.type === "polygon") {
            fabricObj = new fabric.Polygon(shape.points, {
              ...shape,
              selectable: true,
            });
          } else if (shape.type === "path") {
            fabricObj = new fabric.Path(shape.path, {
              ...shape,
              selectable: true,
            });
          } else if (shape.type === "circle") {
            fabricObj = new fabric.Circle({ ...shape, selectable: true });
          }

          if (fabricObj) {
            (fabricObj as any).zoneType = zone.zone_type;
            (fabricObj as any).zoneName = zone.zone_name;
            (fabricObj as any).zoneId = zone.id;
            fabricCanvas.add(fabricObj);
          }
        });
        fabricCanvas.renderAll();

        if (data.length > 0) {
          setHasSavedConfiguration(true);
          setSaveStatus("saved");
        } else {
          setHasSavedConfiguration(false);
          setSaveStatus("idle");
        }
        setIsDirty(false);
      }
    } catch (err) {
      console.error("Failed to load zones", err);
      setHasSavedConfiguration(false);
      setSaveStatus("idle");
      setIsDirty(false);
    } finally {
      isLoadingZonesRef.current = false;
    }
  };

  const getZoneStyles = (type: ZoneType) => {
    switch (type) {
      case "RESTRICTED":
        return { fill: "rgba(239, 68, 68, 0.3)", stroke: "#ef4444" };
      case "WALKWAY":
        return { fill: "rgba(34, 197, 94, 0.3)", stroke: "#22c55e" };
      default:
        return { fill: "rgba(161, 161, 170, 0.3)", stroke: "#a1a1aa" };
    }
  };

  const clampPointer = (pointer: { x: number; y: number }) => {
    if (!fabricCanvas) return pointer;
    const bgImg = fabricCanvas.backgroundImage as fabric.Image;
    if (!bgImg) return pointer;

    const left = bgImg.left || 0;
    const top = bgImg.top || 0;
    const right = left + (bgImg.width || 0) * (bgImg.scaleX || 1);
    const bottom = top + (bgImg.height || 0) * (bgImg.scaleY || 1);

    return {
      x: Math.max(left, Math.min(pointer.x, right)),
      y: Math.max(top, Math.min(pointer.y, bottom)),
    };
  };

  useEffect(() => {
    if (!fabricCanvas || isMonitoring) return;

    const handleMouseDown = (opt: fabric.IEvent) => {
      if (tool === "draw" && fabricCanvas.getActiveObject()) {
        return;
      }

      if (tool !== "draw") return;

      const rawPointer = fabricCanvas.getPointer(opt.e);
      const pointer = clampPointer(rawPointer);
      const newPoint: Point2D = { x: pointer.x, y: pointer.y };

      if (
        points.length > 2 &&
        Math.abs(newPoint.x - points[0].x) < 20 &&
        Math.abs(newPoint.y - points[0].y) < 20
      ) {
        completePath();
        return;
      }

      if (points.length === 0) {
        setPathSegments([`M ${newPoint.x} ${newPoint.y}`]);
        setPoints([newPoint]);
      } else {
        setPathSegments((prev) => [...prev, `L ${newPoint.x} ${newPoint.y}`]);
        setPoints((prev) => [...prev, newPoint]);
      }

      const circle = new fabric.Circle({
        radius: 4,
        fill: "#f97316",
        left: pointer.x,
        top: pointer.y,
        selectable: false,
        originX: "center",
        originY: "center",
        evented: false,
      });
      fabricCanvas.add(circle);
      setActivePoints((prev) => [...prev, circle]);
    };

    const handleMouseMove = (opt: fabric.IEvent) => {
      if (tool === "draw") {
        const hoveredObject = fabricCanvas.findTarget(opt.e as MouseEvent);
        const isHoveringZone =
          hoveredObject &&
          (hoveredObject instanceof fabric.Path ||
            hoveredObject instanceof fabric.Polygon ||
            hoveredObject instanceof fabric.Circle) &&
          !!(hoveredObject as any).zoneType;

        if (canvasRef.current) {
          canvasRef.current.style.cursor = isHoveringZone
            ? "move"
            : "crosshair";
        }
      }

      if (tool !== "draw" || points.length === 0) return;

      const rawPointer = fabricCanvas.getPointer(opt.e);
      const pointer = clampPointer(rawPointer);

      const previewSegments = [...pathSegments, `L ${pointer.x} ${pointer.y}`];
      updateTempPath(previewSegments);
    };

    const handleMouseLeave = () => {
      if (canvasRef.current) {
        canvasRef.current.style.cursor = "default";
      }
    };

    const updateTempPath = (segments: string[]) => {
      if (tempPath) fabricCanvas.remove(tempPath);
      const styles = getZoneStyles(zoneType);
      const path = new fabric.Path(segments.join(" "), {
        ...styles,
        strokeWidth: 2,
        selectable: false,
        evented: false,
      });
      fabricCanvas.add(path);
      setTempPath(path);
      fabricCanvas.renderAll();
    };

    fabricCanvas.on("mouse:down", handleMouseDown);
    fabricCanvas.on("mouse:move", handleMouseMove);
    canvasRef.current?.addEventListener("mouseleave", handleMouseLeave);

    return () => {
      fabricCanvas.off("mouse:down", handleMouseDown);
      fabricCanvas.off("mouse:move", handleMouseMove);
      canvasRef.current?.removeEventListener("mouseleave", handleMouseLeave);
    };
  }, [
    fabricCanvas,
    tool,
    points,
    pathSegments,
    tempPath,
    zoneType,
    isMonitoring,
  ]);

  useEffect(() => {
    if (!fabricCanvas) return;
    const isActivelyDrawing = tool === "draw" && points.length > 0;
    const lockObjects = isActivelyDrawing || isAddPointMode;

    fabricCanvas.selection = !lockObjects;
    fabricCanvas.forEachObject((obj) => {
      obj.selectable = !lockObjects;
      obj.evented = !lockObjects;
    });

    if (isAddPointMode) {
      fabricCanvas.defaultCursor = "crosshair";
      fabricCanvas.hoverCursor = "crosshair";
    } else {
      fabricCanvas.defaultCursor = "default";
      fabricCanvas.hoverCursor = "move";
    }

    fabricCanvas.requestRenderAll();
  }, [fabricCanvas, tool, points.length, isAddPointMode]);

  // Handle polygon vertex editing toggle on selection
  useEffect(() => {
    if (!fabricCanvas) return;

    const handleSelection = (opt: fabric.IEvent) => {
      const selected = opt.selected;

      // Clean up previous editing states
      fabricCanvas.getObjects().forEach((obj) => {
        if (obj instanceof fabric.Polygon && (obj as any).isEditing) {
          disablePolygonEditing(obj);
        }
      });

      if (selected?.length === 1 && selected[0] instanceof fabric.Polygon) {
        setSelectedPolygon(selected[0] as fabric.Polygon);
        enablePolygonEditing(selected[0] as fabric.Polygon);
      } else {
        setSelectedPolygon(null);
        setIsAddPointMode(false);
      }
      fabricCanvas.requestRenderAll();
    };

    fabricCanvas.on("selection:created", handleSelection);
    fabricCanvas.on("selection:updated", handleSelection);
    fabricCanvas.on("selection:cleared", () => {
      fabricCanvas.getObjects().forEach((obj) => {
        if (obj instanceof fabric.Polygon && (obj as any).isEditing) {
          disablePolygonEditing(obj);
        }
      });
      setSelectedPolygon(null);
      setIsAddPointMode(false);
      fabricCanvas.requestRenderAll();
    });

    return () => {
      fabricCanvas.off("selection:created", handleSelection);
      fabricCanvas.off("selection:updated", handleSelection);
      fabricCanvas.off("selection:cleared");
    };
  }, [fabricCanvas]);

  // Add Point Mode interaction logic
  useEffect(() => {
    if (!fabricCanvas || !isAddPointMode || !selectedPolygon) return;

    const handleAddPointMouseDown = (opt: fabric.IEvent) => {
      const pointer = fabricCanvas.getPointer(opt.e);
      const mousePt = new fabric.Point(pointer.x, pointer.y);

      const points = selectedPolygon.points || [];
      const matrix = selectedPolygon.calcTransformMatrix();

      // Transform all points to absolute coordinates for reliable distance checking
      const absolutePoints = points.map((p) =>
        fabric.util.transformPoint(
          new fabric.Point(p.x - (selectedPolygon.pathOffset?.x || 0), p.y - (selectedPolygon.pathOffset?.y || 0)),
          matrix,
        ),
      );

      let bestIndex = -1;
      let minDistance = Infinity;

      // Find the closest edge in absolute space
      for (let i = 0; i < absolutePoints.length; i++) {
        const p1 = absolutePoints[i];
        const p2 = absolutePoints[(i + 1) % absolutePoints.length];

        const dist = distToSegment(mousePt, p1, p2);
        if (dist < minDistance) {
          minDistance = dist;
          bestIndex = i + 1;
        }
      }

      // 30px threshold in absolute space is very generous
      if (bestIndex !== -1 && minDistance < 30) {
        // Convert the absolute mouse click back to polygon's local coordinate system
        const localMatrix = selectedPolygon.calcTransformMatrix();
        const invertedMatrix = fabric.util.invertTransform(localMatrix);
        const localMousePt = fabric.util.transformPoint(mousePt, invertedMatrix);

        // Adjust for pathOffset which Fabric uses internally for points
        const finalLocalPoint = {
          x: localMousePt.x + (selectedPolygon.pathOffset?.x || 0),
          y: localMousePt.y + (selectedPolygon.pathOffset?.y || 0),
        };

        const newPoints = [...points];
        newPoints.splice(bestIndex, 0, finalLocalPoint);

        selectedPolygon.set({ points: newPoints });
        // @ts-ignore
        selectedPolygon._setPositionDimensions({});
        selectedPolygon.setCoords();

        // Refresh editing controls
        disablePolygonEditing(selectedPolygon);
        enablePolygonEditing(selectedPolygon);

        setIsDirty(true);
        setIsAddPointMode(false);
        fabricCanvas.requestRenderAll();
      }
    };

    fabricCanvas.on("mouse:down", handleAddPointMouseDown);
    fabricCanvas.defaultCursor = "crosshair";

    return () => {
      fabricCanvas.off("mouse:down", handleAddPointMouseDown);
      fabricCanvas.defaultCursor = "default";
    };
  }, [fabricCanvas, isAddPointMode, selectedPolygon]);

  // Distance from point to line segment helper
  const distToSegment = (
    p: fabric.Point,
    v: Point2D,
    w: Point2D,
  ): number => {
    const l2 = Math.pow(v.x - w.x, 2) + Math.pow(v.y - w.y, 2);
    if (l2 === 0) return Math.sqrt(Math.pow(p.x - v.x, 2) + Math.pow(p.y - v.y, 2));
    let t = ((p.x - v.x) * (w.x - v.x) + (p.y - v.y) * (w.y - v.y)) / l2;
    t = Math.max(0, Math.min(1, t));
    return Math.sqrt(
      Math.pow(p.x - (v.x + t * (w.x - v.x)), 2) +
        Math.pow(p.y - (v.y + t * (w.y - v.y)), 2),
    );
  };

  const pointInPolygon = (point: Point2D, polygon: Point2D[]): boolean => {
    let inside = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
      const xi = polygon[i].x,
        yi = polygon[i].y;
      const xj = polygon[j].x,
        yj = polygon[j].y;

      const intersect =
        yi > point.y !== yj > point.y &&
        point.x < ((xj - xi) * (point.y - yi)) / (yj - yi) + xi;
      if (intersect) inside = !inside;
    }
    return inside;
  };

  const checkPolygonOverlap = (): boolean => {
    if (!fabricCanvas || points.length < 3) return false;

    const existingObjects = fabricCanvas.getObjects().filter((obj) => {
      const isZone =
        (obj instanceof fabric.Path ||
          obj instanceof fabric.Polygon ||
          obj instanceof fabric.Circle) &&
        !!(obj as any).zoneType &&
        obj !== tempPath;
      return isZone;
    });

    const newPolygonPoints = points;

    for (const obj of existingObjects) {
      if (obj instanceof fabric.Path) {
        const pathObj = obj as any;
        try {
          const svgNS = "http://www.w3.org/2000/svg";
          const svg = document.createElementNS(svgNS, "svg");
          const svgPath = document.createElementNS(svgNS, "path");
          const d = pathObj.path
            .map((segment: any) => segment.join(" "))
            .join(" ");
          svgPath.setAttribute("d", d);
          svg.appendChild(svgPath);
          svg.style.position = "absolute";
          svg.style.visibility = "hidden";
          document.body.appendChild(svg);

          const pathOffset = pathObj.pathOffset || { x: 0, y: 0 };
          const matrix = pathObj.calcTransformMatrix();
          const totalLength = svgPath.getTotalLength();
          const pathPoints: Point2D[] = [];
          for (let i = 0; i <= 50; i++) {
            const p = svgPath.getPointAtLength(totalLength * (i / 50));
            const localPoint = new fabric.Point(
              p.x - pathOffset.x,
              p.y - pathOffset.y,
            );
            const transformed = fabric.util.transformPoint(localPoint, matrix);
            pathPoints.push({ x: transformed.x, y: transformed.y });
          }

          for (const newPoint of newPolygonPoints) {
            if (pointInPolygon(newPoint, pathPoints)) {
              if (svg.parentNode) svg.parentNode.removeChild(svg);
              return true;
            }
          }
          for (const pathPoint of pathPoints) {
            if (pointInPolygon(pathPoint, newPolygonPoints)) {
              if (svg.parentNode) svg.parentNode.removeChild(svg);
              return true;
            }
          }
          if (svg.parentNode) svg.parentNode.removeChild(svg);
        } catch (e) {
          console.error("Error checking path overlap", e);
        }
      } else if (obj instanceof fabric.Polygon) {
        const polygon = obj as fabric.Polygon;
        const existingPoints = polygon.points || [];
        const matrix = polygon.calcTransformMatrix();
        const transformedExistingPoints = existingPoints.map((p) => {
          const tp = fabric.util.transformPoint(
            new fabric.Point(p.x, p.y),
            matrix,
          );
          return { x: tp.x, y: tp.y };
        });

        for (const newPoint of newPolygonPoints) {
          if (pointInPolygon(newPoint, transformedExistingPoints)) return true;
        }
        for (const existingPoint of transformedExistingPoints) {
          if (pointInPolygon(existingPoint, newPolygonPoints)) return true;
        }
      } else if (obj instanceof fabric.Circle) {
        const circle = obj as fabric.Circle;
        const centerX = circle.left || 0;
        const centerY = circle.top || 0;
        const radius = circle.radius || 0;

        for (const newPoint of newPolygonPoints) {
          const dist = Math.sqrt(
            Math.pow(newPoint.x - centerX, 2) +
              Math.pow(newPoint.y - centerY, 2),
          );
          if (dist < radius) return true;
        }
      }
    }

    return false;
  };

  const completePath = () => {
    if (!fabricCanvas || points.length < 3) return;

    if (checkPolygonOverlap()) {
      alert("Cannot draw polygon: it overlaps with an existing zone!");
      activePoints.forEach((p) => fabricCanvas.remove(p));
      if (tempPath) fabricCanvas.remove(tempPath);

      setPoints([]);
      setPathSegments([]);
      setActivePoints([]);
      setTempPath(null);
      fabricCanvas.renderAll();
      return;
    }

    const styles = getZoneStyles(zoneType);
    const finalPolygon = new fabric.Polygon(points, {
      ...styles,
      strokeWidth: 2,
      selectable: true,
      objectCaching: false,
    });
    (finalPolygon as any).zoneType = zoneType;

    fabricCanvas.add(finalPolygon);

    activePoints.forEach((p) => fabricCanvas.remove(p));
    if (tempPath) fabricCanvas.remove(tempPath);

    setPoints([]);
    setPathSegments([]);
    setActivePoints([]);
    setTempPath(null);
    setTool("select");
    fabricCanvas.renderAll();
  };

  const saveConfiguration = async (silent = false) => {
    if (!fabricCanvas || !videoName) return;
    if (!silent) {
      setSaveStatus("saving");
    }

    const objects = fabricCanvas.getObjects();
    const canvasWidth = fabricCanvas.getWidth();
    const canvasHeight = fabricCanvas.getHeight();

    try {
      await deleteZonesForVideo(videoName);
    } catch (err) {
      if (!silent) {
        setSaveStatus("failed");
        alert(`Failed to clear existing zones before saving: ${err}`);
      }
      return;
    }

    const zoneObjects = objects.filter(
      (obj) =>
        (obj instanceof fabric.Path ||
          obj instanceof fabric.Polygon ||
          obj instanceof fabric.Circle) &&
        !!(obj as any).zoneType,
    );

    const zonesToSave: ZoneConfiguration[] = zoneObjects.map((obj) => {
      let flattened: Point2D[] = [];
      const matrix = obj.calcTransformMatrix();

      if (obj instanceof fabric.Path) {
        const pathObj = obj as any;
        const svgNS = "http://www.w3.org/2000/svg";
        const svg = document.createElementNS(svgNS, "svg");
        const svgPath = document.createElementNS(svgNS, "path");
        const d = pathObj.path
          .map((segment: any) => segment.join(" "))
          .join(" ");
        svgPath.setAttribute("d", d);
        svg.appendChild(svgPath);
        svg.style.position = "absolute";
        svg.style.visibility = "hidden";
        document.body.appendChild(svg);

        try {
          const pathOffset = pathObj.pathOffset || { x: 0, y: 0 };
          const totalLength = svgPath.getTotalLength();
          for (let i = 0; i <= 100; i++) {
            const p = svgPath.getPointAtLength(totalLength * (i / 100));
            const localPoint = new fabric.Point(
              p.x - pathOffset.x,
              p.y - pathOffset.y,
            );
            const transformed = fabric.util.transformPoint(localPoint, matrix);
            flattened.push({
              x: transformed.x / canvasWidth,
              y: transformed.y / canvasHeight,
            });
          }
        } catch (e) {
          console.error("Failed to flatten path", e);
        } finally {
          if (svg.parentNode) {
            svg.parentNode.removeChild(svg);
          }
        }
      } else if (obj instanceof fabric.Polygon) {
        const polygon = obj as fabric.Polygon;
        const offset = polygon.pathOffset || { x: 0, y: 0 };
        flattened =
          polygon.points?.map((p) => {
            const transformedPoint = fabric.util.transformPoint(
              new fabric.Point(p.x - offset.x, p.y - offset.y),
              matrix,
            );
            return {
              x: transformedPoint.x / canvasWidth,
              y: transformedPoint.y / canvasHeight,
            };
          }) || [];
      } else if (obj instanceof fabric.Circle) {
        const radius = (obj as fabric.Circle).radius || 0;
        for (let i = 0; i < 64; i++) {
          const angle = (i / 64) * 2 * Math.PI;
          const localPoint = new fabric.Point(
            radius * Math.cos(angle),
            radius * Math.sin(angle),
          );
          const transformedPoint = fabric.util.transformPoint(
            localPoint,
            matrix,
          );
          flattened.push({
            x: transformedPoint.x / canvasWidth,
            y: transformedPoint.y / canvasHeight,
          });
        }
      }

      return {
        video_name: videoName,
        zone_name:
          (obj as any).zoneName || `Zone ${Math.floor(Math.random() * 1000)}`,
        zone_type: (obj as any).zoneType || zoneType,
        dwell_threshold_seconds: 0,
        is_active: true,
        ui_shape_data: JSON.stringify(obj.toObject(["zoneType", "zoneName"])),
        flattened_coordinates: JSON.stringify(flattened),
      };
    });

    try {
      for (let i = 0; i < zonesToSave.length; i++) {
        const res = await fetch(`${API_URL}/zones`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(zonesToSave[i]),
        });
        if (res.ok) {
          const saved = await res.json();
          (zoneObjects[i] as any).zoneId = saved.id;
        }
      }
      setHasSavedConfiguration(zonesToSave.length > 0);
      setSaveStatus("saved");
      setIsDirty(false);
      if (!silent) {
        alert(`Saved ${zonesToSave.length} zones!`);
      }
    } catch (err) {
      if (!silent) {
        setSaveStatus("failed");
        alert("Failed to save configuration");
      }
    }
  };

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Delete" && !isMonitoring && fabricCanvas) {
        deleteSelected();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [fabricCanvas, isMonitoring]);

  const deleteSelected = () => {
    if (!fabricCanvas) return;
    const activeObjects = fabricCanvas.getActiveObjects();
    fabricCanvas.discardActiveObject();
    activeObjects.forEach((obj) => fabricCanvas.remove(obj));
  };

  const discardChanges = async () => {
    if (!fabricCanvas || !bgImage) return;

    if (
      window.confirm(
        "Are you sure you want to discard all changes? This will revert to the latest saved version.",
      )
    ) {
      fabricCanvas.clear();
      await loadZones(bgImage.name);
    }
  };

  const startMonitoring = async () => {
    if (!bgImage || !fabricCanvas || !videoName) return;

    setIsAnalyzing(true);
    setIsMonitoring(false);
    setCurrentViolations([]);
    setActiveZoneBreaches(false);

    try {
      await saveConfiguration(true);
      const result = await analyzeVideo(bgImage);
      setAnalysisResult(result);
      setIsMonitoring(true);

      fabricCanvas.getObjects().forEach((obj) => {
        obj.set({ selectable: false, evented: false });
      });
      fabricCanvas.discardActiveObject();
      fabricCanvas.renderAll();
    } catch (err) {
      alert(
        "Failed to analyze video: " +
          (err instanceof Error ? err.message : "Unknown error"),
      );
    } finally {
      setIsAnalyzing(false);
    }
  };

  useEffect(() => {
    if (videoRef.current) {
      videoRef.current.style.opacity = isMonitoring ? "1" : "0.8";
    }
  }, [isMonitoring]);

  useEffect(() => {
    if (!isMonitoring || !analysisResult || !fabricCanvas) return;

    const fps = analysisResult.summary.fps || 30;
    const currentFrame = Math.floor(playbackTime * fps);
    const windowSize = fps / 1.5;

    const activeThisFrame = analysisResult.reports.filter(
      (r) =>
        r.frame_index != null &&
        Math.abs(r.frame_index - currentFrame) < windowSize,
    );

    const activeZoneViolations = (analysisResult.zone_violations ?? []).filter(
      (zv) => Math.abs(zv.frame_index - currentFrame) < windowSize,
    );

    setCurrentViolations(activeThisFrame);
    setActiveZoneBreaches(
      activeZoneViolations.some((zv) => zv.zone_type === "RESTRICTED"),
    );

    fabricCanvas.getObjects().forEach((obj) => {
      const objZoneType: ZoneType = (obj as any).zoneType || "RESTRICTED";
      const originalStyles = getZoneStyles(objZoneType);
      obj.set({ ...originalStyles, strokeWidth: 2 });
    });
    fabricCanvas.renderAll();
  }, [playbackTime, isMonitoring, analysisResult, fabricCanvas]);

  // Sync bbox canvas size with display dimensions
  useEffect(() => {
    const canvas = bboxCanvasRef.current;
    if (!canvas) return;
    canvas.width = dimensions.width;
    canvas.height = dimensions.height;
  }, [dimensions]);

  const drawBboxOverlay = useCallback(() => {
    const canvas = bboxCanvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (!isMonitoring || !analysisResult) return;

    const fps = analysisResult.summary.fps || 30;
    const stride = Math.max(1, Math.round(
      (analysisResult.summary.total_frames || 1) /
      Math.max(1, analysisResult.summary.processed_frames || 1)
    ));
    const currentFrame = Math.floor(playbackTime * fps);

    const frames = analysisResult.tracking_frames ?? [];
    // For each track_id keep only the single frame closest to currentFrame.
    // Without deduplication every frame within the window stacks on the same spot.
    const byTrackId = new Map<number, (typeof frames)[number]>();
    for (const f of frames) {
      const dist = Math.abs(f.frame_index - currentFrame);
      if (dist > stride * 2) continue; // outside visible window
      const prev = byTrackId.get(f.track_id);
      if (!prev || Math.abs(prev.frame_index - currentFrame) > dist) {
        byTrackId.set(f.track_id, f);
      }
    }
    const visible = Array.from(byTrackId.values());

    const drawRoundRect = (x: number, y: number, w: number, h: number, r: number) => {
      if (typeof (ctx as any).roundRect === "function") {
        (ctx as any).roundRect(x, y, w, h, r);
      } else {
        ctx.rect(x, y, w, h);
      }
    };

    for (const tf of visible) {
      const { x1, y1, x2, y2 } = tf.bbox;
      const cx1 = x1 * canvas.width;
      const cy1 = y1 * canvas.height;
      const cx2 = x2 * canvas.width;
      const cy2 = y2 * canvas.height;
      const bw = cx2 - cx1;
      const bh = cy2 - cy1;

      const isRestricted = tf.zone_type === "RESTRICTED";
      const isWalkway = tf.zone_type === "WALKWAY";
      const inViolation = isRestricted || isWalkway;
      const color = isRestricted ? "#ef4444" : isWalkway ? "#3b82f6" : "#22c55e";

      // Bounding box fill
      ctx.fillStyle = isRestricted
        ? "rgba(239,68,68,0.12)"
        : isWalkway
        ? "rgba(59,130,246,0.12)"
        : "rgba(34,197,94,0.08)";
      ctx.fillRect(cx1, cy1, bw, bh);

      // Bounding box stroke
      ctx.strokeStyle = color;
      ctx.lineWidth = inViolation ? 2.5 : 1.5;
      ctx.strokeRect(cx1, cy1, bw, bh);

      // L-shaped corner accents
      const cs = Math.min(14, bw * 0.22, bh * 0.22);
      ctx.lineWidth = inViolation ? 3 : 2;
      ctx.strokeStyle = color;
      (
        [
          [cx1, cy1, 1, 1],
          [cx2, cy1, -1, 1],
          [cx1, cy2, 1, -1],
          [cx2, cy2, -1, -1],
        ] as [number, number, number, number][]
      ).forEach(([px, py, dx, dy]) => {
        ctx.beginPath();
        ctx.moveTo(px + dx * cs, py);
        ctx.lineTo(px, py);
        ctx.lineTo(px, py + dy * cs);
        ctx.stroke();
      });

      // Info panel lines
      const fontPx = Math.max(9, Math.min(11, canvas.width / 80));
      const panelPad = 6;
      const lineH = fontPx + 4;

      const lines: { text: string; color: string }[] = inViolation
        ? [
            { text: isRestricted ? "RESTRICTED ZONE" : "WALKWAY VIOLATION", color },
            { text: `Zone: ${tf.zone_name ?? `#${tf.zone_id}`}`, color: "#e4e4e7" },
            { text: `Track ID: ${tf.track_id}`, color: "#a1a1aa" },
            { text: `Frame: ${tf.frame_index}`, color: "#71717a" },
          ]
        : [{ text: `Track ID: ${tf.track_id}`, color: "#a1a1aa" }];

      ctx.font = `${fontPx}px 'IBM Plex Mono', monospace`;
      const longestLine = lines.reduce(
        (max, l) => (ctx.measureText(l.text).width > ctx.measureText(max).width ? l.text : max),
        "",
      );
      const panelW = Math.max(100, ctx.measureText(longestLine).width + panelPad * 2 + 4);
      const panelH = lines.length * lineH + panelPad * 2;

      let panelX = cx1;
      let panelY = cy2 + 4;
      if (panelY + panelH > canvas.height) panelY = cy1 - panelH - 4;
      if (panelX + panelW > canvas.width) panelX = canvas.width - panelW - 2;
      if (panelX < 0) panelX = 2;

      // Panel background
      ctx.fillStyle = "rgba(9,9,11,0.88)";
      ctx.beginPath();
      drawRoundRect(panelX, panelY, panelW, panelH, 4);
      ctx.fill();

      // Panel border
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      drawRoundRect(panelX, panelY, panelW, panelH, 4);
      ctx.stroke();

      // Panel text
      ctx.font = `${fontPx}px 'IBM Plex Mono', monospace`;
      lines.forEach((line, i) => {
        ctx.fillStyle = line.color;
        ctx.fillText(line.text, panelX + panelPad, panelY + panelPad + (i + 1) * lineH - 2);
      });
    }
  }, [isMonitoring, analysisResult, playbackTime, dimensions]);

  useEffect(() => {
    drawBboxOverlay();
  }, [drawBboxOverlay]);

  // Clear bbox canvas when monitoring stops
  useEffect(() => {
    if (!isMonitoring) {
      const canvas = bboxCanvasRef.current;
      if (canvas) {
        const ctx = canvas.getContext("2d");
        ctx?.clearRect(0, 0, canvas.width, canvas.height);
      }
    }
  }, [isMonitoring]);

  return (
    <div className="flex flex-col gap-4 items-center w-full max-w-6xl mx-auto">
      {bgImage && (
        <div className="flex flex-wrap gap-4 items-center justify-between w-full bg-zinc-900 p-3 rounded-lg border border-zinc-800">
          <div className="flex gap-2">
            <ToolButton
              active={tool === "select"}
              onClick={() => setTool("select")}
              disabled={isMonitoring || drawingActive}
            >
              Select
            </ToolButton>
            <ToolButton
              active={tool === "draw"}
              onClick={() => setTool("draw")}
              disabled={isMonitoring}
            >
              Draw
            </ToolButton>

            {!isMonitoring && (
              <div className="flex items-center ml-1">
                <select
                  value={zoneType}
                  onChange={(e) => setZoneType(e.target.value as ZoneType)}
                  disabled={drawingActive}
                  className="bg-zinc-950 border border-zinc-800 text-[10px] font-mono px-2 py-1 rounded text-zinc-300 focus:outline-none focus:border-orange-500/50 h-[26px]"
                >
                  <option value="RESTRICTED">RESTRICTED</option>
                  <option value="WALKWAY">WALKWAY</option>
                </select>
              </div>
            )}

            <div className="w-px h-6 bg-zinc-800 mx-1" />
            <ToolButton
              active={false}
              onClick={deleteSelected}
              disabled={isMonitoring || drawingActive}
              className="border-red-900/50 text-red-500 hover:bg-red-500/10"
            >
              Delete
            </ToolButton>

            {!isMonitoring && selectedPolygon && (
              <ToolButton
                active={isAddPointMode}
                onClick={() => setIsAddPointMode(!isAddPointMode)}
                className="border-orange-500/50 text-orange-400 hover:bg-orange-500/10 ml-2"
              >
                {isAddPointMode ? "Click on Edge" : "Add Point"}
              </ToolButton>
            )}
          </div>

          <div className="flex gap-4 items-center">
            <div className="flex gap-2">
              {isDirty && !isMonitoring && (
                <ToolButton
                  active={false}
                  onClick={discardChanges}
                  className="border-none bg-zinc-800 hover:bg-red-900/40 text-zinc-400 hover:text-red-400 px-4"
                >
                  Discard Changes
                </ToolButton>
              )}

              <ToolButton
                active={false}
                onClick={() => saveConfiguration(false)}
                disabled={isMonitoring || !bgImage || drawingActive}
                className={`border-none px-4 ${saveStatus === "saved" ? "bg-emerald-700 hover:bg-emerald-600" : "bg-zinc-800 hover:bg-zinc-700"}`}
              >
                {saveStatus === "saving"
                  ? "Saving..."
                  : saveStatus === "saved"
                    ? "Saved ✓"
                    : "Save Changes"}
              </ToolButton>

              <button
                onClick={
                  isMonitoring ? () => setIsMonitoring(false) : startMonitoring
                }
                disabled={
                  isAnalyzing ||
                  !bgImage ||
                  !bgImage.type.startsWith("video/") ||
                  !hasSavedConfiguration ||
                  drawingActive
                }
                className={`
                px-6 py-1 rounded text-xs font-mono font-bold transition-all
                ${
                  isMonitoring
                    ? "bg-red-600 hover:bg-red-500 text-white"
                    : "bg-orange-600 hover:bg-orange-500 text-white disabled:opacity-30 disabled:grayscale"
                }
              `}
              >
                {isAnalyzing
                  ? "ANALYZING..."
                  : isMonitoring
                    ? "STOP MONITOR"
                    : "START MONITORING"}
              </button>
            </div>
          </div>
        </div>
      )}

      {!bgImage ? (
        <div className="w-full max-w-xl py-20">
          <UploadZone
            onFileSelect={(file) => {
              setBgImage(file);
              setVideoName(file.name);
            }}
          />
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_260px] gap-4 w-full">
          <div className="flex flex-col gap-2">
            <div className="flex justify-between items-center px-1 h-6">
              <span className="text-[10px] font-mono text-zinc-500 uppercase truncate max-w-[200px]">
                {isMonitoring ? "LIVE MONITORING" : "CONFIGURATION"}:{" "}
                {videoName}
              </span>
              {isMonitoring && (
                <div className="flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
                  <span className="text-[10px] font-mono text-zinc-400">
                    {formatTime(playbackTime)}
                  </span>
                </div>
              )}
              {!isMonitoring && (
                <>
                  <input
                    ref={changeMediaInputRef}
                    type="file"
                    accept=".jpg,.jpeg,.png,.webp,.bmp,.mp4,.mpeg,.mpg,.mov,.avi,.mkv,.webm"
                    className="hidden"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (!file) return;
                      setBgImage(file);
                      setVideoName(file.name);
                      e.target.value = "";
                    }}
                  />
                  <button
                    onClick={() => changeMediaInputRef.current?.click()}
                    className="text-[10px] font-mono text-zinc-500 hover:text-zinc-300 underline uppercase"
                  >
                    Change Media
                  </button>
                </>
              )}
            </div>

            <div
              className="relative bg-black rounded-lg overflow-hidden shadow-2xl group border border-zinc-800 mx-auto"
              style={{ width: dimensions.width, height: dimensions.height }}
            >
              {videoUrl &&
                (bgImage?.type.startsWith("video/") ? (
                  <video
                    ref={videoRef}
                    src={videoUrl}
                    muted
                    playsInline
                    loop={isMonitoring}
                    autoPlay={isMonitoring}
                    onLoadedMetadata={(e) => {
                      const v = e.currentTarget;
                      handleMediaLoad(v.videoWidth, v.videoHeight);
                    }}
                    onTimeUpdate={(e) => {
                      if (isMonitoring) {
                        setPlaybackTime(e.currentTarget.currentTime);
                      }
                    }}
                    className="absolute inset-0 w-full h-full z-0"
                  />
                ) : (
                  <img
                    src={videoUrl}
                    onLoad={(e) =>
                      handleMediaLoad(
                        e.currentTarget.naturalWidth,
                        e.currentTarget.naturalHeight,
                      )
                    }
                    className="absolute inset-0 w-full h-full z-0"
                  />
                ))}
              <div key="fabric-host" className="absolute inset-0 z-10">
                <canvas ref={canvasRef} />
              </div>
              <canvas
                ref={bboxCanvasRef}
                className="absolute inset-0 z-20 pointer-events-none"
                style={{ width: dimensions.width, height: dimensions.height }}
              />
              {isMonitoring && activeZoneBreaches && (
                <div className="absolute top-4 left-4 z-30 animate-bounce">
                  <div className="bg-red-600 text-white text-[10px] font-bold px-3 py-1 rounded shadow-lg border border-red-400 uppercase tracking-widest">
                    ⚠️ Restricted Area Breach
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="bg-zinc-900/50 border border-zinc-800 rounded-lg p-3 flex flex-col gap-3">
            <p className="font-mono text-[10px] text-zinc-600 tracking-widest uppercase">
              Monitoring Log
            </p>
            {!isMonitoring ? (
              <div className="text-[10px] font-mono text-zinc-500 space-y-4">
                <p>1. Draw restricted zones using the DRAW tool.</p>
                <p>2. Click SAVE ALL.</p>
                <p>3. Click START MONITORING to run analysis.</p>
              </div>
            ) : (
              <div className="flex-1 overflow-y-auto space-y-2">
                {currentViolations.length === 0 ? (
                  <p className="text-[10px] font-mono text-zinc-600 italic">
                    Scanning for incursions...
                  </p>
                ) : (
                  currentViolations.map((v, i) => (
                    <div
                      key={i}
                      className="bg-red-500/10 border border-red-500/30 rounded p-2 animate-pulse"
                    >
                      <p className="text-[10px] font-mono text-red-400 font-bold">
                        BREACH DETECTED
                      </p>
                      <p className="text-[9px] font-mono text-red-300/70 mt-1">
                        Track ID: {v.track_id}
                        <br />
                        Frame: {v.frame_index}
                      </p>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function ToolButton({
  active,
  onClick,
  children,
  className = "",
  disabled = false,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`px-3 py-1 rounded text-xs font-mono border transition-colors ${
        disabled ? "opacity-30 cursor-not-allowed" : ""
      } ${
        active
          ? "bg-orange-500/20 border-orange-500 text-orange-400"
          : "bg-zinc-950 border-zinc-800 text-zinc-400 hover:text-zinc-200"
      } ${className}`}
    >
      {children.toString().toUpperCase()}
    </button>
  );
}
