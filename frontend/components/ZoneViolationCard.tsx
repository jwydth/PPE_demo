import { ZoneType, ZoneViolation } from "@/types/zone";

interface ZoneViolationCardProps {
  report: ZoneViolation;
}

function MetadataItem({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="min-w-0">
      <p className="font-mono text-[10px] text-zinc-600 tracking-widest">{label}</p>
      <p className="font-mono text-xs text-zinc-300 truncate">{value}</p>
    </div>
  );
}

function formatDetectedTime(timestamp: string): string {
  const detectedAt = new Date(timestamp);
  return Number.isNaN(detectedAt.getTime()) ? timestamp : detectedAt.toLocaleString();
}

const ZONE_TYPE_CONFIG: Record<ZoneType, { label: string; title: string; color: string; border: string }> = {
  RESTRICTED:    { label: "RESTRICTED ZONE",  title: "Restricted Zone Incursion", color: "text-red-300",    border: "border-red-500/30" },
  WALKWAY:       { label: "WALKWAY VIOLATION", title: "Left Walkway Boundary",     color: "text-blue-300",   border: "border-blue-500/30" },
  FORKLIFT_PATH: { label: "FORKLIFT PATH",     title: "Forklift Path Incursion",   color: "text-yellow-300", border: "border-yellow-500/30" },
};

const DEFAULT_CONFIG = { label: "ZONE VIOLATION", title: "Zone Violation", color: "text-yellow-300", border: "border-yellow-500/30" };

export function ZoneViolationCard({ report }: ZoneViolationCardProps) {
  const config = (report.zone_type && ZONE_TYPE_CONFIG[report.zone_type]) || DEFAULT_CONFIG;

  return (
    <article className={`bg-zinc-900 border ${config.border} rounded-lg overflow-hidden`}>
      {report.snapshot_path && (
        <img src={report.snapshot_path} alt="Zone incursion evidence" className="w-full aspect-video object-cover" />
      )}
      <div className="p-3 space-y-3">
        <div className="space-y-1">
          <p className={`font-mono text-[10px] ${config.color} tracking-widest uppercase`}>
            {config.label}
          </p>
          <h3 className="text-sm font-semibold text-zinc-100">
            {config.title}
          </h3>
          {report.zone_name && (
            <p className="font-mono text-xs text-zinc-400">{report.zone_name}</p>
          )}
          <p className="font-mono text-[10px] text-zinc-500">
            Detected {formatDetectedTime(report.timestamp)}
          </p>
        </div>

        <div className="grid grid-cols-2 gap-2 pt-2 border-t border-zinc-800">
          <MetadataItem label="VIDEO" value={report.video_name ?? "-"} />
          <MetadataItem label="FRAME" value={report.frame_index ?? "-"} />
          <MetadataItem label="TRACK ID" value={report.track_id ?? "-"} />
          <MetadataItem label="ZONE" value={report.zone_name ?? `#${report.zone_id}`} />
        </div>
      </div>
    </article>
  );
}
