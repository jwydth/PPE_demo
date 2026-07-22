import {
  BarChart3,
  Bell,
  Camera,
  ClipboardCheck,
  Factory,
  HardHat,
  Siren,
  Users,
  Video,
  type LucideIcon,
} from "lucide-react";

export type SafetyMetric = {
  label: string;
  value: string;
  helper: string;
  trend: string;
  icon: LucideIcon;
  tone: "green" | "amber" | "blue" | "slate";
};

// DEMO DATA — this page is not wired to a metrics backend yet. Values are
// intentionally round placeholders, not a real read of the floor.
export const safetyMetrics: SafetyMetric[] = [
  {
    label: "PPE Compliance",
    value: "—",
    helper: "Helmets and high-vis detected",
    trend: "Not yet tracked",
    icon: HardHat,
    tone: "slate",
  },
  {
    label: "Open Alerts",
    value: "—",
    helper: "Medium and low priority",
    trend: "Not yet tracked",
    icon: Bell,
    tone: "slate",
  },
  {
    label: "Active Cameras",
    value: "—",
    helper: "Median stream rate",
    trend: "Not yet tracked",
    icon: Video,
    tone: "slate",
  },
  {
    label: "People On Shift",
    value: "—",
    helper: "Across monitored zones",
    trend: "Not yet tracked",
    icon: Users,
    tone: "slate",
  },
];

// 3D Map ("factory3d" view) is temporarily hidden from navigation — the
// route and component are still in place, just not linked from the top bar.
export const navigation = [
  { label: "Camera Feeds", active: true, icon: Camera },
  { label: "Incident Log", active: false, icon: ClipboardCheck },
  { label: "Analytics", active: false, icon: BarChart3 },
];

export const appActions = [
  { label: "Notifications", icon: Bell },
  { label: "Emergency escalation", icon: Siren },
  { label: "Factory settings", icon: Factory },
];
