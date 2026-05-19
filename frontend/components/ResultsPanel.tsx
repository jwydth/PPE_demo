import { EquipmentStatus, PersonResult, Summary } from "@/types/detection";

import { StatusBadge } from "./StatusBadge";

interface ResultsPanelProps {
  summary: Summary;
  persons: PersonResult[];
}

export function ResultsPanel({ summary, persons }: ResultsPanelProps) {
  return (
    <div className="flex flex-col gap-4 h-full">
      <StatusBadge violation={summary.violations > 0} />

      {/* Summary stats */}
      <div className="grid grid-cols-2 gap-3">
        <StatCard label="PERSONS"    value={summary.total_persons} />
        <StatCard label="COMPLIANT"  value={summary.compliant}  accent="green" />
        <StatCard label="VIOLATIONS" value={summary.violations} accent="red" />
        <StatCard label="INFERENCE"  value={`${summary.inference_ms.toFixed(1)} ms`} />
      </div>

      {/* Per-person breakdown */}
      <div className="flex-1 overflow-y-auto flex flex-col gap-3 pr-1 min-h-0">
        <p className="font-mono text-[10px] text-zinc-600 tracking-widest">
          WORKERS ({persons.length})
        </p>
        {persons.map((person) => (
          <PersonCard key={person.person_id} person={person} />
        ))}
      </div>
    </div>
  );
}

// ── Stat card ─────────────────────────────────────────────────────────────────

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

// ── Person card ───────────────────────────────────────────────────────────────

function PersonCard({ person }: { person: PersonResult }) {
  const borderColor = person.compliant
    ? "border-green-500/30"
    : "border-red-500/30";
  const headerBg = person.compliant
    ? "bg-green-500/10"
    : "bg-red-500/10";
  const badgeText  = person.compliant ? "COMPLIANT" : "VIOLATION";
  const badgeColor = person.compliant
    ? "bg-green-500/15 text-green-400"
    : "bg-red-500/15 text-red-400";

  return (
    <div className={`bg-zinc-900 border ${borderColor} rounded-lg overflow-hidden`}>
      {/* Person header */}
      <div className={`flex items-center justify-between px-3 py-2 ${headerBg}`}>
        <div className="flex items-center gap-2">
          <span className="text-zinc-400 font-mono text-xs">
            👷 Person {person.person_id}
          </span>
          <span className="font-mono text-[10px] text-zinc-600">
            {(person.confidence * 100).toFixed(0)}%
          </span>
        </div>
        <span className={`font-mono text-[10px] px-2 py-0.5 rounded-full ${badgeColor}`}>
          {badgeText}
        </span>
      </div>

      {/* Equipment rows */}
      <div className="flex flex-col divide-y divide-zinc-800">
        {person.equipment.map((eq) => (
          <EquipmentRow key={eq.label} eq={eq} />
        ))}
      </div>
    </div>
  );
}

// ── Equipment row ─────────────────────────────────────────────────────────────

function EquipmentRow({ eq }: { eq: EquipmentStatus }) {
  const isCompliant = eq.status === "compliant";
  const icon        = isCompliant ? "✓" : "✗";
  const iconColor   = isCompliant ? "text-green-400" : "text-red-400";
  const labelColor  = isCompliant ? "text-zinc-200"  : "text-red-300";
  const statusText  = isCompliant ? "Detected" : "Not detected";
  const barColor    = isCompliant ? "#22c55e" : "#ef4444";
  const pct         = eq.confidence !== undefined ? Math.round(eq.confidence * 100) : null;

  return (
    <div className="flex items-center gap-3 px-3 py-2">
      {/* Icon */}
      <span className={`font-mono text-sm font-bold w-4 shrink-0 ${iconColor}`}>
        {icon}
      </span>

      {/* Label + status */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-2">
          <span className={`font-mono text-xs ${labelColor} truncate`}>{eq.label}</span>
          <span className="font-mono text-[10px] text-zinc-500 shrink-0">{statusText}</span>
        </div>

        {/* Confidence bar — only when detected */}
        {pct !== null && (
          <div className="flex items-center gap-2 mt-1">
            <div className="flex-1 h-1 bg-zinc-800 rounded-full overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-500"
                style={{ width: `${pct}%`, backgroundColor: barColor }}
              />
            </div>
            <span className="font-mono text-[10px] text-zinc-500 w-8 text-right shrink-0">
              {pct}%
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
