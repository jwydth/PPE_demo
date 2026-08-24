"use client";

import { HardHat, Trash2 } from "lucide-react";
import { getEventSeverity, SEVERITY_BADGE_CLASS } from "@/lib/incident-severity";
import { BehaviorIncident } from "@/types/behavior";
import { DetectionResponse, PersonResult, ViolationReport } from "@/types/detection";
import { ZoneViolation } from "@/types/zone";

export function DetectionSummary({ result }: { result: DetectionResponse }) {
  const { summary } = result;
  return (
    <div className="grid gap-3 sm:grid-cols-4">
      <Metric label="Workers" value={summary.total_persons} />
      <Metric label="Compliant" value={summary.compliant} tone="green" />
      <Metric label="Violations" value={summary.violations} tone={summary.violations > 0 ? "red" : "green"} />
      <Metric label="Inference" value={`${summary.inference_ms.toFixed(0)}ms`} />
    </div>
  );
}

export function PeopleResults({ persons }: { persons: PersonResult[] }) {
  if (persons.length === 0) {
    return <EmptyState text="No workers were detected in this frame." />;
  }

  return (
    <div className="grid gap-3">
      {persons.map((person) => (
        <article key={`${person.person_id}-${person.track_id ?? "single"}`} className="rounded-md border border-slate-200 bg-white p-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-slate-950">
                {formatPersonRole(person)} {person.person_id}
              </p>
              <p className="text-xs text-slate-500">Confidence {Math.round(person.confidence * 100)}%</p>
            </div>
            <span className={`rounded px-2 py-1 text-xs font-semibold ring-1 ${person.compliant ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : "bg-red-50 text-red-700 ring-red-200"}`}>
              {person.compliant ? "Compliant" : "Violation"}
            </span>
          </div>
          <div className="mt-3 grid gap-2">
            {person.equipment.map((item) => (
              <div key={item.label} className="flex items-center justify-between rounded bg-slate-50 px-3 py-2 text-sm">
                <span className="flex items-center gap-2 text-slate-700">
                  <HardHat className="size-4 text-green-900" aria-hidden="true" />
                  {formatEquipmentLabel(item.label)}
                </span>
                <span className={item.status === "compliant" ? "text-emerald-700" : "text-red-700"}>
                  {item.status}
                </span>
              </div>
            ))}
          </div>
        </article>
      ))}
    </div>
  );
}

export function IncidentCard({
  event,
  onDelete,
  onOpenDetail,
  compact = false,
  cameraName,
  showMetadata = true,
}: {
  event: ViolationReport | ZoneViolation | BehaviorIncident;
  onDelete?: () => void;
  onOpenDetail?: (category: "ppe" | "zone" | "behavior", id: number) => void;
  compact?: boolean;
  cameraName?: string;
  showMetadata?: boolean;
}) {
  const isPpe = isPpeIncident(event);
  const isBehavior = isBehaviorIncident(event);
  const category = isPpe ? "ppe" : isBehavior ? "behavior" : "zone";
  const imageUrl = isPpe
    ? event.snapshot_url
    : isBehavior
      ? event.snapshot_url ?? event.evidence[0]?.file_url ?? undefined
      : event.snapshot_path;
  const title = isPpe
    ? formatIncidentType(event.violation_type)
    : isBehavior
      ? formatBehaviorType(event.behavior_type)
      : formatZoneType(event.zone_type, event.zone_name);
  const sourceLabel = isPpe ? "PPE" : isBehavior ? "Behavior" : "Zone";
  const categoryLabel = isBehavior ? "Safety Incident" : "Violation";
  const categoryClass = isBehavior
    ? "bg-orange-50 text-orange-700 ring-orange-200"
    : "bg-red-50 text-red-700 ring-red-200";
  const badgeClass = isPpe
    ? "bg-red-50 text-red-700 ring-red-200"
    : isBehavior
      ? "bg-orange-50 text-orange-700 ring-orange-200"
      : "bg-amber-50 text-amber-700 ring-amber-200";
  const frameLabel = isBehavior ? event.frame_start : event.frame_index;
  const severity = getEventSeverity(event);

  const canOpenDetail = Boolean(onOpenDetail && event.id != null);

  return (
    <article
      className={`overflow-hidden rounded-md border border-slate-200 bg-white shadow-sm ${canOpenDetail ? "cursor-pointer transition hover:border-slate-300 hover:shadow-md" : ""}`}
      onClick={canOpenDetail ? () => onOpenDetail!(category, event.id as number) : undefined}
    >
      {imageUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={imageUrl}
          alt="Incident evidence"
          className={`${compact ? "max-h-32" : "aspect-video"} w-full object-cover`}
        />
      ) : null}
      <div className={compact ? "p-2" : "p-3"}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className={`${compact ? "text-xs" : "text-sm"} font-semibold text-slate-950`}>
              {title}
            </p>
            <p className="mt-1 text-xs text-slate-500">{formatTime(event.timestamp)}</p>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1">
            <span className={`rounded px-2 py-1 text-xs font-semibold ring-1 ${categoryClass}`}>
              {categoryLabel}
            </span>
            <span className={`rounded px-2 py-1 text-xs font-semibold ring-1 ${badgeClass}`}>
              {sourceLabel}
            </span>
            <span className={`rounded px-2 py-1 text-xs font-semibold ring-1 ${SEVERITY_BADGE_CLASS[severity]}`}>
              {severity}
            </span>
          </div>
        </div>
        {showMetadata ? (
          <div className={`${compact ? "mt-2 gap-1 pt-2" : "mt-3 gap-2 pt-3"} grid grid-cols-2 border-t border-slate-100 text-xs text-slate-600`}>
            <span className="truncate">Video: {cameraName ?? event.video_name ?? "-"}</span>
            <span>Frame: {frameLabel ?? "-"}</span>
            <span>Track: {event.track_id ?? "-"}</span>
            <span>ID: #{event.id ?? "-"}</span>
          </div>
        ) : null}
        {onDelete ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
            className="mt-3 inline-flex items-center gap-2 rounded-md border border-red-200 px-3 py-2 text-xs font-semibold text-red-700 transition hover:bg-red-50"
          >
            <Trash2 className="size-3.5" aria-hidden="true" />
            Delete
          </button>
        ) : null}
      </div>
    </article>
  );
}

export function EmptyState({ text }: { text: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
      {text}
    </div>
  );
}

export function LoadingState({ text }: { text: string }) {
  return (
    <div className="rounded-md border border-lime-200 bg-lime-50 p-4 text-sm font-medium text-green-900">
      {text}
    </div>
  );
}

export function ErrorState({ text }: { text: string }) {
  return (
    <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm font-medium text-red-700">
      {text}
    </div>
  );
}

function Metric({
  label,
  value,
  tone = "slate",
}: {
  label: string;
  value: string | number;
  tone?: "green" | "red" | "slate";
}) {
  const colors = {
    green: "text-emerald-700",
    red: "text-red-700",
    slate: "text-slate-950",
  };

  return (
    <div className="rounded-md border border-slate-200 bg-white p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-1 text-xl font-semibold ${colors[tone]}`}>{value}</p>
    </div>
  );
}

function formatIncidentType(type: string): string {
  const labels: Record<string, string> = {
    missing_helmet: "Missing Safety Helmet",
    missing_vest: "Missing Safety Vest",
    missing_helmet_and_vest: "Missing Helmet and Vest",
    missing_cleaning_coverall: "Missing Cleaning Coverall",
    missing_helmet_and_cleaning_coverall: "Missing Helmet and Cleaning Coverall",
    missing_role_uniform: "Missing Role Uniform",
    missing_helmet_and_role_uniform: "Missing Helmet and Role Uniform",
    proximity_violation: "Proximity Violation",
    zone_incursion: "Zone Incursion",
  };
  return labels[type] ?? type.replaceAll("_", " ");
}

function formatBehaviorType(type: string): string {
  const labels: Record<string, string> = {
    FALL_DETECTED: "Fall Detected",
    RUNNING_DETECTED: "Running Detected",
    FAINT_DETECTED: "Faint Detected",
    COLLAPSE_DETECTED: "Collapse Detected",
  };
  return labels[type] ?? type.replaceAll("_", " ");
}

function isPpeIncident(event: ViolationReport | ZoneViolation | BehaviorIncident): event is ViolationReport {
  return "violation_type" in event;
}

function isBehaviorIncident(event: ViolationReport | ZoneViolation | BehaviorIncident): event is BehaviorIncident {
  return "behavior_type" in event;
}

function formatPersonRole(person: PersonResult): string {
  if (person.role === "worker") return "Worker";
  if (person.role === "janitor") return "Janitor";
  return "Unknown role";
}

function formatEquipmentLabel(label: string): string {
  if (label === "Role Uniform") return "Role Uniform (Vest or Cleaning Coverall)";
  return label;
}

function formatZoneType(type?: string, name?: string): string {
  if (type === "WALKWAY") return `Left Walkway${name ? `: ${name}` : ""}`;
  if (type === "SLIPPERY") return `Slippery Area${name ? `: ${name}` : ""}`;
  return `Restricted Zone Incursion${name ? `: ${name}` : ""}`;
}

function formatTime(timestamp: string): string {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleString();
}
