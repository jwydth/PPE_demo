"use client";

import {
  Bell,
  Calendar,
  Camera as CameraIcon,
  ChevronDown,
  ClipboardCheck,
  Factory,
  FileDown,
  Minus,
  Siren,
  Trash2,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ReportExportDialog } from "@/components/analytics/report-export-dialog";
import { ScheduleReportDialog } from "@/components/analytics/schedule-report-dialog";
import { IncidentDetailModal, type IncidentCategory } from "@/components/dashboard/incident-detail-modal";
import { useSafetyKpis } from "@/hooks/useSafetyKpis";
import { CONFIRM_DELETE_INCIDENT } from "@/lib/messages";
import {
  deleteIncident,
  getAnalyticsCompare,
  getAnalyticsTrend,
  getUnifiedIncidents,
} from "@/lib/ppe-api";
import {
  AnalyticsRangeParam,
  CompareMode,
  SeverityCounts,
  SeverityLevel,
  UnifiedIncident,
  ZoneTotal,
} from "@/types/analytics";

const FEED_POLL_MS = 5000;
const FEED_LIMIT = 30;
const FRESH_ROW_MS = 6000;
const ANALYTICS_POLL_MS = 5000;

const ZONE_COLOR_PALETTE = [
  "#0ea5e9",
  "#8b5cf6",
  "#f59e0b",
  "#f43f5e",
  "#14b8a6",
  "#eab308",
  "#6366f1",
  "#ec4899",
];
const UNASSIGNED_COLOR = "#94a3b8";

const SEVERITY_TONE: Record<SeverityLevel, { text: string; bg: string; ring: string; hex: string }> = {
  Critical: { text: "text-red-700", bg: "bg-red-50", ring: "ring-red-200", hex: "#ef4444" },
  High: { text: "text-orange-700", bg: "bg-orange-50", ring: "ring-orange-200", hex: "#f97316" },
  Medium: { text: "text-amber-700", bg: "bg-amber-50", ring: "ring-amber-200", hex: "#f59e0b" },
  Low: { text: "text-emerald-700", bg: "bg-emerald-50", ring: "ring-emerald-200", hex: "#10b981" },
};

const CATEGORY_BADGE: Record<"ppe" | "zone" | "behavior", { label: string; className: string }> = {
  ppe: { label: "PPE", className: "bg-red-50 text-red-700 ring-red-200" },
  zone: { label: "Zone", className: "bg-amber-50 text-amber-700 ring-amber-200" },
  behavior: { label: "Behavior", className: "bg-orange-50 text-orange-700 ring-orange-200" },
};

interface ZoneMeta {
  id: number | null;
  name: string;
  color: string;
  total: number;
  severityCounts: SeverityCounts;
}

function zoneKey(id: number | null): string {
  return id === null ? "unassigned" : String(id);
}

function buildZoneMeta(zoneTotals: ZoneTotal[]): ZoneMeta[] {
  const real = [...zoneTotals]
    .filter((z) => z.zone_id !== null)
    .sort((a, b) => (a.zone_id as number) - (b.zone_id as number));
  const unassigned = zoneTotals.find((z) => z.zone_id === null);

  const metas: ZoneMeta[] = real.map((z, idx) => ({
    id: z.zone_id,
    name: z.zone_name,
    color: ZONE_COLOR_PALETTE[idx % ZONE_COLOR_PALETTE.length],
    total: z.total,
    severityCounts: z.severity_counts,
  }));

  if (unassigned) {
    metas.push({
      id: null,
      name: "Unassigned",
      color: UNASSIGNED_COLOR,
      total: unassigned.total,
      severityCounts: unassigned.severity_counts,
    });
  }
  return metas;
}

function timeAgo(iso: string, now: Date): string {
  const diff = Math.floor((now.getTime() - new Date(iso).getTime()) / 1000);
  if (diff < 5) return "just now";
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function ChartTooltip({
  active,
  payload,
  label,
  zoneLookup,
}: {
  active?: boolean;
  payload?: { dataKey: string | number; value: number; name: string; color: string }[];
  label?: string;
  zoneLookup?: Record<string, ZoneMeta>;
}) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="rounded-md border border-slate-200 bg-white px-3 py-2 font-mono text-[0.6875rem] shadow-md">
      <div className="mb-1 text-slate-400">{label}</div>
      {payload
        .slice()
        .reverse()
        .filter((p) => p.value > 0)
        .map((p) => {
          const zone = zoneLookup ? zoneLookup[String(p.dataKey)] : undefined;
          return (
            <div key={String(p.dataKey)} className="flex justify-between gap-4 text-slate-700">
              <span style={{ color: zone?.color ?? p.color }}>{zone?.name ?? p.name}</span>
              <span>{p.value}</span>
            </div>
          );
        })}
    </div>
  );
}

export function AnalyticsDashboard({
  embedded = false,
  isVisible = true,
}: {
  embedded?: boolean;
  isVisible?: boolean;
} = {}) {
  const [timeRange, setTimeRange] = useState<AnalyticsRangeParam>("7D");
  const [selectedZone, setSelectedZone] = useState<number | null>(null);
  const [comparisonMode, setComparisonMode] = useState<CompareMode>("week");

  const [freshKeys, setFreshKeys] = useState<Set<string>>(new Set());
  const [now, setNow] = useState(new Date());
  const [selectedIncident, setSelectedIncident] = useState<{ category: IncidentCategory; id: number } | null>(
    null,
  );
  const [exportOpen, setExportOpen] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);

  const queryClient = useQueryClient();

  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(t);
  }, []);

  // TanStack Query replaces four independent setInterval-polling useEffects
  // (PERF_PLAN.md Tier 3.2): same refetch cadence per panel, but now cached,
  // deduped across mounts, and each query tracks its own loading/error state
  // instead of hand-rolled cancelled/loading/error flags.
  // Summary comes from useSafetyKpis, shared with the Camera Feeds dashboard
  // so the two can't show different numbers for the same underlying data
  // again. This page no longer renders its own KPI row (DashboardShell's
  // top-of-page row is the only one now — this page's copy was a redundant
  // duplicate of it), but still needs `summary` for the charts/feed below.
  const { summary, error } = useSafetyKpis({
    range: timeRange,
    zoneId: selectedZone,
  });

  // Trend is intentionally always fetched unfiltered (zoneId=null): the chart
  // always plots every zone, dimming the non-selected ones client-side —
  // matching how the reference design keeps all zone areas visible while
  // filtering only affects emphasis, not what data is fetched.
  const trendQuery = useQuery({
    queryKey: ["analytics", "trend", timeRange],
    queryFn: () => getAnalyticsTrend(timeRange, null),
    refetchInterval: ANALYTICS_POLL_MS,
  });
  const trend = trendQuery.data ?? null;

  const compareQuery = useQuery({
    queryKey: ["analytics", "compare", comparisonMode, selectedZone],
    queryFn: () => getAnalyticsCompare(comparisonMode, selectedZone),
    refetchInterval: ANALYTICS_POLL_MS,
  });
  const compare = compareQuery.data ?? null;

  const feedQueryKey = ["analytics", "feed", FEED_LIMIT] as const;
  const feedQuery = useQuery({
    queryKey: feedQueryKey,
    queryFn: () => getUnifiedIncidents(FEED_LIMIT, null),
    refetchInterval: FEED_POLL_MS,
  });
  const feed = useMemo(() => feedQuery.data ?? [], [feedQuery.data]);

  // Diffs each new feed fetch against the previous one to flag newly-arrived
  // rows for the fade-in animation, clearing the flag after FRESH_ROW_MS. Kept
  // as a ref (not query state) since it's a pure animation trigger, not data.
  const prevFeedKeysRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    const data = feedQuery.data;
    if (!data) return;
    const keys = data.map((d) => `${d.category}-${d.id}`);
    const fresh = keys.filter((key) => !prevFeedKeysRef.current.has(key));
    prevFeedKeysRef.current = new Set(keys);
    if (fresh.length === 0) return;
    setFreshKeys((current) => new Set([...current, ...fresh]));
    const timer = window.setTimeout(() => {
      setFreshKeys((current) => {
        const next = new Set(current);
        fresh.forEach((key) => next.delete(key));
        return next;
      });
    }, FRESH_ROW_MS);
    return () => window.clearTimeout(timer);
  }, [feedQuery.data]);

  const zoneMeta = useMemo(() => buildZoneMeta(summary?.zone_totals ?? []), [summary]);
  const zoneLookup = useMemo(
    () => Object.fromEntries(zoneMeta.map((z) => [zoneKey(z.id), z])),
    [zoneMeta],
  );
  const selectedZoneMeta = selectedZone != null ? zoneLookup[zoneKey(selectedZone)] : undefined;
  const activeZoneKeys = useMemo(
    () => new Set((summary?.active_zone_ids ?? []).map(zoneKey)),
    [summary],
  );
  const filteredFeed = selectedZone != null ? feed.filter((f) => f.zone_id === selectedZone) : feed;

  // Zone Pulse geometry
  const centerX = 200;
  const centerY = 200;
  const orbitR = 125;
  const angleStep = zoneMeta.length > 0 ? (2 * Math.PI) / zoneMeta.length : 0;
  const maxZonePulseTotal = Math.max(...zoneMeta.map((z) => z.total), 1);
  const nodeRadius = (total: number) => 24 + (total / maxZonePulseTotal) * 24;
  const nodePositions = zoneMeta.map((z, i) => {
    const angle = -Math.PI / 2 + i * angleStep;
    return { ...z, x: centerX + orbitR * Math.cos(angle), y: centerY + orbitR * Math.sin(angle) };
  });
  const pulseGrandTotal = zoneMeta.reduce((s, z) => s + z.total, 0);

  const trendChartData = useMemo(() => {
    if (!trend) return [];
    return trend.points.map((p) => {
      const row: Record<string, string | number> = { date: p.date };
      zoneMeta.forEach((z) => {
        row[zoneKey(z.id)] = p.zone_totals[zoneKey(z.id)] ?? 0;
      });
      return row;
    });
  }, [trend, zoneMeta]);

  const severityPieData = summary
    ? (Object.keys(SEVERITY_TONE) as SeverityLevel[]).map((sev) => ({
        name: sev,
        value: summary.severity_counts[sev],
        color: SEVERITY_TONE[sev].hex,
      }))
    : [];
  const severityPieTotal = severityPieData.reduce((s, d) => s + d.value, 0);

  const typeCounts = summary?.type_counts ?? [];
  const maxType = Math.max(...typeCounts.map((r) => r.count), 1);

  const comparisonData = [...zoneMeta].sort((a, b) => b.total - a.total);
  const maxZoneComparisonTotal = Math.max(...comparisonData.map((z) => z.total), 1);

  const removeFromFeedCache = (category: IncidentCategory, id: number) => {
    queryClient.setQueryData<UnifiedIncident[]>(feedQueryKey, (current) =>
      current?.filter((f) => !(f.category === category && f.id === id)),
    );
  };

  const handleDeleteFromFeed = async (item: UnifiedIncident) => {
    if (!confirm(CONFIRM_DELETE_INCIDENT)) {
      return;
    }
    await deleteIncident(item.category, item.id);
    removeFromFeedCache(item.category, item.id);
  };

  const panel = "rounded-md border border-slate-200 bg-white shadow-sm";

  return (
    <div className="min-h-full bg-slate-100">
      <style>{`
        @keyframes pulseRing { 0% { transform: scale(0.9); opacity: 0.7; } 100% { transform: scale(1.6); opacity: 0; } }
        @keyframes blinkDot { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
        @keyframes fadeInRow { from { opacity: 0; } to { opacity: 1; } }
        .zone-node { cursor: pointer; transition: transform 0.15s ease-out, opacity 0.15s ease-out; }
        .zone-node:hover { transform: scale(1.05); }
        .live-dot { animation: blinkDot 1.6s ease-in-out infinite; }
        .feed-row-new { animation: fadeInRow 0.4s ease-out; }
        @media (prefers-reduced-motion: reduce) {
          .live-dot, .feed-row-new, .zone-node { animation: none !important; transition: none !important; }
        }
      `}</style>

      {embedded ? null : (
        <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-slate-800 bg-slate-950 px-4 text-white shadow-sm lg:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex size-10 shrink-0 items-center justify-center rounded-md bg-lime-200 text-green-950">
              <Factory className="size-5" aria-hidden="true" />
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold uppercase tracking-wide text-lime-200">De Heus LLC</p>
              <h1 className="truncate text-base font-semibold text-slate-50 sm:text-lg">
                Smart Factory Safety Monitoring
              </h1>
            </div>
          </div>
          <nav className="hidden h-full items-center gap-1 lg:flex" aria-label="Main">
            <Link
              href="/"
              className="flex h-full items-center gap-2 border-b-2 border-transparent px-4 text-xs font-semibold uppercase tracking-wide text-slate-400 transition hover:text-slate-100"
            >
              <CameraIcon className="size-4" aria-hidden="true" />
              Camera Feeds
            </Link>
            <Link
              href="/"
              className="flex h-full items-center gap-2 border-b-2 border-transparent px-4 text-xs font-semibold uppercase tracking-wide text-slate-400 transition hover:text-slate-100"
            >
              <ClipboardCheck className="size-4" aria-hidden="true" />
              Incident Log
            </Link>
            <span className="flex h-full items-center gap-2 border-b-2 border-lime-200 px-4 text-xs font-semibold uppercase tracking-wide text-lime-200">
              <Bell className="size-4" aria-hidden="true" />
              Incident Analytics
            </span>
          </nav>
          <div className="flex items-center gap-2">
            <div className="hidden items-center gap-1 md:flex">
              {[Bell, Siren, Factory].map((Icon, i) => (
                <button
                  key={i}
                  type="button"
                  aria-label="action"
                  className="inline-flex size-10 items-center justify-center rounded-md text-slate-300 transition hover:bg-white/10 hover:text-white"
                >
                  <Icon className="size-4" aria-hidden="true" />
                </button>
              ))}
            </div>
            <button
              type="button"
              className="flex items-center gap-2 rounded-md border border-slate-700 bg-slate-900 py-1.5 pl-1.5 pr-2 text-sm text-slate-100 transition hover:bg-slate-800"
            >
              <span className="flex size-7 items-center justify-center rounded bg-lime-200 text-xs font-bold text-green-950">
                DH
              </span>
              <ChevronDown className="size-4 text-slate-400" aria-hidden="true" />
            </button>
          </div>
        </header>
      )}

      <main className="min-w-0 flex-1 p-4 lg:p-6">
          {error ? (
            <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
          ) : null}

          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-slate-950">Incident Analytics</h2>
              <p className="text-xs text-slate-500">
                {selectedZoneMeta ? selectedZoneMeta.name : "All zones"} · updated{" "}
                {now.toLocaleTimeString("en-US", { hour12: false })}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={() => setExportOpen(true)}
                className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50"
              >
                <FileDown className="size-4" aria-hidden="true" />
                Export report
              </button>
              <button
                type="button"
                onClick={() => setScheduleOpen(true)}
                className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50"
              >
                <Calendar className="size-4" aria-hidden="true" />
                Schedule
              </button>
              <div className="inline-flex rounded-md border border-slate-200 bg-white p-1 shadow-sm">
                {(["24H", "7D", "30D"] as const).map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setTimeRange(t)}
                    className={`rounded px-3 py-1.5 font-mono text-xs font-semibold transition ${
                      timeRange === t ? "bg-slate-950 text-lime-200" : "text-slate-500 hover:bg-slate-100"
                    }`}
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="mb-5 grid grid-cols-1 gap-4 xl:grid-cols-3">
            <div className={`${panel} p-4 xl:col-span-2`}>
              <h3 className="text-sm font-semibold text-slate-950">Incidents by zone</h3>
              <p className="text-xs text-slate-500">Total incident volume per camera zone</p>
              <div style={{ height: 340 }} className="mt-2">
                <svg viewBox="0 0 400 400" style={{ width: "100%", height: "100%" }}>
                  {nodePositions.map((z) => (
                    <line
                      key={`line-${zoneKey(z.id)}`}
                      x1={centerX}
                      y1={centerY}
                      x2={z.x}
                      y2={z.y}
                      stroke={selectedZone === z.id ? z.color : "#e2e8f0"}
                      strokeWidth={selectedZone === z.id ? 1.5 : 1}
                      opacity={selectedZone != null && selectedZone !== z.id ? 0.3 : 0.8}
                    />
                  ))}
                  <g
                    className="cursor-pointer transition hover:opacity-80"
                    onClick={() => setSelectedZone(null)}
                  >
                    <circle
                      cx={centerX}
                      cy={centerY}
                      r={50}
                      fill="#f8fafc"
                      stroke={selectedZone === null ? "#0f172a" : "#e2e8f0"}
                      strokeWidth={selectedZone === null ? 2.5 : 1.5}
                    />
                    <text
                      x={centerX}
                      y={centerY - 4}
                      textAnchor="middle"
                      fill="#0f172a"
                      className="font-mono"
                      style={{ fontSize: 22, fontWeight: 700 }}
                    >
                      {pulseGrandTotal}
                    </text>
                    <text
                      x={centerX}
                      y={centerY + 15}
                      textAnchor="middle"
                      fill={selectedZone === null ? "#0f172a" : "#94a3b8"}
                      className="font-mono"
                      style={{ fontSize: 9, letterSpacing: "0.06em", fontWeight: selectedZone === null ? 700 : 400 }}
                    >
                      ALL ZONES
                    </text>
                  </g>

                  {nodePositions.map((z) => {
                    const r = nodeRadius(z.total);
                    const isActive = activeZoneKeys.has(zoneKey(z.id));
                    const isSelected = selectedZone === z.id;
                    const dim = selectedZone != null && !isSelected;
                    return (
                      <g
                        key={zoneKey(z.id)}
                        className="zone-node"
                        onClick={() => setSelectedZone(isSelected ? null : z.id)}
                        opacity={dim ? 0.3 : 1}
                      >
                        {isActive && (
                          <circle
                            cx={z.x}
                            cy={z.y}
                            r={r}
                            fill="none"
                            stroke={z.color}
                            strokeWidth={1.5}
                            style={{ animation: "pulseRing 1.8s ease-out infinite", transformOrigin: `${z.x}px ${z.y}px` }}
                          />
                        )}
                        <circle cx={z.x} cy={z.y} r={r} fill={z.color} stroke={isSelected ? "#0f172a" : "white"} strokeWidth={isSelected ? 2 : 1.5} />
                        <text
                          x={z.x}
                          y={z.y + 4}
                          textAnchor="middle"
                          fill="#0f172a"
                          stroke="white"
                          strokeWidth={3}
                          paintOrder="stroke"
                          className="font-mono"
                          style={{ fontWeight: 700, fontSize: 11 }}
                        >
                          {z.total}
                        </text>
                        <text
                          x={z.x}
                          y={z.y + r + 14}
                          textAnchor="middle"
                          fill={dim ? "#cbd5e1" : "#64748b"}
                          style={{ fontSize: 9 }}
                        >
                          {z.name}
                        </text>
                      </g>
                    );
                  })}
                </svg>
              </div>
            </div>

            <div className={`${panel} flex flex-col p-4`} style={{ height: 480 }}>
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-base font-semibold text-slate-950">Live Feed</h3>
                  <p className="text-xs text-slate-500">{selectedZoneMeta ? selectedZoneMeta.name : "All zones"}</p>
                </div>
                <span className="inline-flex items-center gap-1.5 rounded bg-red-50 px-2.5 py-1.5 text-xs font-semibold text-red-700 ring-1 ring-red-200">
                  <span className="live-dot size-2 rounded-full bg-red-600" />
                  LIVE
                </span>
              </div>
              <div className="mt-2 flex-1 overflow-y-auto">
                {filteredFeed.length === 0 && (
                  <div className="p-4 text-sm text-slate-400">No incidents for this zone yet.</div>
                )}
                {filteredFeed.map((item) => {
                  const zone = zoneLookup[zoneKey(item.zone_id)];
                  const badge = CATEGORY_BADGE[item.category];
                  const key = `${item.category}-${item.id}`;
                  return (
                    <div
                      key={key}
                      onClick={() => setSelectedIncident({ category: item.category, id: item.id })}
                      className={`flex cursor-pointer items-center gap-3 border-b border-slate-100 py-3.5 transition hover:bg-slate-50 ${freshKeys.has(key) ? "feed-row-new" : ""}`}
                    >
                      <span
                        className="size-3 shrink-0 rounded-full"
                        style={{ background: zone?.color ?? UNASSIGNED_COLOR }}
                      />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-base font-semibold text-slate-900">{item.type}</p>
                        <p className="truncate font-mono text-xs text-slate-500">
                          {item.zone_name} · {item.camera_label} · {timeAgo(item.timestamp, now)}
                        </p>
                      </div>
                      <span className={`shrink-0 rounded px-2 py-1 text-xs font-semibold ring-1 ${badge.className}`}>
                        {badge.label}
                      </span>
                      <span
                        className={`shrink-0 rounded px-2 py-1 text-xs font-semibold ring-1 ${SEVERITY_TONE[item.severity].bg} ${SEVERITY_TONE[item.severity].text} ${SEVERITY_TONE[item.severity].ring}`}
                      >
                        {item.severity}
                      </span>
                      <button
                        type="button"
                        aria-label="Delete incident"
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleDeleteFromFeed(item);
                        }}
                        className="shrink-0 rounded p-1.5 text-slate-300 transition hover:bg-red-50 hover:text-red-600"
                      >
                        <Trash2 className="size-4" aria-hidden="true" />
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          <div className={`${panel} mb-5 p-4`}>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-950">Incident Volume Trend</h3>
                <p className="text-xs text-slate-500">Stacked by zone · click a legend chip to filter</p>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={() => setSelectedZone(null)}
                  className="flex items-center gap-1.5 px-2 py-1 rounded text-xs font-semibold text-slate-600 hover:bg-slate-100 transition cursor-pointer"
                  style={{ opacity: selectedZone === null ? 1 : 0.35 }}
                >
                  <span className="size-2 rounded-full bg-slate-400" />
                  <span>All Zones</span>
                </button>
                {zoneMeta.map((z) => (
                  <button
                    key={zoneKey(z.id)}
                    type="button"
                    onClick={() => setSelectedZone(selectedZone === z.id ? null : z.id)}
                    className="flex items-center gap-1.5 px-2 py-1 rounded text-xs font-semibold text-slate-600 hover:bg-slate-100 transition cursor-pointer"
                    style={{ opacity: selectedZone != null && selectedZone !== z.id ? 0.35 : 1 }}
                  >
                    <span className="size-2 rounded-full" style={{ background: z.color }} />
                    <span>{z.name}</span>
                  </button>
                ))}
              </div>
            </div>
            <div style={{ height: 270 }} className="mt-2">
              {isVisible ? (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={trendChartData} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
                  <defs>
                    {zoneMeta.map((z) => (
                      <linearGradient key={zoneKey(z.id)} id={`grad-${zoneKey(z.id)}`} x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={z.color} stopOpacity={0.55} />
                        <stop offset="100%" stopColor={z.color} stopOpacity={0.03} />
                      </linearGradient>
                    ))}
                  </defs>
                  <CartesianGrid vertical={false} stroke="#e2e8f0" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="date"
                    tick={{ fill: "#94a3b8", fontFamily: "var(--font-geist-mono)", fontSize: 10 }}
                    axisLine={{ stroke: "#e2e8f0" }}
                    tickLine={false}
                    interval="preserveStartEnd"
                  />
                  <YAxis
                    tick={{ fill: "#94a3b8", fontFamily: "var(--font-geist-mono)", fontSize: 10 }}
                    axisLine={false}
                    tickLine={false}
                    width={30}
                  />
                  <Tooltip content={<ChartTooltip zoneLookup={zoneLookup} />} />
                  {zoneMeta.map((z) => (
                    <Area
                      key={zoneKey(z.id)}
                      type="monotone"
                      dataKey={zoneKey(z.id)}
                      stackId="1"
                      stroke={z.color}
                      strokeWidth={selectedZone === z.id ? 2 : 1.25}
                      fill={`url(#grad-${zoneKey(z.id)})`}
                      opacity={selectedZone != null && selectedZone !== z.id ? 0.15 : 1}
                    />
                  ))}
                  </AreaChart>
                </ResponsiveContainer>
              ) : null}
            </div>
          </div>

          <div className={`${panel} mb-5 p-4`}>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-950">Period Comparison</h3>
                <p className="text-xs text-slate-500">
                  {selectedZoneMeta ? selectedZoneMeta.name : "All zones"} · this period vs the one before it
                </p>
              </div>
              <div className="inline-flex rounded-md border border-slate-200 bg-white p-1 shadow-sm">
                {([
                  { id: "week", label: "Week over Week" },
                  { id: "month", label: "Month over Month" },
                ] as const).map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => setComparisonMode(m.id)}
                    className={`rounded px-3 py-1.5 text-xs font-semibold transition ${
                      comparisonMode === m.id ? "bg-slate-950 text-lime-200" : "text-slate-500 hover:bg-slate-100"
                    }`}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="mt-4 grid grid-cols-1 gap-5 lg:grid-cols-3">
              <div className="flex flex-col justify-center gap-3 lg:col-span-1">
                <div>
                  <p className="text-xs font-medium text-slate-500">
                    {comparisonMode === "week" ? "This week" : "This month"}
                  </p>
                  <p className="font-mono text-3xl font-semibold text-slate-950">{compare?.current_total ?? 0}</p>
                </div>
                <div>
                  <p className="text-xs font-medium text-slate-500">
                    {comparisonMode === "week" ? "Prior week" : "Prior month"}
                  </p>
                  <p className="font-mono text-lg font-semibold text-slate-500">{compare?.prior_total ?? 0}</p>
                </div>
                {compare ? (
                  <div
                    className={`inline-flex w-fit items-center gap-1.5 rounded px-2 py-1 text-xs font-semibold ring-1 ${
                      compare.delta_pct > 0
                        ? "bg-red-50 text-red-700 ring-red-200"
                        : compare.delta_pct < 0
                          ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
                          : "bg-slate-100 text-slate-700 ring-slate-200"
                    }`}
                  >
                    {compare.delta_pct > 0 ? (
                      <TrendingUp className="size-3.5" />
                    ) : compare.delta_pct < 0 ? (
                      <TrendingDown className="size-3.5" />
                    ) : (
                      <Minus className="size-3.5" />
                    )}
                    {compare.delta_pct > 0 ? "+" : ""}
                    {compare.delta_pct}% vs prior {comparisonMode}
                  </div>
                ) : null}
                <div className="mt-2 flex flex-col gap-1.5">
                  {(Object.keys(SEVERITY_TONE) as SeverityLevel[]).map((sev) => {
                    const delta = compare?.severity_breakdown.find((s) => s.severity === sev);
                    return (
                      <div key={sev} className="flex items-center justify-between text-xs">
                        <span className="flex items-center gap-1.5 text-slate-500">
                          <span className="size-1.5 rounded-full" style={{ background: SEVERITY_TONE[sev].hex }} />
                          {sev}
                        </span>
                        <span className="font-mono text-slate-400">
                          {delta?.prior ?? 0} → {delta?.current ?? 0}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="lg:col-span-2">
                <div className="mb-2 flex items-center gap-4 text-xs text-slate-500">
                  <span className="flex items-center gap-1.5">
                    <span className="h-0.5 w-4 rounded bg-slate-950" />
                    Current {comparisonMode}
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="h-0.5 w-4 rounded" style={{ borderTop: "2px dashed #cbd5e1" }} />
                    Prior {comparisonMode}
                  </span>
                </div>
                <div style={{ height: 200 }}>
                  {isVisible ? (
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={compare?.points ?? []} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                      <CartesianGrid vertical={false} stroke="#e2e8f0" strokeDasharray="3 3" />
                      <XAxis
                        dataKey="label"
                        tick={{ fill: "#94a3b8", fontFamily: "var(--font-geist-mono)", fontSize: 10 }}
                        axisLine={{ stroke: "#e2e8f0" }}
                        tickLine={false}
                      />
                      <YAxis
                        tick={{ fill: "#94a3b8", fontFamily: "var(--font-geist-mono)", fontSize: 10 }}
                        axisLine={false}
                        tickLine={false}
                        width={30}
                      />
                      <Tooltip content={<ChartTooltip />} />
                      <Line type="monotone" dataKey="prior" name="Prior" stroke="#cbd5e1" strokeWidth={2} strokeDasharray="4 4" dot={false} />
                      <Line type="monotone" dataKey="current" name="Current" stroke="#0f172a" strokeWidth={2.5} dot={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  ) : null}
                </div>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <div className={`${panel} p-4`}>
              <h3 className="text-sm font-semibold text-slate-950">Severity &amp; Type Breakdown</h3>
              <p className="text-xs text-slate-500">
                {selectedZoneMeta ? selectedZoneMeta.name : "All zones"} · {timeRange}
              </p>

              <div className="mt-3 flex flex-wrap items-center gap-5">
                <div className="relative size-36 shrink-0">
                  {isVisible ? (
                    <ResponsiveContainer width="100%" height="100%">
                      <PieChart>
                      <Pie data={severityPieData} dataKey="value" nameKey="name" innerRadius="62%" outerRadius="92%" paddingAngle={3} stroke="none">
                        {severityPieData.map((s) => (
                          <Cell key={s.name} fill={s.color} />
                        ))}
                      </Pie>
                      <Tooltip
                        content={<ChartTooltip />}
                        position={{ x: 115, y: 40 }}
                        allowEscapeViewBox={{ x: true, y: true }}
                        wrapperStyle={{ zIndex: 10 }}
                      />
                      </PieChart>
                    </ResponsiveContainer>
                  ) : null}
                  <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                    <div className="font-mono text-xl font-semibold text-slate-950">{severityPieTotal}</div>
                    <div className="font-mono text-[0.625rem] text-slate-400">INCIDENTS</div>
                  </div>
                </div>
                <div className="flex min-w-[140px] flex-1 flex-col gap-2">
                  {severityPieData.map((s) => (
                    <div key={s.name} className="flex items-center justify-between text-sm">
                      <span className="flex items-center gap-2 text-slate-500">
                        <span className="size-2 rounded-full" style={{ background: s.color }} />
                        {s.name}
                      </span>
                      <span className="font-mono font-semibold text-slate-900">{s.value}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="mt-5 flex flex-col gap-2.5">
                {typeCounts.map((row) => (
                  <div key={`${row.category}-${row.type}`}>
                    <div className="mb-1 flex items-center justify-between gap-2">
                      <span className="flex min-w-0 items-center gap-1.5 text-xs text-slate-600">
                        <span className={`shrink-0 rounded px-1 py-0.5 text-[0.5625rem] font-semibold ring-1 ${CATEGORY_BADGE[row.category].className}`}>
                          {CATEGORY_BADGE[row.category].label}
                        </span>
                        <span className="truncate">{row.type}</span>
                      </span>
                      <span className="shrink-0 font-mono text-xs text-slate-900">{row.count}</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
                      <div className="h-full rounded-full bg-slate-400" style={{ width: `${(row.count / maxType) * 100}%` }} />
                    </div>
                  </div>
                ))}
                {typeCounts.length === 0 ? <p className="text-xs text-slate-400">No incidents in this range.</p> : null}
              </div>
            </div>

            <div className={`${panel} p-4`}>
              <h3 className="text-sm font-semibold text-slate-950">Zone Comparison</h3>
              <p className="text-xs text-slate-500">Ranked by total incidents · {timeRange} · click to filter</p>
              <div className="mt-4 flex flex-col gap-4">
                {comparisonData.map((z) => (
                  <div
                    key={zoneKey(z.id)}
                    onClick={() => setSelectedZone(selectedZone === z.id ? null : z.id)}
                    className="cursor-pointer"
                    style={{ opacity: selectedZone != null && selectedZone !== z.id ? 0.4 : 1 }}
                  >
                    <div className="mb-1.5 flex items-center justify-between">
                      <span className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                        <span className="size-2 rounded-full" style={{ background: z.color }} />
                        {z.name}
                      </span>
                      <span className="font-mono text-xs text-slate-500">{z.total}</span>
                    </div>
                    <div
                      className="flex h-2 overflow-hidden rounded-full bg-slate-100"
                      style={{ width: `${(z.total / maxZoneComparisonTotal) * 100}%` }}
                    >
                      {(Object.keys(SEVERITY_TONE) as SeverityLevel[]).map((sev) => {
                        const value = z.severityCounts[sev];
                        if (value === 0 || z.total === 0) return null;
                        return (
                          <div
                            key={sev}
                            title={`${sev}: ${value}`}
                            style={{ width: `${(value / z.total) * 100}%`, background: SEVERITY_TONE[sev].hex }}
                          />
                        );
                      })}
                    </div>
                  </div>
                ))}
                {comparisonData.length === 0 ? <p className="text-xs text-slate-400">No zones configured yet.</p> : null}
              </div>
            </div>
          </div>
      </main>

      {selectedIncident ? (
        <IncidentDetailModal
          key={`${selectedIncident.category}-${selectedIncident.id}`}
          category={selectedIncident.category}
          incidentId={selectedIncident.id}
          onClose={() => setSelectedIncident(null)}
          onDeleted={() => removeFromFeedCache(selectedIncident.category, selectedIncident.id)}
        />
      ) : null}

      <ReportExportDialog
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        range={timeRange}
        zoneId={selectedZone}
      />
      <ScheduleReportDialog open={scheduleOpen} onClose={() => setScheduleOpen(false)} />
    </div>
  );
}
