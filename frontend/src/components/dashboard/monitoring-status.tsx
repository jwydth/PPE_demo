import type { AnalyticsSummary } from "@/types/analytics";

type Tone = "live" | "idle" | "down" | "pending";

const dotTone: Record<Tone, string> = {
  live: "bg-emerald-500",
  idle: "bg-amber-500",
  down: "bg-red-500",
  pending: "bg-slate-300",
};

const textTone: Record<Tone, string> = {
  live: "text-slate-600",
  idle: "text-amber-700",
  down: "text-red-700",
  pending: "text-slate-500",
};

/**
 * The "is anything actually being watched right now" line above the page title.
 *
 * This used to be a hardcoded green dot beside the words "Live monitoring
 * active" — no state binding of any kind. It read as healthy while zero
 * cameras were connected and while the backend was down, which is the one
 * thing a safety banner must never do: an indicator that cannot report bad
 * news trains people to stop reading it.
 */
export function MonitoringStatus({
  summary,
  isError,
}: {
  summary: AnalyticsSummary | null;
  isError: boolean;
}) {
  const live = summary?.live_cameras ?? 0;
  const total = summary?.total_cameras ?? 0;

  const tone: Tone = isError && summary === null
    ? "down"
    : summary === null
      ? "pending"
      : live > 0
        ? "live"
        : "idle";

  const label =
    tone === "down"
      ? "Monitoring unavailable — cannot reach the backend"
      : tone === "pending"
        ? "Checking monitoring status…"
        : tone === "idle"
          ? `No cameras streaming — ${total} configured`
          : `Live monitoring · ${live} of ${total} camera${total === 1 ? "" : "s"}`;

  return (
    <div className="flex items-center gap-2" role="status" aria-live="polite">
      <span
        className={`size-2 shrink-0 rounded-full ${dotTone[tone]} ${
          tone === "pending" ? "animate-pulse motion-reduce:animate-none" : ""
        }`}
        aria-hidden="true"
      />
      {/* The dot is decorative — the sentence beside it carries the state, so
          this never depends on colour alone. */}
      <p className={`text-sm font-medium ${textTone[tone]}`}>{label}</p>
    </div>
  );
}
