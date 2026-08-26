import {
  BarChart3,
  Bell,
  Camera,
  ClipboardCheck,
  Factory,
  Siren,
  type LucideIcon,
} from "lucide-react";

export type SafetyMetric = {
  label: string;
  value: string;
  helper: string;
  trend: string;
  icon: LucideIcon;
  tone: "green" | "amber" | "blue" | "slate";
  /** The value isn't known yet (still loading, or the request failed).
   * MetricCard renders a placeholder instead of `value` — a safety readout
   * must never show a real-looking number it hasn't actually got. */
  pending?: boolean;
};

// 3D Map ("factory3d" view) is temporarily hidden from navigation — the
// route and component are still in place, just not linked from the top bar.
export const navigation = [
  { label: "Camera Feeds", active: true, icon: Camera },
  { label: "Live Incident Panel", active: false, icon: ClipboardCheck },
  { label: "Analytics", active: false, icon: BarChart3 },
];

export const appActions = [
  { label: "Notifications", icon: Bell },
  { label: "Emergency escalation", icon: Siren },
  { label: "Factory settings", icon: Factory },
];
