"use client";

import { useCallback, useMemo, useState } from "react";
import { useDropzone } from "react-dropzone";

import { BoundingBoxCanvas } from "@/components/BoundingBoxCanvas";
import { ResultsPanel } from "@/components/ResultsPanel";
import { SummaryBar } from "@/components/SummaryBar";
import { analyzeImage } from "@/lib/api";
import { DetectionResponse } from "@/types/detection";

type BatchStatus = "queued" | "analyzing" | "done" | "error";

interface BatchImageItem {
  id: string;
  file: File;
  status: BatchStatus;
  result: DetectionResponse | null;
  error: string;
}

const ACCEPTED_IMAGE_MIME: Record<string, string[]> = {
  "image/jpeg": [".jpg", ".jpeg"],
  "image/png": [".png"],
  "image/webp": [".webp"],
  "image/bmp": [".bmp"],
};

export function BatchImagesPanel() {
  const [items, setItems] = useState<BatchImageItem[]>([]);
  const [isAnalyzing, setIsAnalyzing] = useState(false);

  const totals = useMemo(() => {
    return items.reduce(
      (acc, item) => {
        if (item.status === "done" && item.result) {
          acc.done += 1;
          acc.persons += item.result.summary.total_persons;
          acc.violations += item.result.summary.violations;
        }
        if (item.status === "error") acc.errors += 1;
        return acc;
      },
      { done: 0, errors: 0, persons: 0, violations: 0 },
    );
  }, [items]);

  const analyzeFiles = useCallback(async (files: File[]) => {
    const imageFiles = files.filter((file) => file.type.startsWith("image/"));
    if (imageFiles.length === 0) return;

    const nextItems: BatchImageItem[] = imageFiles.map((file, index) => ({
      id: `${file.name}-${file.lastModified}-${file.size}-${index}`,
      file,
      status: "queued",
      result: null,
      error: "",
    }));

    setItems(nextItems);
    setIsAnalyzing(true);

    await Promise.all(
      nextItems.map(async (item) => {
        setItems((current) =>
          current.map((entry) =>
            entry.id === item.id ? { ...entry, status: "analyzing" } : entry,
          ),
        );

        try {
          const result = await analyzeImage(item.file);
          setItems((current) =>
            current.map((entry) =>
              entry.id === item.id
                ? { ...entry, status: "done", result, error: "" }
                : entry,
            ),
          );
        } catch (err) {
          setItems((current) =>
            current.map((entry) =>
              entry.id === item.id
                ? {
                    ...entry,
                    status: "error",
                    result: null,
                    error: err instanceof Error ? err.message : "Unknown error",
                  }
                : entry,
            ),
          );
        }
      }),
    );

    setIsAnalyzing(false);
  }, []);

  const onDrop = useCallback(
    (accepted: File[]) => {
      void analyzeFiles(accepted);
    },
    [analyzeFiles],
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: ACCEPTED_IMAGE_MIME,
    multiple: true,
    disabled: isAnalyzing,
  });

  const reset = () => {
    setItems([]);
    setIsAnalyzing(false);
  };

  return (
    <section className="max-w-6xl mx-auto flex flex-col gap-5">
      <div
        {...getRootProps()}
        className={[
          "relative border-2 border-dashed rounded-lg transition-all duration-200",
          "flex flex-col items-center justify-center min-h-[220px] overflow-hidden",
          isDragActive ? "border-orange-500 bg-orange-500/5" : "border-zinc-700 hover:border-zinc-500",
          isAnalyzing ? "opacity-50 cursor-not-allowed" : "cursor-pointer",
        ].join(" ")}
      >
        <input {...getInputProps()} />
        <div className="flex flex-col items-center gap-3 p-8 text-center select-none">
          <svg
            className="w-12 h-12 text-zinc-600"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"
            />
          </svg>
          <p className="text-zinc-400 font-mono text-sm">
            {isDragActive ? "DROP IMAGES HERE" : "DRAG & DROP OR CLICK TO UPLOAD IMAGES"}
          </p>
          <p className="text-zinc-600 font-mono text-xs">
            JPG / PNG / WEBP / BMP
          </p>
        </div>
      </div>

      {items.length > 0 && (
        <div className="bg-zinc-950 border border-zinc-800 rounded-lg p-3">
          <p className="font-mono text-[10px] text-zinc-600 tracking-widest mb-3">
            BATCH SUMMARY
          </p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <BatchMetric label="IMAGES" value={items.length} />
            <BatchMetric label="COMPLETED" value={totals.done} accent="green" />
            <BatchMetric label="WORKERS" value={totals.persons} />
            <BatchMetric label="VIOLATIONS" value={totals.violations} accent={totals.violations > 0 ? "red" : "green"} />
          </div>
          {totals.errors > 0 && (
            <p className="mt-3 font-mono text-xs text-red-400">
              {totals.errors} image{totals.errors === 1 ? "" : "s"} could not be analyzed.
            </p>
          )}
        </div>
      )}

      <div className="flex flex-col gap-4">
        {items.map((item) => (
          <ImageResultCard key={item.id} item={item} />
        ))}
      </div>

      {items.length > 0 && (
        <button
          onClick={reset}
          className="
            w-fit font-mono text-xs text-zinc-500 hover:text-orange-400 transition-colors
            border border-zinc-800 hover:border-orange-500/30 rounded px-4 py-2
          "
        >
          CLEAR BATCH
        </button>
      )}
    </section>
  );
}

function BatchMetric({
  label,
  value,
  accent,
}: {
  label: string;
  value: string | number;
  accent?: "green" | "red";
}) {
  const valueClass =
    accent === "green" ? "text-green-400" : accent === "red" ? "text-red-400" : "text-orange-400";

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 min-w-0">
      <p className="text-zinc-500 font-mono text-[10px] tracking-widest truncate">{label}</p>
      <p className={`font-mono text-sm font-bold ${valueClass}`}>{value}</p>
    </div>
  );
}

function ImageResultCard({ item }: { item: BatchImageItem }) {
  return (
    <article className="bg-zinc-950 border border-zinc-800 rounded-lg p-3">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div className="min-w-0">
          <p className="font-mono text-[10px] text-zinc-600 tracking-widest uppercase">
            Image Result
          </p>
          <h2 className="text-sm font-semibold text-zinc-100 truncate">
            {item.file.name}
          </h2>
        </div>
        <StatusPill status={item.status} />
      </div>

      {item.status === "queued" || item.status === "analyzing" ? (
        <div className="border border-orange-500/30 bg-orange-500/10 rounded-lg p-4 font-mono text-sm text-orange-300">
          {item.status === "queued" ? "Queued for analysis" : "Analyzing image..."}
        </div>
      ) : item.status === "error" ? (
        <div className="border border-red-500/30 bg-red-500/10 rounded-lg p-4 font-mono text-sm text-red-300">
          {item.error}
        </div>
      ) : item.result ? (
        <div className="flex flex-col gap-3">
          <SummaryBar summary={item.result.summary} />
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-4 items-start">
            <BoundingBoxCanvas imageFile={item.file} detections={item.result.detections} />
            <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-3 max-h-[520px] overflow-hidden">
              <ResultsPanel persons={item.result.persons} />
            </div>
          </div>
        </div>
      ) : null}
    </article>
  );
}

function StatusPill({ status }: { status: BatchStatus }) {
  const styles: Record<BatchStatus, string> = {
    queued: "bg-zinc-500/10 border-zinc-500/30 text-zinc-400",
    analyzing: "bg-orange-500/10 border-orange-500/40 text-orange-300",
    done: "bg-green-500/10 border-green-500/40 text-green-300",
    error: "bg-red-500/10 border-red-500/40 text-red-300",
  };

  const labels: Record<BatchStatus, string> = {
    queued: "QUEUED",
    analyzing: "ANALYZING",
    done: "DONE",
    error: "ERROR",
  };

  return (
    <span className={`font-mono text-[10px] rounded-full px-3 py-1 border shrink-0 ${styles[status]}`}>
      {labels[status]}
    </span>
  );
}
