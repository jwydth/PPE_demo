"use client";

import { useEffect, useRef, useCallback } from "react";

import { Detection } from "@/types/detection";

interface BoundingBoxCanvasProps {
  imageFile: File;
  detections: Detection[];
  onHeightReady?: (height: number) => void;
}

export function BoundingBoxCanvas({ imageFile, detections, onHeightReady }: BoundingBoxCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const srcRef = useRef<string>("");
  const containerRef = useRef<HTMLDivElement>(null);

  const reportHeight = useCallback(() => {
    if (containerRef.current && onHeightReady) {
      onHeightReady(containerRef.current.offsetHeight);
    }
  }, [onHeightReady]);

  useEffect(() => {
    // Revoke the previous object URL
    if (srcRef.current) URL.revokeObjectURL(srcRef.current);
    const url = URL.createObjectURL(imageFile);
    srcRef.current = url;

    const img = imgRef.current;
    const canvas = canvasRef.current;
    if (!img || !canvas) return;

    const draw = () => {
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      drawDetections(ctx, detections, canvas.width);
      // Report rendered height after the image has loaded & sized
      reportHeight();
    };

    img.src = url;
    if (img.complete && img.naturalWidth > 0) {
      draw();
    } else {
      img.onload = draw;
    }

    return () => {
      img.onload = null;
    };
  }, [imageFile, detections, reportHeight]);

  // Also report height on window resize (image scales with CSS)
  useEffect(() => {
    const handleResize = () => reportHeight();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [reportHeight]);

  return (
    <div ref={containerRef} className="relative w-full">
      <img
        ref={imgRef}
        alt="Analyzed frame"
        className="w-full rounded-lg block"
      />
      <canvas
        ref={canvasRef}
        className="absolute inset-0 w-full h-full rounded-lg pointer-events-none"
      />
    </div>
  );
}

// ── Drawing ──────────────────────────────────────────────────────────────────

function drawDetections(
  ctx: CanvasRenderingContext2D,
  detections: Detection[],
  canvasWidth: number,
) {
  for (const det of detections) {
    const { x1, y1, x2, y2 } = det.bbox;
    const w = x2 - x1;
    const h = y2 - y1;
    const color = det.color;
    const cornerLen = Math.min(w, h) * 0.22;

    // Semi-transparent fill
    ctx.fillStyle = `${color}1a`;
    ctx.fillRect(x1, y1, w, h);

    // Corner accent lines
    ctx.strokeStyle = color;
    ctx.lineWidth = Math.max(1.5, canvasWidth / 500);
    ctx.lineCap = "square";
    ctx.beginPath();

    // Top-left
    ctx.moveTo(x1, y1 + cornerLen);
    ctx.lineTo(x1, y1);
    ctx.lineTo(x1 + cornerLen, y1);

    // Top-right
    ctx.moveTo(x2 - cornerLen, y1);
    ctx.lineTo(x2, y1);
    ctx.lineTo(x2, y1 + cornerLen);

    // Bottom-right
    ctx.moveTo(x2, y2 - cornerLen);
    ctx.lineTo(x2, y2);
    ctx.lineTo(x2 - cornerLen, y2);

    // Bottom-left
    ctx.moveTo(x1 + cornerLen, y2);
    ctx.lineTo(x1, y2);
    ctx.lineTo(x1, y2 - cornerLen);

    ctx.stroke();

    // Label
    const label = det.label;
    const isPersonLabel = label.startsWith("P");

    if (isPersonLabel) {
      const fontSize = Math.max(10, Math.min(14, canvasWidth / 55));
      ctx.font = `600 ${fontSize}px "IBM Plex Mono", monospace`;
      const textW = ctx.measureText(label).width;
      const padX = 6;
      const padY = 4;
      const labelH = fontSize + padY * 2;

      // Person labels → bottom-inside of the box
      ctx.fillStyle = color;
      ctx.fillRect(x1, y2 - labelH, textW + padX * 2, labelH);
      ctx.fillStyle = "#0a0c0f";
      ctx.fillText(label, x1 + padX, y2 - padY);
    }
  }
}
