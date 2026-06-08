"use client";

import { useCallback, useEffect, useState } from "react";

import { BatchImagesPanel } from "@/components/BatchImagesPanel";
import { BoundingBoxCanvas } from "@/components/BoundingBoxCanvas";
import { DetectionHistoryPanel } from "@/components/DetectionHistoryPanel";
import { ResultsPanel } from "@/components/ResultsPanel";
import { SummaryBar } from "@/components/SummaryBar";
import { UploadZone } from "@/components/UploadZone";
import { VideoReportsPanel } from "@/components/VideoReportsPanel";
import { ZoneDrawingCanvas } from "@/components/ZoneDrawingCanvas";
import { analyzeImage, analyzeVideo } from "@/lib/api";
import { DetectionResponse, VideoProcessingResponse } from "@/types/detection";

type ActiveTab = "detect" | "batch" | "history" | "zones";
type Phase = "idle" | "analyzing" | "done" | "error";
type FileKind = "image" | "video";

export default function Home() {
  const [activeTab, setActiveTab] = useState<ActiveTab>("detect");
  const [navDisabled, setNavDisabled] = useState(false);

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as boolean;
      setNavDisabled(!!detail);
    };
    window.addEventListener("zone-drawing-active", handler as EventListener);
    return () => window.removeEventListener("zone-drawing-active", handler as EventListener);
  }, []);

  return (
    <main className="min-h-screen bg-[#0a0c0f] text-zinc-100 p-6 md:p-10">
      <header className="mb-6 border-b border-zinc-800 pb-6">
        <div className="flex items-center gap-3 mb-2">
          <span className="w-2 h-2 rounded-full bg-orange-500 animate-pulse" />
          <p className="font-mono text-orange-500 text-xs tracking-widest uppercase">
            De Heus / Smart Factory / Safety Monitor v1.1
          </p>
        </div>
        <h1 className="font-sans text-2xl font-bold text-zinc-100">
          PPE Compliance Detection
        </h1>
        <p className="text-zinc-500 text-sm mt-1 font-mono">
          Upload image frames or CCTV clips to scan for personal protective equipment violations
        </p>
      </header>

      <nav className="max-w-6xl mx-auto mb-6 flex flex-wrap gap-2">
        <TabButton active={activeTab === "detect"} onClick={() => setActiveTab("detect")} disabled={navDisabled}>
          Detect PPE
        </TabButton>
        <TabButton active={activeTab === "batch"} onClick={() => setActiveTab("batch")} disabled={navDisabled}>
          Batch Images
        </TabButton>
        <TabButton active={activeTab === "history"} onClick={() => setActiveTab("history")} disabled={navDisabled}>
          Detection History
        </TabButton>
        <TabButton active={activeTab === "zones"} onClick={() => setActiveTab("zones")} disabled={navDisabled}>
          Zone Configuration
        </TabButton>
      </nav>

      {activeTab === "detect" && <DetectPpePanel />}
      {activeTab === "batch" && <BatchImagesPanel />}
      {activeTab === "history" && <DetectionHistoryPanel />}
      {activeTab === "zones" && <ZoneDrawingCanvas />}
    </main>
  );
}

function TabButton({
  active,
  onClick,
  children,
  disabled = false,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={() => {
        if (disabled) return;
        onClick();
      }}
      disabled={disabled}
      className={[
        "font-mono text-xs rounded border px-4 py-2 transition-colors",
        active
          ? "border-orange-500/50 bg-orange-500/10 text-orange-300"
          : "border-zinc-800 bg-zinc-950 text-zinc-500 hover:text-zinc-200 hover:border-zinc-600",
        disabled ? "opacity-40 cursor-not-allowed pointer-events-none" : "",
      ].join(" ")}
    >
      {children}
    </button>
  );
}

function DetectPpePanel() {
  const [file, setFile] = useState<File | null>(null);
  const [fileKind, setFileKind] = useState<FileKind>("image");
  const [result, setResult] = useState<DetectionResponse | null>(null);
  const [videoResult, setVideoResult] = useState<VideoProcessingResponse | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [errorMsg, setErrorMsg] = useState("");
  const [imageHeight, setImageHeight] = useState<number | null>(null);

  const handleFile = async (selected: File) => {
    const selectedKind = selected.type.startsWith("video/") ? "video" : "image";
    setFile(selected);
    setFileKind(selectedKind);
    setResult(null);
    setVideoResult(null);
    setErrorMsg("");
    setImageHeight(null);
    setPhase("analyzing");

    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(URL.createObjectURL(selected));

    try {
      if (selectedKind === "video") {
        setVideoResult(await analyzeVideo(selected));
      } else {
        setResult(await analyzeImage(selected));
      }
      setPhase("done");
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Unknown error");
      setPhase("error");
    }
  };

  const reset = () => {
    setPhase("idle");
    setFile(null);
    setResult(null);
    setVideoResult(null);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(null);
    setErrorMsg("");
    setImageHeight(null);
  };

  const onImageHeightReady = useCallback((h: number) => {
    setImageHeight(h);
  }, []);

  return (
    <>
      {phase !== "done" && (
        <div className="max-w-xl mx-auto flex flex-col gap-4">
          <p className="font-mono text-xs text-zinc-500">
            Image detection is for quick testing only. Confirmed incidents are saved from video/CCTV analysis.
          </p>
          <UploadZone onFileSelect={handleFile} disabled={phase === "analyzing"} />

          {phase === "analyzing" && (
            <div className="flex items-center gap-3 text-orange-400 font-mono text-sm">
              <svg className="w-4 h-4 animate-spin shrink-0" fill="none" viewBox="0 0 24 24">
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
              </svg>
              {fileKind === "video" ? "PROCESSING VIDEO..." : "RUNNING INFERENCE..."}
            </div>
          )}

          {phase === "error" && (
            <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 font-mono text-sm text-red-400">
              <span className="font-bold">ERROR</span> - {errorMsg}
            </div>
          )}
        </div>
      )}

      {phase === "done" && file && result && (
        <div className="max-w-6xl mx-auto flex flex-col gap-4">
          <SummaryBar summary={result.summary} />

          <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-4 items-start">
            <BoundingBoxCanvas
              imageFile={file}
              detections={result.detections}
              onHeightReady={onImageHeightReady}
            />

            <div
              className="bg-zinc-950 border border-zinc-800 rounded-lg p-3"
              style={imageHeight ? { height: imageHeight, overflow: "hidden" } : undefined}
            >
              <ResultsPanel persons={result.persons} />
            </div>
          </div>

          <ResetButton onClick={reset} />
        </div>
      )}

      {phase === "done" && file && videoResult && (
        <div className="max-w-6xl mx-auto flex flex-col gap-4">
          <div>
            <p className="font-mono text-[10px] text-orange-500 tracking-widest uppercase">
              Analysis Result
            </p>
            <h2 className="mt-1 text-xl font-semibold text-zinc-100">
              {videoResult.summary.video_name}
            </h2>
          </div>

          <VideoReportsPanel result={videoResult} previewUrl={previewUrl || undefined} />

          <ResetButton onClick={reset} />
        </div>
      )}
    </>
  );
}

function ResetButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="
        w-fit font-mono text-xs text-zinc-500 hover:text-orange-400 transition-colors
        border border-zinc-800 hover:border-orange-500/30 rounded px-4 py-2
      "
    >
      ANALYZE ANOTHER FILE
    </button>
  );
}
