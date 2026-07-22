"use client";

import { TrendingDown, TrendingUp } from "lucide-react";
import { IncidentCard } from "@/components/ppe/result-panels";
import { countLabel } from "@/lib/format";
import type { ZoneDef } from "./factory-layout";
import type { ZoneAggregate } from "./use-zone-incidents";

const CATEGORY_READOUT: { key: "ppeCount" | "zoneCount" | "fallCount"; label: string }[] = [
  { key: "ppeCount", label: "PPE" },
  { key: "zoneCount", label: "Zone" },
  { key: "fallCount", label: "Fall" },
];

interface ZoneDetailPanelProps {
  zone: ZoneDef | null;
  aggregate: ZoneAggregate | null;
  loading: boolean;
}

export function ZoneDetailPanel({ zone, aggregate, loading }: ZoneDetailPanelProps) {
  if (!zone) {
    return (
      <aside className="rounded-md border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
        Select a zone to view its incident log.
      </aside>
    );
  }

  const total = aggregate?.total ?? 0;
  const trendDelta = aggregate?.trendDelta ?? 0;

  return (
    <aside className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-4">
      <div>
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-semibold text-slate-950">
            {zone.id} · {zone.label}
          </h3>
          {zone.active ? (
            <span className="flex items-center gap-1 text-xs font-semibold text-slate-600">
              {trendDelta > 0 ? (
                <TrendingUp className="size-3.5 text-red-600" aria-hidden="true" />
              ) : trendDelta < 0 ? (
                <TrendingDown className="size-3.5 text-emerald-600" aria-hidden="true" />
              ) : null}
              {trendDelta !== 0 ? `${trendDelta > 0 ? "+" : ""}${trendDelta} vs prior 24h` : null}
            </span>
          ) : null}
        </div>
        {zone.active ? (
          <p className="mt-1 text-xs text-slate-600">{countLabel(total, "incident")} logged</p>
        ) : null}
        {zone.active && aggregate && total > 0 ? (
          <dl className="mt-2 grid grid-cols-3 gap-2 rounded-md border border-slate-200 bg-white p-2">
            {CATEGORY_READOUT.map(({ key, label }) => (
              <div key={key} className="text-center">
                <dt className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">{label}</dt>
                <dd className="font-mono text-sm font-semibold text-slate-900">{aggregate[key]}</dd>
              </div>
            ))}
          </dl>
        ) : null}
      </div>

      {!zone.active ? (
        <p className="rounded-md border border-slate-200 bg-white p-3 text-sm text-slate-600">
          This zone has no connected camera yet. Monitoring is planned.
        </p>
      ) : loading ? (
        <p className="rounded-md border border-slate-200 bg-white p-3 text-sm text-slate-600">
          Loading incidents…
        </p>
      ) : total === 0 ? (
        <p className="rounded-md border border-slate-200 bg-white p-3 text-sm text-slate-600">
          No incidents recorded for this zone yet.
        </p>
      ) : (
        <div className="grid max-h-[520px] gap-2 overflow-y-auto pr-1">
          {aggregate!.incidents.map((event, index) => (
            <IncidentCard key={`${event.id ?? index}-${event.timestamp}`} event={event} compact />
          ))}
        </div>
      )}
    </aside>
  );
}
