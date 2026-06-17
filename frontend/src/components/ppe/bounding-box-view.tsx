"use client";

import { useEffect, useMemo, useState } from "react";
import { Detection } from "@/types/detection";

export function BoundingBoxView({
  file,
  detections,
  showConfidence = true,
}: {
  file: File;
  detections: Detection[];
  showConfidence?: boolean;
}) {
  const [size, setSize] = useState({ width: 1, height: 1 });
  const url = useMemo(() => URL.createObjectURL(file), [file]);

  useEffect(() => {
    return () => URL.revokeObjectURL(url);
  }, [url]);

  return (
    <div className="overflow-hidden rounded-md border border-slate-200 bg-slate-950">
      <div className="relative mx-auto max-h-[620px] w-fit">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={url}
          alt="PPE analysis"
          className="max-h-[620px] w-auto max-w-full object-contain"
          onLoad={(event) =>
            setSize({
              width: event.currentTarget.naturalWidth || 1,
              height: event.currentTarget.naturalHeight || 1,
            })
          }
        />
        {detections.map((detection) => {
          const left = (detection.bbox.x1 / size.width) * 100;
          const top = (detection.bbox.y1 / size.height) * 100;
          const width = ((detection.bbox.x2 - detection.bbox.x1) / size.width) * 100;
          const height = ((detection.bbox.y2 - detection.bbox.y1) / size.height) * 100;
          const color =
            detection.color || (detection.category === "violation" ? "#ef4444" : "#84cc16");

          return (
            <div
              key={detection.id}
              className="absolute border-2 shadow-[0_0_0_1px_rgba(0,0,0,0.35)]"
              style={{ left: `${left}%`, top: `${top}%`, width: `${width}%`, height: `${height}%`, borderColor: color }}
            >
              <span
                className="absolute -top-7 left-0 whitespace-nowrap rounded px-2 py-1 text-xs font-semibold text-white"
                style={{ backgroundColor: color }}
              >
                {detection.label}
                {showConfidence ? ` ${Math.round(detection.confidence * 100)}%` : ""}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
