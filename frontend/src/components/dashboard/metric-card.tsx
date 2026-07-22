import { type SafetyMetric } from "./data";

const metricTone: Record<SafetyMetric["tone"], string> = {
  green: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  amber: "bg-amber-50 text-amber-700 ring-amber-200",
  blue: "bg-sky-50 text-sky-700 ring-sky-200",
  slate: "bg-slate-100 text-slate-700 ring-slate-200",
};

export function MetricCard({ metric }: { metric: SafetyMetric }) {
  return (
    <section className="rounded-md border border-slate-200 bg-white p-3 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-slate-500">{metric.label}</p>
          <p className="mt-2 text-2xl font-semibold text-slate-950">{metric.value}</p>
        </div>
        <div className={`rounded-md p-2 ring-1 ${metricTone[metric.tone]}`}>
          <metric.icon className="size-5" aria-hidden="true" />
        </div>
      </div>
      <p className="mt-2 text-sm text-slate-600">{metric.helper}</p>
      <p
        className={`mt-1 text-xs font-semibold uppercase tracking-wide ${
          metric.tone === "slate" ? "text-slate-400" : "text-emerald-700"
        }`}
      >
        {metric.trend}
      </p>
    </section>
  );
}
