import { VideoProcessingResponse, ViolationReport } from "@/types/detection";

interface VideoReportsPanelProps {
  result: VideoProcessingResponse;
}

export function VideoReportsPanel({ result }: VideoReportsPanelProps) {
  const totalIncidents = result.reports.length;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4 items-start">
      <div className="bg-zinc-950 border border-zinc-800 rounded-lg p-3">
        <p className="font-mono text-[10px] text-zinc-600 tracking-widest mb-3">
          SAFETY SUMMARY
        </p>
        <div className="grid grid-cols-2 gap-2">
          <Metric label="TOTAL INCIDENTS" value={totalIncidents} accent={totalIncidents > 0 ? "red" : "green"} />
          <Metric label="INFERENCE TIME" value={formatInferenceTime(result.summary.inference_ms)} />
        </div>
      </div>

      <div className="bg-zinc-950 border border-zinc-800 rounded-lg p-3">
        <p className="font-mono text-[10px] text-zinc-600 tracking-widest mb-3">
          SAFETY INCIDENTS ({result.reports.length})
        </p>
        {result.reports.length === 0 ? (
          <div className="border border-green-500/30 bg-green-500/10 rounded-lg p-4 font-mono text-sm text-green-300">
            No safety incidents were detected in this video.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {result.reports.map((report) => (
              <ReportCard key={report.id} report={report} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  accent,
}: {
  label: string;
  value: string | number;
  accent?: "red" | "green";
}) {
  const valueColor =
    accent === "red" ? "text-red-400" : accent === "green" ? "text-green-400" : "text-orange-400";

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 min-w-0">
      <p className="text-zinc-500 font-mono text-[10px] tracking-widest truncate">{label}</p>
      <p className={`font-mono text-sm font-bold ${valueColor}`}>
        {value}
      </p>
    </div>
  );
}

function ReportCard({ report }: { report: ViolationReport }) {
  return (
    <article className="bg-zinc-900 border border-red-500/30 rounded-lg overflow-hidden">
      {report.snapshot_url && (
        <img src={report.snapshot_url} alt="Safety incident evidence" className="w-full aspect-video object-cover" />
      )}
      <div className="p-3 space-y-2">
        <p className="font-mono text-[10px] text-red-300 tracking-widest uppercase">
          INCIDENT TYPE
        </p>
        <h3 className="text-sm font-semibold text-zinc-100">
          {formatIncidentType(report.violation_type)}
        </h3>
        <p className="font-mono text-[10px] text-zinc-500">
          Detected {formatDetectedTime(report.timestamp)}
        </p>
      </div>
    </article>
  );
}

function formatIncidentType(type: string): string {
  const labels: Record<string, string> = {
    missing_helmet: "Missing Safety Helmet",
    missing_vest: "Missing Safety Vest",
    missing_helmet_and_vest: "Missing Safety Helmet and Vest",
    proximity_violation: "Proximity Violation",
    zone_incursion: "Zone Incursion",
    fall_detection: "Fall Detection",
    hazard_sign_activation: "Hazard Sign Activation",
  };

  return labels[type] ?? toTitleCase(type);
}

function formatDetectedTime(timestamp: string): string {
  const detectedAt = new Date(timestamp);
  return Number.isNaN(detectedAt.getTime()) ? timestamp : detectedAt.toLocaleString();
}

function formatInferenceTime(inferenceMs: number): string {
  return `${(inferenceMs / 1000).toFixed(1)}s`;
}

function toTitleCase(value: string): string {
  return value
    .replaceAll("_", " ")
    .replace(/\w\S*/g, (word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase());
}
