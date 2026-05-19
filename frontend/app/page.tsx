"use client";

import { useState } from "react";

import { BoundingBoxCanvas } from "@/components/BoundingBoxCanvas";
import { ResultsPanel } from "@/components/ResultsPanel";
import { UploadZone } from "@/components/UploadZone";
import { analyzeImage } from "@/lib/api";
import { DetectionResponse } from "@/types/detection";

type Phase = "idle" | "analyzing" | "done" | "error";

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<DetectionResponse | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [errorMsg, setErrorMsg] = useState("");

  const handleFile = async (selected: File) => {
    setFile(selected);
    setResult(null);
    setErrorMsg("");
    setPhase("analyzing");

    try {
      const data = await analyzeImage(selected);
      setResult(data);
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
    setErrorMsg("");
  };

  return (
    <main className="min-h-screen bg-[#0a0c0f] text-zinc-100 p-6 md:p-10">
      {/* Header */}
      <header className="mb-8 border-b border-zinc-800 pb-6">
        <div className="flex items-center gap-3 mb-2">
          <span className="w-2 h-2 rounded-full bg-orange-500 animate-pulse" />
          <p className="font-mono text-orange-500 text-xs tracking-widest uppercase">
            De Heus · Smart Factory · Safety Monitor v1.0
          </p>
        </div>
        <h1 className="font-sans text-2xl font-bold text-zinc-100">
          PPE Compliance Detection
        </h1>
        <p className="text-zinc-500 text-sm mt-1 font-mono">
          Upload a frame to scan for personal protective equipment violations
        </p>
      </header>

      {/* Upload / analyzing / error state */}
      {phase !== "done" && (
        <div className="max-w-xl mx-auto flex flex-col gap-4">
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
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8v8H4z"
                />
              </svg>
              RUNNING INFERENCE…
            </div>
          )}

          {phase === "error" && (
            <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 font-mono text-sm text-red-400">
              <span className="font-bold">ERROR</span> — {errorMsg}
            </div>
          )}
        </div>
      )}

      {/* Results state */}
      {phase === "done" && file && result && (
        <div className="max-w-6xl mx-auto flex flex-col gap-6">
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-6 items-start">
            <BoundingBoxCanvas imageFile={file} detections={result.detections} />
            <ResultsPanel summary={result.summary} detections={result.detections} />
          </div>

          <button
            onClick={reset}
            className="
              w-fit font-mono text-xs text-zinc-500 hover:text-orange-400 transition-colors
              border border-zinc-800 hover:border-orange-500/30 rounded px-4 py-2
            "
          >
            ← ANALYZE ANOTHER IMAGE
          </button>
        </div>
      )}
    </main>
  );
}
