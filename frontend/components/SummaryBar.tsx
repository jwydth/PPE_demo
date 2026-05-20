import { Summary } from "@/types/detection";

import { StatusBadge } from "./StatusBadge";

interface SummaryBarProps {
  summary: Summary;
}

export function SummaryBar({ summary }: SummaryBarProps) {
  return (
    <div className="flex items-center gap-3 flex-wrap">
      <StatusBadge violation={summary.violations > 0} />

      <div className="flex items-center gap-3 flex-1 min-w-0">
        <StatChip label="PERSONS"    value={summary.total_persons} />
        <StatChip label="COMPLIANT"  value={summary.compliant}  accent="green" />
        <StatChip label="VIOLATIONS" value={summary.violations} accent="red" />
        <StatChip label="INFERENCE"  value={`${summary.inference_ms.toFixed(1)} ms`} />
      </div>
    </div>
  );
}

function StatChip({
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
    <div className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 flex items-center gap-2 min-w-0">
      <span className="text-zinc-500 font-mono text-[10px] tracking-widest whitespace-nowrap">{label}</span>
      <span className={`font-mono text-sm font-bold ${valueClass} whitespace-nowrap`}>{value}</span>
    </div>
  );
}
