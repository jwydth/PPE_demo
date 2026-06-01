import { VideoProcessingResponse, ViolationReport } from "@/types/detection";

interface VideoReportsPanelProps {
  result: VideoProcessingResponse;
}

export function VideoReportsPanel({ result }: VideoReportsPanelProps) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4 items-start">
      <div className="bg-zinc-950 border border-zinc-800 rounded-lg p-3">
        <p className="font-mono text-[10px] text-zinc-600 tracking-widest mb-3">
          VIDEO SUMMARY
        </p>
        <div className="grid grid-cols-2 gap-2">
          <Metric label="FRAMES" value={result.summary.total_frames} />
          <Metric label="SCANNED" value={result.summary.processed_frames} />
          <Metric label="FPS" value={result.summary.fps.toFixed(1)} />
          <Metric label="SECONDS" value={result.summary.duration_seconds.toFixed(1)} />
          <Metric label="PENDING" value={result.summary.candidate_violations ?? 0} />
          <Metric label="REPORTS" value={result.summary.unique_violations} accent="red" />
          <Metric label="TIME" value={`${(result.summary.inference_ms / 1000).toFixed(1)}s`} />
        </div>
      </div>

      <div className="bg-zinc-950 border border-zinc-800 rounded-lg p-3">
        <p className="font-mono text-[10px] text-zinc-600 tracking-widest mb-3">
          VIOLATION REPORTS ({result.reports.length})
        </p>
        {result.reports.length === 0 ? (
          <div className="border border-green-500/30 bg-green-500/10 rounded-lg p-4 font-mono text-sm text-green-300">
            No unique PPE violations were detected in this video.
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
  accent?: "red";
}) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 min-w-0">
      <p className="text-zinc-500 font-mono text-[10px] tracking-widest truncate">{label}</p>
      <p className={`font-mono text-sm font-bold ${accent === "red" ? "text-red-400" : "text-orange-400"}`}>
        {value}
      </p>
    </div>
  );
}

function ReportCard({ report }: { report: ViolationReport }) {
  const timestamp = new Date(report.timestamp);

  return (
    <article className="bg-zinc-900 border border-red-500/30 rounded-lg overflow-hidden">
      {report.snapshot_url && (
        <img src={report.snapshot_url} alt="Violation snapshot" className="w-full aspect-video object-cover" />
      )}
      <div className="p-3 space-y-2">
        <div className="flex items-center justify-between gap-3">
          <span className="font-mono text-[10px] text-red-300 tracking-widest uppercase">
            {report.violation_type.replaceAll("_", " ")}
          </span>
          <span className="font-mono text-[10px] text-zinc-500 shrink-0">
            Frame {report.frame_index ?? "-"}
          </span>
        </div>
        <p className="font-mono text-sm text-zinc-200">{report.details}</p>
        <div className="flex items-center justify-between gap-3 font-mono text-[10px] text-zinc-500">
          <span>Track {report.track_id ?? "-"}</span>
          <span>{Number.isNaN(timestamp.getTime()) ? report.timestamp : timestamp.toLocaleString()}</span>
        </div>
      </div>
    </article>
  );
}
