"use client";

import dynamic from "next/dynamic";

const AnalyticsDashboard = dynamic(
  () => import("@/components/analytics/analytics-dashboard").then((m) => m.AnalyticsDashboard),
  { ssr: false },
);

export default function AnalyticsPage() {
  return <AnalyticsDashboard />;
}
