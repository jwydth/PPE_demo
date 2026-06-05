"use client";

import { VideoProcessingResponse } from "@/types/detection";
import { ViolationReportCard } from "./ViolationReportCard";

interface VideoReportsPanelProps {
  result: VideoProcessingResponse;
  previewUrl?: string;
}

export function VideoReportsPanel({ result, previewUrl }: VideoReportsPanelProps) {
  const totalIncidents = result.reports.length;

  return (
    <div className="flex flex-col gap-4">
      {previewUrl && (
        <div className="bg-zinc-950 border border-zinc-800 rounded-lg overflow-hidden flex justify-center shadow-2xl">
          <video src={previewUrl} controls muted playsInline className="max-h-[400px] w-auto" />
        </div>
      )}
      
      <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4 items-start">
        <div className="bg-zinc-950 border border-zinc-800 rounded-lg p-3">
          <p className="font-mono text-[10px] text-zinc-600 tracking-widest mb-3">
            SAFETY SUMMARY
          </p>
          <div className="grid grid-cols-2 gap-2">
            <Metric 
              label="TOTAL INCIDENTS" 
              value={totalIncidents} 
              accent={totalIncidents > 0 ? "red" : "green"} 
            />
            <Metric 
              label="INFERENCE TIME" 
              value={formatInferenceTime(result.summary.inference_ms)} 
            />
          </div>
        </div>

        <div className="bg-zinc-950 border border-zinc-800 rounded-lg p-3">
          <p className="font-mono text-[10px] text-zinc-600 tracking-widest mb-3">
            SAFETY INCIDENTS ({result.reports.length})
          </p>
          {result.reports.length === 0 ? (
            <div className="border border-green-500/30 bg-green-500/10 rounded-lg p-4 font-mono text-sm text-green-300">
              No safety incidents were detected in this video.
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {result.reports.map((report) => (
                <ViolationReportCard key={report.id} report={report} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  accent,
}: {
  label: string;
  value: string | number;
  accent?: "red" | "green";
}) {
  const valueColor =
    accent === "red" ? "text-red-400" : accent === "green" ? "text-green-400" : "text-orange-400";

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 min-w-0">
      <p className="text-zinc-500 font-mono text-[10px] tracking-widest truncate">{label}</p>
      <p className={`font-mono text-sm font-bold ${valueColor}`}>
        {value}
      </p>
    </div>
  );
}

function formatInferenceTime(inferenceMs: number): string {
  return `${(inferenceMs / 1000).toFixed(1)}s`;
}
