import {
  DetectionResponse,
  VideoProcessingResponse,
  ViolationReport,
} from "@/types/detection";
import { BehaviorIncident } from "@/types/behavior";
import { ZoneConfiguration, ZoneViolation } from "@/types/zone";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8000";

async function readError(res: Response, fallback: string): Promise<Error> {
  const body = await res.json().catch(() => ({ detail: res.statusText }));
  return new Error((body as { detail?: string }).detail ?? fallback);
}

export function toAbsoluteUrl(url?: string): string | undefined {
  if (!url) return undefined;
  if (/^https?:\/\//i.test(url)) return url;
  return `${API_URL}${url.startsWith("/") ? url : `/${url}`}`;
}

export async function analyzeImage(file: File): Promise<DetectionResponse> {
  const form = new FormData();
  form.append("file", file);

  const res = await fetch(`${API_URL}/predict`, {
    method: "POST",
    body: form,
  });

  if (!res.ok) throw await readError(res, "Image inference request failed");
  return res.json() as Promise<DetectionResponse>;
}

export async function uploadVideo(file: File): Promise<{ filename: string; message: string }> {
  const form = new FormData();
  form.append("file", file);

  const res = await fetch(`${API_URL}/upload-video`, {
    method: "POST",
    body: form,
  });

  if (!res.ok) throw await readError(res, "Video upload failed");
  return res.json() as Promise<{ filename: string; message: string }>;
}

export async function analyzeVideo(
  file: File,
  options: { enablePpe?: boolean; enableZone?: boolean } = {},
): Promise<VideoProcessingResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("enable_ppe", String(options.enablePpe ?? true));
  form.append("enable_zone", String(options.enableZone ?? true));

  const res = await fetch(`${API_URL}/predict-video`, {
    method: "POST",
    body: form,
  });

  if (!res.ok) throw await readError(res, "Video processing failed");

  const payload = (await res.json()) as VideoProcessingResponse;
  return {
    ...payload,
    reports: payload.reports.map((report) => ({
      ...report,
      snapshot_url: toAbsoluteUrl(report.snapshot_url),
    })),
    zone_violations: (payload.zone_violations ?? []).map((violation) => ({
      ...violation,
      snapshot_path: toAbsoluteUrl(violation.snapshot_path),
    })),
  };
}

export async function getViolations(): Promise<ViolationReport[]> {
  const res = await fetch(`${API_URL}/violations`);
  if (!res.ok) throw await readError(res, "Could not load PPE violations");

  const payload = (await res.json()) as ViolationReport[];
  return payload.map((report) => ({
    ...report,
    snapshot_url: toAbsoluteUrl(report.snapshot_url),
  }));
}

export async function getZoneViolations(): Promise<ZoneViolation[]> {
  const res = await fetch(`${API_URL}/zone-violations`);
  if (!res.ok) throw await readError(res, "Could not load zone violations");

  const payload = (await res.json()) as ZoneViolation[];
  return payload.map((violation) => ({
    ...violation,
    snapshot_path: toAbsoluteUrl(violation.snapshot_path),
  }));
}

export async function getBehaviorIncidents(): Promise<BehaviorIncident[]> {
  const res = await fetch(`${API_URL}/behavior-incidents?limit=100`);
  if (!res.ok) throw await readError(res, "Could not load behavior incidents");

  const payload = (await res.json()) as BehaviorIncident[];
  return payload.map((incident) => ({
    ...incident,
    snapshot_url: toAbsoluteUrl(incident.snapshot_url ?? undefined),
    evidence: incident.evidence.map((item) => ({
      ...item,
      file_url: toAbsoluteUrl(item.file_url ?? undefined),
    })),
  }));
}

export async function getSafetyEvents(): Promise<(ViolationReport | ZoneViolation | BehaviorIncident)[]> {
  const [ppe, zones, behavior] = await Promise.all([
    getViolations(),
    getZoneViolations(),
    getBehaviorIncidents(),
  ]);
  return [...ppe, ...zones, ...behavior].sort(
    (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
  );
}

export async function deleteAllIncidents(): Promise<{
  ppe_violations_deleted: number;
  zone_violations_deleted: number;
  total_deleted: number;
}> {
  const res = await fetch(`${API_URL}/violations`, { method: "DELETE" });
  if (!res.ok) throw await readError(res, "Could not delete incidents");
  return res.json();
}

export async function deleteViolation(violationId: number): Promise<{ success: boolean }> {
  const res = await fetch(`${API_URL}/violations/${violationId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw await readError(res, "Could not delete violation");
  return res.json();
}

export async function deleteZoneViolation(
  zoneViolationId: number,
): Promise<{ success: boolean }> {
  const res = await fetch(`${API_URL}/zone-violations/${zoneViolationId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw await readError(res, "Could not delete zone violation");
  return res.json();
}

export async function getZones(videoName: string): Promise<ZoneConfiguration[]> {
  // video_name is a query param so source keys with slashes (RTSP URLs) work.
  const res = await fetch(`${API_URL}/zones?video_name=${encodeURIComponent(videoName)}`);
  if (!res.ok) throw await readError(res, "Could not load zones");
  return res.json();
}

export async function deleteZonesForVideo(videoName: string): Promise<{ deleted: number }> {
  const res = await fetch(`${API_URL}/zones/video?video_name=${encodeURIComponent(videoName)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw await readError(res, "Could not clear saved zones");
  return res.json();
}

export async function saveZone(zone: ZoneConfiguration): Promise<ZoneConfiguration> {
  const res = await fetch(`${API_URL}/zones`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(zone),
  });
  if (!res.ok) throw await readError(res, "Could not save zone");
  return res.json();
}
