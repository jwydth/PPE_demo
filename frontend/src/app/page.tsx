import { Suspense } from "react";
import { DashboardShell } from "@/components/dashboard/dashboard-shell";

export default function Home() {
  return (
    <main className="p-4 pb-8">
      <Suspense fallback={null}>
        <DashboardShell />
      </Suspense>
    </main>
  );
}
