import { ZoneViolation } from "@/types/zone";

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

export function ZoneViolationCard({ report }: ZoneViolationCardProps) {
  return (
    <article className="bg-zinc-900 border border-yellow-500/30 rounded-lg overflow-hidden">
      {report.snapshot_path && (
        <img src={report.snapshot_path} alt="Zone incursion evidence" className="w-full aspect-video object-cover" />
      )}
      <div className="p-3 space-y-3">
        <div className="space-y-1">
          <p className="font-mono text-[10px] text-yellow-300 tracking-widest uppercase">
            INCIDENT TYPE
          </p>
          <h3 className="text-sm font-semibold text-zinc-100">
            Restricted Zone Incursion
          </h3>
          <p className="font-mono text-[10px] text-zinc-500">
            Detected {formatDetectedTime(report.timestamp)}
          </p>
        </div>

        <div className="grid grid-cols-2 gap-2 pt-2 border-t border-zinc-800">
            <MetadataItem label="VIDEO" value={report.video_name ?? "-"} />
            <MetadataItem label="FRAME" value={report.frame_index ?? "-"} />
            <MetadataItem label="TRACK ID" value={report.track_id ?? "-"} />
            <MetadataItem label="ZONE ID" value={report.zone_id ?? "-"} />
        </div>
      </div>
    </article>
  );
}
