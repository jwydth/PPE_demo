import { ViolationReport } from "@/types/detection";

interface ViolationReportCardProps {
  report: ViolationReport;
  showMetadata?: boolean;
}

export function ViolationReportCard({ report, showMetadata = false }: ViolationReportCardProps) {
  return (
    <article className="bg-zinc-900 border border-red-500/30 rounded-lg overflow-hidden">
      {report.snapshot_url && (
        <img src={report.snapshot_url} alt="Safety incident evidence" className="w-full aspect-video object-cover" />
      )}
      <div className="p-3 space-y-3">
        <div className="space-y-1">
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

        {showMetadata && (
          <div className="grid grid-cols-2 gap-2 pt-2 border-t border-zinc-800">
            <MetadataItem label="VIDEO" value={report.video_name ?? "-"} />
            <MetadataItem label="FRAME" value={report.frame_index ?? "-"} />
            <MetadataItem label="TRACK ID" value={report.track_id ?? "-"} />
            <MetadataItem label="RECORD" value={`#${report.id}`} />
          </div>
        )}
      </div>
    </article>
  );
}

function MetadataItem({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="min-w-0">
      <p className="font-mono text-[10px] text-zinc-600 tracking-widest">{label}</p>
      <p className="font-mono text-xs text-zinc-300 truncate">{value}</p>
    </div>
  );
}

export function formatIncidentType(type: string): string {
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

function toTitleCase(value: string): string {
  return value
    .replaceAll("_", " ")
    .replace(/\w\S*/g, (word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase());
}
