"use client";

import { useEffect, useRef, useState } from "react";
import { fabric } from "fabric";
import { UploadZone } from "./UploadZone";
import { Point2D, ZoneConfiguration, ZoneType } from "@/types/zone";
import { analyzeVideo, deleteZonesForVideo, API_URL } from "@/lib/api";
import { VideoProcessingResponse, ViolationReport } from "@/types/detection";

type Tool = "select" | "draw" | "delete" | "calibrate";
type SegmentType = "line" | "curve";

export function ZoneDrawingCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [fabricCanvas, setFabricCanvas] = useState<fabric.Canvas | null>(null);
  const [tool, setTool] = useState<Tool>("select");
  const [segmentType, setSegmentType] = useState<SegmentType>("line");
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
  const [saveStatus, setSaveStatus] = useState<
    "idle" | "saving" | "saved" | "failed"
  >("idle");

  // Calibration state
  const [calibrationPoints, setCalibrationPoints] = useState<Point2D[]>([]);
  const [calibrationPolygon, setCalibrationPolygon] =
    useState<fabric.Polygon | null>(null);

  // Drawing state
  const [isAdjustingCurve, setIsAdjustingCurve] = useState(false);
  const [points, setPoints] = useState<Point2D[]>([]);
  const [pathSegments, setPathSegments] = useState<string[]>([]);
  const [tempPath, setTempPath] = useState<fabric.Path | null>(null);
  const [activePoints, setActivePoints] = useState<fabric.Circle[]>([]);

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
    // bgImage controls whether the canvas element is in the DOM; re-run when it changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    fabricCanvas.clear();
    zonesLoadedRef.current = null;
    setHasSavedConfiguration(false);
    setSaveStatus("idle");
  }, [bgImage, fabricCanvas]);

  useEffect(() => {
    if (!fabricCanvas) return;

    const markUnsaved = () => {
      setHasSavedConfiguration(false);
      setSaveStatus("idle");
    };

    fabricCanvas.on("object:added", markUnsaved);
    fabricCanvas.on("object:removed", markUnsaved);
    fabricCanvas.on("object:modified", markUnsaved);

    return () => {
      fabricCanvas.off("object:added", markUnsaved);
      fabricCanvas.off("object:removed", markUnsaved);
      fabricCanvas.off("object:modified", markUnsaved);
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
    try {
      const res = await fetch(`${API_URL}/zones/${vName}`);
      if (!res.ok) return;
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

        // If zones were loaded, mark as saved so monitoring can start
        if (data.length > 0) {
          setHasSavedConfiguration(true);
          setSaveStatus("saved");
        }
      }
      loadCalibration(vName);
    } catch (err) {
      console.error("Failed to load zones", err);
    }
  };

  const loadCalibration = async (vName: string) => {
    try {
      const res = await fetch(`${API_URL}/calibration/${vName}`);
      if (!res.ok) return;
      const data = await res.json();
      const points: Point2D[] = JSON.parse(data.source_points);

      if (fabricCanvas && points.length === 4) {
        const canvasWidth = fabricCanvas.getWidth();
        const canvasHeight = fabricCanvas.getHeight();
        const absolutePoints = points.map((p) => ({
          x: p.x * canvasWidth,
          y: p.y * canvasHeight,
        }));

        setCalibrationPoints(absolutePoints);

        const poly = new fabric.Polygon(absolutePoints, {
          fill: "rgba(59, 130, 246, 0.2)",
          stroke: "#3b82f6",
          strokeWidth: 2,
          strokeDashArray: [5, 5],
          selectable: false,
          evented: false,
        });
        fabricCanvas.add(poly);
        setCalibrationPolygon(poly);

        absolutePoints.forEach((p) => {
          const circle = new fabric.Circle({
            radius: 5,
            fill: "#3b82f6",
            left: p.x,
            top: p.y,
            selectable: false,
            originX: "center",
            originY: "center",
            evented: false,
          });
          fabricCanvas.add(circle);
          setActivePoints((prev) => [...prev, circle]);
        });
        fabricCanvas.renderAll();
      }
    } catch (err) {
      console.error("Failed to load calibration", err);
    }
  };

  const getZoneStyles = (type: ZoneType) => {
    switch (type) {
      case "RESTRICTED":
        return { fill: "rgba(239, 68, 68, 0.3)", stroke: "#ef4444" };
      case "WALKWAY":
        return { fill: "rgba(34, 197, 94, 0.3)", stroke: "#22c55e" };
      case "FORKLIFT_PATH":
        return { fill: "rgba(234, 179, 8, 0.3)", stroke: "#eab308" };
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
      // Check if clicking on an existing zone when in draw mode
      if (tool === "draw" && fabricCanvas.getActiveObject()) {
        return; // Let fabric.js handle selection/dragging
      }

      if (tool === "calibrate") {
        const rawPointer = fabricCanvas.getPointer(opt.e);
        const pointer = clampPointer(rawPointer);
        const newPoint: Point2D = { x: pointer.x, y: pointer.y };

        if (calibrationPoints.length < 4) {
          const newPoints = [...calibrationPoints, newPoint];
          setCalibrationPoints(newPoints);

          const circle = new fabric.Circle({
            radius: 5,
            fill: "#3b82f6",
            left: pointer.x,
            top: pointer.y,
            selectable: false,
            originX: "center",
            originY: "center",
            evented: false,
          });
          fabricCanvas.add(circle);
          setActivePoints((prev) => [...prev, circle]);

          if (newPoints.length === 4) {
            const poly = new fabric.Polygon(newPoints, {
              fill: "rgba(59, 130, 246, 0.2)",
              stroke: "#3b82f6",
              strokeWidth: 2,
              strokeDashArray: [5, 5],
              selectable: false,
              evented: false,
            });
            fabricCanvas.add(poly);
            setCalibrationPolygon(poly);
          }
        } else {
          // Reset calibration
          activePoints.forEach((p) => fabricCanvas.remove(p));
          if (calibrationPolygon) fabricCanvas.remove(calibrationPolygon);
          setCalibrationPoints([newPoint]);
          setCalibrationPolygon(null);

          const circle = new fabric.Circle({
            radius: 5,
            fill: "#3b82f6",
            left: pointer.x,
            top: pointer.y,
            selectable: false,
            originX: "center",
            originY: "center",
            evented: false,
          });
          fabricCanvas.add(circle);
          setActivePoints([circle]);
        }
        return;
      }

      if (tool !== "draw" || isAdjustingCurve) return;

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
        if (segmentType === "line") {
          setPathSegments((prev) => [...prev, `L ${newPoint.x} ${newPoint.y}`]);
          setPoints((prev) => [...prev, newPoint]);
        } else {
          setIsAdjustingCurve(true);
          setPoints((prev) => [...prev, newPoint]);
          setPathSegments((prev) => [
            ...prev,
            `Q ${newPoint.x} ${newPoint.y} ${newPoint.x} ${newPoint.y}`,
          ]);
        }
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
          canvasRef.current.style.cursor = isHoveringZone ? "move" : "crosshair";
        }
      }

      if (tool !== "draw" || points.length === 0) return;

      const rawPointer = fabricCanvas.getPointer(opt.e);
      const pointer = clampPointer(rawPointer);

      if (isAdjustingCurve) {
        const endPoint = points[points.length - 1];
        const newSegments = [...pathSegments];
        newSegments[newSegments.length - 1] =
          `Q ${pointer.x} ${pointer.y} ${endPoint.x} ${endPoint.y}`;
        updateTempPath(newSegments);
      } else {
        const previewSegments = [
          ...pathSegments,
          `L ${pointer.x} ${pointer.y}`,
        ];
        updateTempPath(previewSegments);
      }
    };

    const handleMouseUp = () => {
      if (isAdjustingCurve) {
        setIsAdjustingCurve(false);
      }
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
    fabricCanvas.on("mouse:up", handleMouseUp);
    canvasRef.current?.addEventListener("mouseleave", handleMouseLeave);

    return () => {
      fabricCanvas.off("mouse:down", handleMouseDown);
      fabricCanvas.off("mouse:move", handleMouseMove);
      fabricCanvas.off("mouse:up", handleMouseUp);
      canvasRef.current?.removeEventListener("mouseleave", handleMouseLeave);
    };
  }, [
    fabricCanvas,
    tool,
    points,
    pathSegments,
    tempPath,
    segmentType,
    isAdjustingCurve,
    zoneType,
    isMonitoring,
  ]);

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
          const totalLength = svgPath.getTotalLength();
          const pathPoints: Point2D[] = [];
          for (let i = 0; i <= 50; i++) {
            const p = svgPath.getPointAtLength(totalLength * (i / 50));
            pathPoints.push({
              x: p.x - pathOffset.x + (pathObj.left || 0),
              y: p.y - pathOffset.y + (pathObj.top || 0),
            });
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

        for (const newPoint of newPolygonPoints) {
          if (pointInPolygon(newPoint, existingPoints)) return true;
        }
        for (const existingPoint of existingPoints) {
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

    const finalPathString = [...pathSegments, "z"].join(" ");
    const styles = getZoneStyles(zoneType);
    const finalPath = new fabric.Path(finalPathString, {
      ...styles,
      strokeWidth: 2,
      selectable: true,
    });
    (finalPath as any).zoneType = zoneType;

    fabricCanvas.add(finalPath);

    activePoints.forEach((p) => fabricCanvas.remove(p));
    if (tempPath) fabricCanvas.remove(tempPath);

    setPoints([]);
    setPathSegments([]);
    setActivePoints([]);
    setTempPath(null);
    setTool("select");
    fabricCanvas.renderAll();
  };

  const clearDbZones = async () => {
    if (!videoName) return;
    if (
      !confirm(
        `Delete all saved zones for "${videoName}" from the database? Canvas objects are kept.`,
      )
    )
      return;
    try {
      const result = await deleteZonesForVideo(videoName);
      // Clear the zoneId stamp on canvas objects so they can be re-saved with correct coords
      fabricCanvas?.getObjects().forEach((obj) => {
        delete (obj as any).zoneId;
      });
      alert(
        `Deleted ${result.deleted} zone(s) from DB. You can now re-save with corrected coordinates.`,
      );
    } catch (err) {
      alert(`Failed to clear zones: ${err}`);
    }
  };

  const saveConfiguration = async (silent = false) => {
    if (!fabricCanvas || !videoName) return;
    if (!silent) {
      setSaveStatus("saving");
    }

    const objects = fabricCanvas.getObjects();
    const canvasWidth = fabricCanvas.getWidth();
    const canvasHeight = fabricCanvas.getHeight();

    // 1. Save Calibration if 4 points exist
    if (calibrationPoints.length === 4) {
      const normalizedCalibration = calibrationPoints.map((p) => ({
        x: p.x / canvasWidth,
        y: p.y / canvasHeight,
      }));

      try {
        await fetch(`${API_URL}/calibration`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            video_name: videoName,
            source_points: JSON.stringify(normalizedCalibration),
          }),
        });
      } catch (err) {
        console.error("Failed to save calibration", err);
      }
    }

    // 2. Save only NEW zones (no zoneId = not yet in DB); exclude calibration objects (no zoneType)
    const zonesToSave: ZoneConfiguration[] = objects
      .filter(
        (obj) =>
          (obj instanceof fabric.Path ||
            obj instanceof fabric.Polygon ||
            obj instanceof fabric.Circle) &&
          !!(obj as any).zoneType &&
          !(obj as any).zoneId,
      )
      .map((obj) => {
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
              // SVG path data is in path-local space centered on pathOffset.
              // Subtract pathOffset so the point is relative to the object's origin,
              // then apply the transform matrix to get canvas coordinates.
              const localPoint = new fabric.Point(
                p.x - pathOffset.x,
                p.y - pathOffset.y,
              );
              const transformed = fabric.util.transformPoint(
                localPoint,
                matrix,
              );
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
          flattened =
            (obj as fabric.Polygon).points?.map((p) => {
              const transformedPoint = fabric.util.transformPoint(
                new fabric.Point(p.x, p.y),
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
          ui_shape_data: JSON.stringify(
            obj.toObject(["zoneType", "zoneName", "zoneId"]),
          ),
          flattened_coordinates: JSON.stringify(flattened),
        };
      });

    try {
      const zoneObjects = objects.filter(
        (obj) =>
          (obj instanceof fabric.Path ||
            obj instanceof fabric.Polygon ||
            obj instanceof fabric.Circle) &&
          !!(obj as any).zoneType &&
          !(obj as any).zoneId,
      );
      for (let i = 0; i < zonesToSave.length; i++) {
        const res = await fetch(`${API_URL}/zones`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(zonesToSave[i]),
        });
        if (res.ok) {
          const saved = await res.json();
          // Stamp the DB id back so this object won't be re-saved next time
          (zoneObjects[i] as any).zoneId = saved.id;
        }
      }
      if (!silent) {
        setHasSavedConfiguration(true);
        setSaveStatus("saved");
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

  const startMonitoring = async () => {
    if (!bgImage || !fabricCanvas || !videoName) return;

    setIsAnalyzing(true);
    setIsMonitoring(false);
    setCurrentViolations([]);
    setActiveZoneBreaches(false);

    try {
      // Ensure zones are persisted to DB before analysis runs
      await saveConfiguration(true);

      const result = await analyzeVideo(bgImage);
      setAnalysisResult(result);
      setIsMonitoring(true);

      // Lock zones for monitoring
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
      videoRef.current.style.opacity = isMonitoring ? "1" : "0.8"; // Subtle hint that it's in config mode
    }
  }, [isMonitoring]);

  useEffect(() => {
    if (!isMonitoring || !analysisResult || !fabricCanvas) return;

    const fps = analysisResult.summary.fps || 30;
    const currentFrame = Math.floor(playbackTime * fps);
    const window = fps / 1.5;

    const activeThisFrame = analysisResult.reports.filter(
      (r) =>
        r.frame_index != null &&
        Math.abs(r.frame_index - currentFrame) < window,
    );

    const activeZoneViolations = (analysisResult.zone_violations ?? []).filter(
      (zv) => Math.abs(zv.frame_index - currentFrame) < window,
    );

    setCurrentViolations(activeThisFrame);
    setActiveZoneBreaches(activeZoneViolations.length > 0);

    const violatingZoneIds = new Set(
      activeZoneViolations.map((zv) => zv.zone_id),
    );

    fabricCanvas.getObjects().forEach((obj) => {
      const zoneId = (obj as any).zoneId;
      const isViolating = violatingZoneIds.has(zoneId);

      if (isViolating) {
        obj.set({
          fill: "rgba(255, 0, 0, 0.6)",
          stroke: "#ff0000",
          strokeWidth: 4,
        });
      } else {
        const originalStyles = getZoneStyles(
          (obj as any).zoneType || "RESTRICTED",
        );
        obj.set({ ...originalStyles, strokeWidth: 2 });
      }
    });
    fabricCanvas.renderAll();
  }, [playbackTime, isMonitoring, analysisResult, fabricCanvas]);

  return (
    <div className="flex flex-col gap-4 items-center w-full max-w-6xl mx-auto">
      <div className="flex flex-wrap gap-4 items-center justify-between w-full bg-zinc-900 p-3 rounded-lg border border-zinc-800">
        <div className="flex gap-2">
          <ToolButton
            active={tool === "select"}
            onClick={() => setTool("select")}
            disabled={isMonitoring}
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
          <ToolButton
            active={tool === "calibrate"}
            onClick={() => setTool("calibrate")}
            disabled={isMonitoring}
          >
            Calibrate
          </ToolButton>
          <div className="w-px h-6 bg-zinc-800 mx-1" />
          <ToolButton
            active={segmentType === "line"}
            onClick={() => setSegmentType("line")}
            disabled={tool !== "draw" || isMonitoring}
          >
            Line
          </ToolButton>
          <ToolButton
            active={segmentType === "curve"}
            onClick={() => setSegmentType("curve")}
            disabled={tool !== "draw" || isMonitoring}
          >
            Curve
          </ToolButton>
          <div className="w-px h-6 bg-zinc-800 mx-1" />
          <ToolButton
            active={false}
            onClick={deleteSelected}
            disabled={isMonitoring}
            className="border-red-900/50 text-red-500 hover:bg-red-500/10"
          >
            Delete
          </ToolButton>
        </div>

        <div className="flex gap-4 items-center">
          {!isMonitoring && (
            <div className="flex gap-2 items-center">
              <span className="text-[10px] font-mono text-zinc-500 uppercase">
                Zone Type:
              </span>
              <select
                value={zoneType}
                onChange={(e) => setZoneType(e.target.value as ZoneType)}
                className="bg-zinc-950 border border-zinc-800 text-xs font-mono px-2 py-1 rounded text-zinc-300 focus:outline-none focus:border-orange-500/50"
              >
                <option value="RESTRICTED">RESTRICTED</option>
                <option value="WALKWAY">WALKWAY</option>
                <option value="FORKLIFT_PATH">FORKLIFT_PATH</option>
              </select>
            </div>
          )}

          <div className="flex gap-2">
            <ToolButton
              active={false}
              onClick={() => saveConfiguration(false)}
              disabled={isMonitoring || !bgImage}
              className={`border-none px-4 ${saveStatus === "saved" ? "bg-emerald-700 hover:bg-emerald-600" : "bg-zinc-800 hover:bg-zinc-700"}`}
            >
              {saveStatus === "saving"
                ? "Saving..."
                : saveStatus === "saved"
                  ? "Saved ✓"
                  : "Save Zones"}
            </ToolButton>

            <ToolButton
              active={false}
              onClick={clearDbZones}
              disabled={isMonitoring || !videoName}
              className="bg-red-900 hover:bg-red-800 text-zinc-100 border-none px-4"
            >
              Clear DB Zones
            </ToolButton>

            <button
              onClick={
                isMonitoring ? () => setIsMonitoring(false) : startMonitoring
              }
              disabled={
                isAnalyzing ||
                !bgImage ||
                !bgImage.type.startsWith("video/") ||
                !hasSavedConfiguration
              }
              title={
                !hasSavedConfiguration
                  ? "Save zones before starting monitoring"
                  : undefined
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
                <button
                  onClick={() => setBgImage(null)}
                  className="text-[10px] font-mono text-zinc-500 hover:text-zinc-300 underline uppercase"
                >
                  Change Media
                </button>
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

              {/* Isolated container for Fabric.js to avoid React reconciliation conflicts */}
              <div key="fabric-host" className="absolute inset-0 z-10">
                <canvas ref={canvasRef} />
              </div>

              {isMonitoring && activeZoneBreaches && (
                <div className="absolute top-4 left-4 z-20 animate-bounce">
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
                <p>2. Set ground plane using CALIBRATE (4 points).</p>
                <p>3. Click SAVE ALL.</p>
                <p>
                  4. Click START MONITORING to run analysis and preview
                  incursions.
                </p>
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
