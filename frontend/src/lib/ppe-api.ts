import {
  DetectionResponse,
  VideoProcessingResponse,
  ViolationDetail,
  ViolationReport,
} from "@/types/detection";
import {
  AnalyticsCompare,
  AnalyticsRangeParam,
  AnalyticsSummary,
  AnalyticsTrend,
  CompareMode,
  UnifiedIncident,
} from "@/types/analytics";
import { BehaviorIncident } from "@/types/behavior";
import { Camera, Feature, CameraFeatureConfig } from "@/types/camera";
import {
  ReportEmailRequest,
  ReportEmailResponse,
  ReportPreview,
  ReportScheduleRequest,
  ReportScheduleResponse,
} from "@/types/report";
import { PhysicalZone, ZoneConfiguration, ZoneViolation } from "@/types/zone";

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
  const res = await fetch(`${API_URL}/violations?limit=500`);
  if (!res.ok) throw await readError(res, "Could not load PPE violations");

  const payload = (await res.json()) as ViolationReport[];
  return payload.map((report) => ({
    ...report,
    snapshot_url: toAbsoluteUrl(report.snapshot_url),
  }));
}

export async function getViolation(violationId: number): Promise<ViolationDetail> {
  const res = await fetch(`${API_URL}/violations/${violationId}`);
  if (!res.ok) throw await readError(res, "Could not load violation detail");

  const detail = (await res.json()) as ViolationDetail;
  return { ...detail, snapshot_url: toAbsoluteUrl(detail.snapshot_url) };
}

export async function getZoneViolations(): Promise<ZoneViolation[]> {
  const res = await fetch(`${API_URL}/zone-violations?limit=500`);
  if (!res.ok) throw await readError(res, "Could not load zone violations");

  const payload = (await res.json()) as ZoneViolation[];
  return payload.map((violation) => ({
    ...violation,
    snapshot_path: toAbsoluteUrl(violation.snapshot_path),
  }));
}

export async function getZoneViolation(zoneViolationId: number): Promise<ZoneViolation> {
  const res = await fetch(`${API_URL}/zone-violations/${zoneViolationId}`);
  if (!res.ok) throw await readError(res, "Could not load zone violation detail");

  const violation = (await res.json()) as ZoneViolation;
  return { ...violation, snapshot_path: toAbsoluteUrl(violation.snapshot_path) };
}

export async function getBehaviorIncidents(): Promise<BehaviorIncident[]> {
  const res = await fetch(`${API_URL}/behavior-incidents?limit=500`);
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

export async function getBehaviorIncident(incidentId: number): Promise<BehaviorIncident> {
  const res = await fetch(`${API_URL}/behavior-incidents/${incidentId}`);
  if (!res.ok) throw await readError(res, "Could not load behavior incident detail");

  const incident = (await res.json()) as BehaviorIncident;
  return {
    ...incident,
    snapshot_url: toAbsoluteUrl(incident.snapshot_url ?? undefined),
    evidence: incident.evidence.map((item) => ({
      ...item,
      file_url: toAbsoluteUrl(item.file_url ?? undefined),
    })),
  };
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
  behavior_incidents_deleted: number;
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

export async function deleteBehaviorIncident(
  incidentId: number,
): Promise<{ success: boolean }> {
  const res = await fetch(`${API_URL}/behavior-incidents/${incidentId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw await readError(res, "Could not delete behavior incident");
  return res.json();
}

export type IncidentCategory = "ppe" | "zone" | "behavior";

export async function deleteIncident(
  category: IncidentCategory,
  id: number,
): Promise<{ success: boolean }> {
  if (category === "ppe") return deleteViolation(id);
  if (category === "zone") return deleteZoneViolation(id);
  return deleteBehaviorIncident(id);
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

export async function getAnalyticsSummary(
  range: AnalyticsRangeParam,
  zoneId: number | null,
): Promise<AnalyticsSummary> {
  const params = new URLSearchParams({ range });
  if (zoneId != null) params.set("zone_id", String(zoneId));
  const res = await fetch(`${API_URL}/analytics/summary?${params}`);
  if (!res.ok) throw await readError(res, "Could not load analytics summary");
  return res.json();
}

export async function getAnalyticsTrend(
  range: AnalyticsRangeParam,
  zoneId: number | null,
): Promise<AnalyticsTrend> {
  const params = new URLSearchParams({ range });
  if (zoneId != null) params.set("zone_id", String(zoneId));
  const res = await fetch(`${API_URL}/analytics/trend?${params}`);
  if (!res.ok) throw await readError(res, "Could not load analytics trend");
  return res.json();
}

export async function getAnalyticsCompare(
  mode: CompareMode,
  zoneId: number | null,
): Promise<AnalyticsCompare> {
  const params = new URLSearchParams({ mode });
  if (zoneId != null) params.set("zone_id", String(zoneId));
  const res = await fetch(`${API_URL}/analytics/compare?${params}`);
  if (!res.ok) throw await readError(res, "Could not load analytics comparison");
  return res.json();
}

export async function getUnifiedIncidents(
  limit: number,
  zoneId: number | null,
): Promise<UnifiedIncident[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (zoneId != null) params.set("zone_id", String(zoneId));
  const res = await fetch(`${API_URL}/analytics/incidents?${params}`);
  if (!res.ok) throw await readError(res, "Could not load incident feed");
  const incidents = (await res.json()) as UnifiedIncident[];
  return incidents.map((i) => ({ ...i, snapshot_url: toAbsoluteUrl(i.snapshot_url ?? undefined) ?? null }));
}

export async function getPhysicalZones(): Promise<PhysicalZone[]> {
  const res = await fetch(`${API_URL}/physical-zones`);
  if (!res.ok) throw await readError(res, "Could not load physical zones");
  return res.json();
}

export async function createPhysicalZone(name: string): Promise<PhysicalZone> {
  const res = await fetch(`${API_URL}/physical-zones`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw await readError(res, "Could not create zone");
  return res.json();
}

export async function deletePhysicalZone(zoneId: number): Promise<void> {
  const res = await fetch(`${API_URL}/physical-zones/${zoneId}`, { method: "DELETE" });
  if (!res.ok) throw await readError(res, "Could not delete zone");
}

export async function updatePhysicalZone(zoneId: number, name: string): Promise<PhysicalZone> {
  const res = await fetch(`${API_URL}/physical-zones/${zoneId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw await readError(res, "Could not update zone");
  return res.json();
}

export async function getCameras(): Promise<Camera[]> {
  const res = await fetch(`${API_URL}/cameras`);
  if (!res.ok) throw await readError(res, "Could not load cameras");
  return res.json();
}

// Idempotent get-or-create, keyed by source_key. Lets the frontend bind a
// configured stream to a backend camera before any incident has occurred on it.
export async function ensureCamera(name: string, sourceKey: string): Promise<Camera> {
  const res = await fetch(`${API_URL}/cameras`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, source_key: sourceKey }),
  });
  if (!res.ok) throw await readError(res, "Could not register camera");
  return res.json();
}

export async function setCameraHomeZone(
  cameraId: number,
  zoneId: number | null,
): Promise<Camera> {
  const res = await fetch(`${API_URL}/cameras/${cameraId}/home-zone`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ zone_id: zoneId }),
  });
  if (!res.ok) throw await readError(res, "Could not set camera home zone");
  return res.json();
}

export async function deleteCamera(cameraId: number): Promise<{ success: boolean }> {
  const res = await fetch(`${API_URL}/cameras/${cameraId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw await readError(res, "Could not delete camera");
  return res.json();
}

export async function getFeatures(): Promise<Feature[]> {
  const res = await fetch(`${API_URL}/features`);
  if (!res.ok) throw await readError(res, "Could not load features");
  return res.json();
}

export async function getCameraFeatures(cameraId: number): Promise<CameraFeatureConfig[]> {
  const res = await fetch(`${API_URL}/cameras/${cameraId}/features`);
  if (!res.ok) throw await readError(res, "Could not load camera features");
  return res.json();
}

export async function updateCameraFeatures(
  cameraId: number,
  updates: { feature_key: string; is_enabled: boolean; config_params?: any }[]
): Promise<CameraFeatureConfig[]> {
  const res = await fetch(`${API_URL}/cameras/${cameraId}/features`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw await readError(res, "Could not update camera features");
  return res.json();
}

export async function getReportPreview(
  range: AnalyticsRangeParam,
  zoneId: number | null,
): Promise<ReportPreview> {
  const params = new URLSearchParams({ range });
  if (zoneId != null) params.set("zone_id", String(zoneId));
  const res = await fetch(`${API_URL}/reports/incidents/preview?${params}`);
  if (!res.ok) throw await readError(res, "Could not load the report preview");
  return res.json();
}

export async function downloadIncidentReportPdf(
  range: AnalyticsRangeParam,
  zoneId: number | null,
  includeSnapshots = true,
): Promise<void> {
  const params = new URLSearchParams({ range, include_snapshots: String(includeSnapshots) });
  if (zoneId != null) params.set("zone_id", String(zoneId));
  const res = await fetch(`${API_URL}/reports/incidents.pdf?${params}`);
  if (!res.ok) throw await readError(res, "Could not generate the PDF report");
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(cd)?.[1] ?? "safety-report.pdf";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function emailIncidentReport(payload: ReportEmailRequest): Promise<ReportEmailResponse> {
  const res = await fetch(`${API_URL}/reports/incidents/email`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw await readError(res, "Could not send the report email");
  return res.json();
}

export async function getReportSchedule(): Promise<ReportScheduleResponse> {
  const res = await fetch(`${API_URL}/reports/schedule`);
  if (!res.ok) throw await readError(res, "Could not load the report schedule");
  return res.json();
}

export async function updateReportSchedule(
  payload: ReportScheduleRequest,
): Promise<ReportScheduleResponse> {
  const res = await fetch(`${API_URL}/reports/schedule`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw await readError(res, "Could not save the report schedule");
  return res.json();
}
