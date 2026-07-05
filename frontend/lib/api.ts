import { DetectionResponse, VideoProcessingResponse, ViolationReport } from "@/types/detection";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

export async function analyzeImage(file: File): Promise<DetectionResponse> {
  const form = new FormData();
  form.append("file", file);

  const res = await fetch(`${API_URL}/predict`, {
    method: "POST",
    body: form,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error((body as { detail?: string }).detail ?? "Inference request failed");
  }

  return res.json() as Promise<DetectionResponse>;
}

export async function analyzeVideo(file: File): Promise<VideoProcessingResponse> {
  const form = new FormData();
  form.append("file", file);

  const res = await fetch(`${API_URL}/predict-video`, {
    method: "POST",
    body: form,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error((body as { detail?: string }).detail ?? "Video processing failed");
  }

  const payload = (await res.json()) as VideoProcessingResponse;
  return {
    ...payload,
    reports: payload.reports.map((report) => ({
      ...report,
      snapshot_url: toAbsoluteUrl(report.snapshot_url),
    })),
  };
}

export async function getViolations(): Promise<ViolationReport[]> {
  const res = await fetch(`${API_URL}/violations`);

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error((body as { detail?: string }).detail ?? "Could not load detection history");
  }

  const payload = (await res.json()) as ViolationReport[];
  return payload.map((report) => ({
    ...report,
    snapshot_url: toAbsoluteUrl(report.snapshot_url),
  }));
}

function toAbsoluteUrl(url?: string): string | undefined {
  if (!url) return undefined;
  if (/^https?:\/\//i.test(url)) return url;
  return `${API_URL}${url.startsWith("/") ? url : `/${url}`}`;
}
