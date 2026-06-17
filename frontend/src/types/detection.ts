import { ZoneViolation } from "./zone";

export interface BoundingBox {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export type Category = "compliant" | "violation";

export interface Detection {
  id: number;
  label: string;
  category: Category;
  confidence: number;
  bbox: BoundingBox;
  color: string;
}

export interface EquipmentStatus {
  label: string;
  status: Category;
  confidence?: number;
  bbox?: BoundingBox;
}

export interface PersonResult {
  person_id: number;
  track_id?: number;
  bbox: BoundingBox;
  confidence: number;
  role: "worker" | "janitor" | null;
  uniform_type: "vest" | "cleaning_coverall" | null;
  equipment: EquipmentStatus[];
  compliant: boolean;
}

export interface Summary {
  total_persons: number;
  compliant: number;
  violations: number;
  inference_ms: number;
}

export interface DetectionResponse {
  detections: Detection[];
  persons: PersonResult[];
  summary: Summary;
}

export interface ViolationReport {
  id: number;
  timestamp: string;
  violation_type: string;
  details: string;
  snapshot_url?: string;
  video_name?: string;
  frame_index?: number;
  track_id?: number;
}

export interface VideoSummary {
  video_name: string;
  total_frames: number;
  processed_frames: number;
  fps: number;
  duration_seconds: number;
  unique_violations: number;
  candidate_violations: number;
  inference_ms: number;
}

export interface TrackingOverlayFrame {
  frame_index: number;
  time_seconds: number;
  track_id?: number;
  person_id?: number;
  bbox: BoundingBox;
  confidence: number;
  role?: "worker" | "janitor" | null;
  uniform_type?: "vest" | "cleaning_coverall" | null;
  compliant: boolean;
  missing_equipment: string[];
  zone_id?: number;
  camera_zone_view_id?: number;
  physical_zone_id?: number;
  zone_name?: string;
  zone_type?: "RESTRICTED" | "WALKWAY" | "SLIPPERY";
  status: Category | "unknown";
}

export interface TrackingOverlay {
  fps: number;
  stride: number;
  frame_width: number | null;
  frame_height: number | null;
  frames: TrackingOverlayFrame[];
}

export interface VideoProcessingResponse {
  summary: VideoSummary;
  reports: ViolationReport[];
  zone_violations?: ZoneViolation[];
  tracking_overlay?: TrackingOverlay;
}

export interface StreamEvent {
  event: "frame" | "violation" | "zone_violation" | "summary" | "error" | "start" | "end";
  frame_index?: number;
  data: any;
}
