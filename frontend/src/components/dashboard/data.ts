import {
  Bell,
  Camera,
  ClipboardCheck,
  Clock3,
  Factory,
  Gauge,
  HardHat,
  Siren,
  Thermometer,
  Users,
  Video,
  type LucideIcon,
} from "lucide-react";

export type Zone = {
  name: string;
  cameraCount: number;
  status: "online" | "warning" | "standby";
};

export type SafetyMetric = {
  label: string;
  value: string;
  helper: string;
  trend: string;
  icon: LucideIcon;
  tone: "green" | "amber" | "blue" | "slate";
};

export type Incident = {
  title: string;
  zone: string;
  time: string;
  severity: "low" | "medium" | "high";
};

export const zones: Zone[] = [
  { name: "Warehouse Intake", cameraCount: 8, status: "online" },
  { name: "Packaging Line 1", cameraCount: 12, status: "online" },
  { name: "Production Floor", cameraCount: 16, status: "warning" },
  { name: "Forklift Loading Zone", cameraCount: 10, status: "online" },
  { name: "Gate 3", cameraCount: 6, status: "standby" },
];

export const safetyMetrics: SafetyMetric[] = [
  {
    label: "PPE Compliance",
    value: "98.2%",
    helper: "Helmets and high-vis detected",
    trend: "+2.4% today",
    icon: HardHat,
    tone: "green",
  },
  {
    label: "Open Alerts",
    value: "4",
    helper: "2 medium, 2 low priority",
    trend: "-6 vs yesterday",
    icon: Bell,
    tone: "amber",
  },
  {
    label: "Active Cameras",
    value: "52/54",
    helper: "30 FPS median stream rate",
    trend: "96ms edge latency",
    icon: Video,
    tone: "blue",
  },
  {
    label: "People On Shift",
    value: "143",
    helper: "Across monitored zones",
    trend: "Stable occupancy",
    icon: Users,
    tone: "slate",
  },
];

export const incidents: Incident[] = [
  {
    title: "Vest not detected near palletizer",
    zone: "Packaging Line 1",
    time: "2 min ago",
    severity: "medium",
  },
  {
    title: "Forklift boundary briefly crossed",
    zone: "Loading Zone",
    time: "11 min ago",
    severity: "high",
  },
  {
    title: "Camera 07 calibration drift",
    zone: "Production Floor",
    time: "24 min ago",
    severity: "low",
  },
];

export const cameraStats = [
  { label: "Status", value: "LIVE", icon: Camera },
  { label: "FPS", value: "30", icon: Gauge },
  { label: "Latency", value: "1.2s", icon: Clock3 },
  { label: "Zone temp", value: "27.4C", icon: Thermometer },
];

export const navigation = [
  { label: "Camera Feeds", active: true, icon: Camera },
  { label: "Incident Log", active: false, icon: ClipboardCheck },
];

export const appActions = [
  { label: "Notifications", icon: Bell },
  { label: "Emergency escalation", icon: Siren },
  { label: "Factory settings", icon: Factory },
];
