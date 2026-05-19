import { Detection, Summary } from "@/types/detection";

import { StatusBadge } from "./StatusBadge";

interface ResultsPanelProps {
  summary: Summary;
  detections: Detection[];
}

export function ResultsPanel({ summary, detections }: ResultsPanelProps) {
  return (
    <div className="flex flex-col gap-4 h-full">
      <StatusBadge violation={summary.violations > 0} />

      <div className="grid grid-cols-2 gap-3">
        <StatCard label="PERSONS" value={summary.total_persons} />
        <StatCard label="COMPLIANT" value={summary.compliant} accent="green" />
        <StatCard label="VIOLATIONS" value={summary.violations} accent="red" />
        <StatCard label="INFERENCE" value={`${summary.inference_ms.toFixed(1)} ms`} />
      </div>

      <div className="flex-1 overflow-y-auto flex flex-col gap-2 pr-1 min-h-0">
        <p className="font-mono text-[10px] text-zinc-600 tracking-widest">
          DETECTIONS ({detections.length})
        </p>
        {detections.map((det) => (
          <DetectionCard key={det.id} detection={det} />
        ))}
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  accent,
}: {
  label: string;
  value: string | number;
  accent?: "green" | "red";
}) {
  const valueClass =
    accent === "green"
      ? "text-green-400"
      : accent === "red"
        ? "text-red-400"
        : "text-orange-400";

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-3">
      <p className="text-zinc-500 font-mono text-[10px] tracking-widest mb-1">{label}</p>
      <p className={`font-mono text-xl font-bold ${valueClass}`}>{value}</p>
    </div>
  );
}

function DetectionCard({ detection }: { detection: Detection }) {
  const isViolation = detection.category === "violation";
  const pct = Math.round(detection.confidence * 100);

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-3 flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-sm text-zinc-200 truncate">{detection.label}</span>
        <span
          className={`font-mono text-[10px] px-2 py-0.5 rounded-full shrink-0 ${
            isViolation
              ? "bg-red-500/15 text-red-400"
              : "bg-green-500/15 text-green-400"
          }`}
        >
          {detection.category.toUpperCase()}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <div className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{ width: `${pct}%`, backgroundColor: detection.color }}
          />
        </div>
        <span className="font-mono text-xs text-zinc-400 w-10 text-right shrink-0">
          {pct}%
        </span>
      </div>
    </div>
  );
}
