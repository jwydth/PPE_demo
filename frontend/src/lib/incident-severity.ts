/** Client-side mirror of backend/app/services/incident_normalization.py.
 *
 * Severity isn't a single stored column across the three incident tables, so
 * each category derives it differently. Keeping one copy of these rules (used
 * by both the incident card badges and the incident detail modal) avoids the
 * three call sites drifting out of sync with each other or with the backend
 * predicates in incident_filters.py that actually do the filtering.
 */

import { BehaviorIncident } from "@/types/behavior";
import { ViolationReport } from "@/types/detection";
import { ZoneViolation } from "@/types/zone";

export const VALID_SEVERITIES = ["Critical", "High", "Medium", "Low"] as const;
export type Severity = (typeof VALID_SEVERITIES)[number];

function titleCase(raw: string): string {
  const trimmed = raw.trim();
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1).toLowerCase();
}

function asSeverity(titled: string, fallback: Severity): Severity {
  return (VALID_SEVERITIES as readonly string[]).includes(titled)
    ? (titled as Severity)
    : fallback;
}

/** PPE has no stored severity: multiple missing items -> High, a single
 * missing item -> Medium, a proximity violation -> Low. Never Critical. */
export function getPpeSeverity(violationType: string): Severity {
  const normalized = violationType.trim().toLowerCase();
  if (normalized.includes("proximity")) return "Low";
  if (normalized.includes("_and_") || normalized.includes(" and ")) return "High";
  return "Medium";
}

export function getZoneSeverity(raw: string | null | undefined): Severity {
  if (!raw) return "Medium";
  return asSeverity(titleCase(raw), "Medium");
}

export function getBehaviorSeverity(raw: string | null | undefined): Severity {
  if (!raw) return "High";
  return asSeverity(titleCase(raw), "High");
}

export function getEventSeverity(
  event: ViolationReport | ZoneViolation | BehaviorIncident,
): Severity {
  if ("violation_type" in event) return getPpeSeverity(event.violation_type);
  if ("behavior_type" in event) return getBehaviorSeverity(event.severity);
  return getZoneSeverity(event.severity);
}

export const SEVERITY_BADGE_CLASS: Record<Severity, string> = {
  Critical: "bg-red-50 text-red-700 ring-red-200",
  High: "bg-orange-50 text-orange-700 ring-orange-200",
  Medium: "bg-amber-50 text-amber-700 ring-amber-200",
  Low: "bg-slate-100 text-slate-600 ring-slate-200",
};

/** Solid fill for a *selected* severity filter chip — deliberately higher
 * contrast than SEVERITY_BADGE_CLASS's soft badge, so an active filter reads
 * as "on" without relying on the neutral slate/lime treatment used for
 * non-semantic filters (type, time). Text colors are picked per-swatch to
 * clear 4.5:1 (white on Critical/High/Low, dark on the lighter Medium amber). */
export const SEVERITY_ACTIVE_CLASS: Record<Severity, string> = {
  Critical: "bg-red-600 text-white ring-red-600",
  High: "bg-orange-700 text-white ring-orange-700",
  Medium: "bg-amber-500 text-slate-900 ring-amber-500",
  Low: "bg-slate-600 text-white ring-slate-600",
};

/** Small color dot shown on an *unselected* severity chip, so the
 * severity -> color mapping is visible before the user picks anything. */
export const SEVERITY_DOT_CLASS: Record<Severity, string> = {
  Critical: "bg-red-500",
  High: "bg-orange-500",
  Medium: "bg-amber-500",
  Low: "bg-slate-400",
};
