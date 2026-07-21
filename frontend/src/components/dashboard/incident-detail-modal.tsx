"use client";

import { Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import {
  deleteIncident,
  getBehaviorIncident,
  getViolation,
  getZoneViolation,
  IncidentCategory,
} from "@/lib/ppe-api";
import { BehaviorIncident } from "@/types/behavior";
import { ViolationDetail } from "@/types/detection";
import { ZoneViolation } from "@/types/zone";

export type { IncidentCategory };

interface IncidentDetailModalProps {
  category: IncidentCategory;
  incidentId: number;
  onClose: () => void;
  /** Called after a successful delete, in addition to onClose. Omit to hide the delete action. */
  onDeleted?: () => void;
}

type IncidentDetail = ViolationDetail | ZoneViolation | BehaviorIncident;

const CATEGORY_LABEL: Record<IncidentCategory, string> = {
  ppe: "PPE Violation",
  zone: "Zone Violation",
  behavior: "Behavior Incident",
};

export function IncidentDetailModal({
  category,
  incidentId,
  onClose,
  onDeleted,
}: IncidentDetailModalProps) {
  const [detail, setDetail] = useState<IncidentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const fetcher =
      category === "ppe" ? getViolation : category === "zone" ? getZoneViolation : getBehaviorIncident;

    fetcher(incidentId)
      .then((result) => {
        if (!cancelled) setDetail(result);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not load incident");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [category, incidentId]);

  const handleDelete = async () => {
    if (
      !confirm(
        "Mark this incident as a false positive? This permanently deletes the record — this cannot be undone.",
      )
    ) {
      return;
    }
    setDeleting(true);
    setError("");
    try {
      await deleteIncident(category, incidentId);
      onDeleted?.();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete incident");
      setDeleting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-4"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-md border border-slate-200 bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              {CATEGORY_LABEL[category]} · #{incidentId}
            </p>
            <h2 className="text-sm font-semibold text-slate-950">Incident Detail</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-md p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 cursor-pointer"
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="p-4">
          {loading ? <p className="text-sm text-slate-500">Loading…</p> : null}
          {error ? (
            <p className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</p>
          ) : null}
          {!loading && detail ? <DetailBody category={category} detail={detail} /> : null}
        </div>

        {!loading && detail ? (
          <div className="flex justify-end border-t border-slate-200 px-4 py-3">
            <button
              type="button"
              onClick={() => void handleDelete()}
              disabled={deleting}
              className="inline-flex items-center gap-2 rounded-md border border-red-200 px-3 py-2 text-xs font-semibold text-red-700 transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Trash2 className="size-3.5" aria-hidden="true" />
              {deleting ? "Deleting…" : "Mark as false positive"}
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function DetailBody({ category, detail }: { category: IncidentCategory; detail: IncidentDetail }) {
  if (category === "ppe") {
    const d = detail as ViolationDetail;
    return (
      <div className="grid gap-4">
        <SnapshotImage src={d.snapshot_url} />
        <FieldGrid
          fields={[
            ["Type", formatSnakeCase(d.violation_type)],
            ["Timestamp", formatTimestamp(d.timestamp)],
            ["Camera / video", d.video_name ?? "—"],
            ["Frame", d.frame_index ?? "—"],
            ["Track ID", d.track_id ?? "—"],
          ]}
        />
        <p className="text-sm text-slate-700">{d.details}</p>
        {d.subjects.length > 0 ? (
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
              Subjects ({d.subjects.length})
            </p>
            <div className="grid gap-2">
              {d.subjects.map((s) => (
                <div key={s.id} className="rounded-md border border-slate-200 p-2 text-xs text-slate-600">
                  <span className="font-semibold text-slate-900">Missing: </span>
                  {s.missing_equipment.join(", ")}
                  {s.confidence != null ? ` · ${Math.round(s.confidence * 100)}% confidence` : null}
                  {s.track_id != null ? ` · track ${s.track_id}` : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    );
  }

  if (category === "zone") {
    const d = detail as ZoneViolation;
    return (
      <div className="grid gap-4">
        <SnapshotImage src={d.snapshot_path} />
        <FieldGrid
          fields={[
            ["Zone", d.zone_name ?? "—"],
            ["Zone type", d.zone_type ?? "—"],
            ["Timestamp", formatTimestamp(d.timestamp)],
            ["Camera / video", d.video_name],
            ["Frame", d.frame_index],
            ["Track ID", d.track_id ?? "—"],
            ["Status", d.status],
            ["Severity", d.severity ?? "—"],
          ]}
        />
      </div>
    );
  }

  const d = detail as BehaviorIncident;
  const snapshot = d.snapshot_url ?? d.evidence[0]?.file_url ?? undefined;
  return (
    <div className="grid gap-4">
      <SnapshotImage src={snapshot} />
      <FieldGrid
        fields={[
          ["Type", formatSnakeCase(d.behavior_type)],
          ["Timestamp", formatTimestamp(d.timestamp)],
          ["Camera / video", d.video_name ?? "—"],
          ["Status", d.status],
          ["Severity", d.severity ?? "—"],
          ["Confidence", d.confidence != null ? `${Math.round(d.confidence * 100)}%` : "—"],
        ]}
      />
      <p className="text-sm text-slate-700">{d.details}</p>
      {d.subjects.length > 0 ? (
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Subjects ({d.subjects.length})
          </p>
          <div className="grid gap-2">
            {d.subjects.map((s) => (
              <div key={s.id} className="rounded-md border border-slate-200 p-2 text-xs text-slate-600">
                {s.track_id != null ? `Track ${s.track_id}` : `Person ${s.person_index ?? "?"}`}
                {s.confidence != null ? ` · ${Math.round(s.confidence * 100)}% confidence` : null}
                {s.keypoints ? ` · ${s.keypoints.length} keypoints` : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function SnapshotImage({ src }: { src?: string | null }) {
  if (!src) {
    return (
      <div className="flex aspect-video w-full items-center justify-center rounded-md border border-dashed border-slate-300 bg-slate-50 text-sm text-slate-400">
        No snapshot available
      </div>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={src} alt="Incident evidence" className="aspect-video w-full rounded-md object-cover" />
  );
}

function FieldGrid({ fields }: { fields: [string, string | number | null | undefined][] }) {
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-2 border-t border-slate-100 pt-3 text-xs text-slate-600 sm:grid-cols-3">
      {fields.map(([label, value]) => (
        <div key={label}>
          <p className="font-semibold uppercase tracking-wide text-slate-400">{label}</p>
          <p className="mt-0.5 text-slate-800">{value ?? "—"}</p>
        </div>
      ))}
    </div>
  );
}

function formatSnakeCase(value: string): string {
  return value
    .replace(/_/g, " ")
    .split(" ")
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
