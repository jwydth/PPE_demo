export type BehaviorType =
  | "FALL_DETECTED"
  | "RUNNING_DETECTED"
  | "FAINT_DETECTED"
  | "COLLAPSE_DETECTED";

export type BehaviorIncidentStatus = "NEW" | "REVIEWED" | "RESOLVED" | "FALSE_POSITIVE";
export type BehaviorIncidentSeverity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export interface BehaviorIncidentSubject {
  id: number;
  track_id?: number | null;
  person_index?: number | null;
  bounding_box?: Record<string, number> | null;
  confidence?: number | null;
  keypoints?: unknown[] | null;
  features?: Record<string, unknown> | null;
}

export interface BehaviorEvidence {
  id: number;
  evidence_type: "SNAPSHOT";
  object_key: string;
  file_url?: string | null;
  frame_index?: number | null;
  timestamp: string;
}

export interface BehaviorIncident {
  id: number;
  camera_id?: number | null;
  video_name?: string | null;
  behavior_type: BehaviorType;
  status: BehaviorIncidentStatus;
  severity?: BehaviorIncidentSeverity | null;
  confidence?: number | null;
  track_id?: number | null;
  frame_start?: number | null;
  frame_end?: number | null;
  timestamp: string;
  ended_at?: string | null;
  details: string;
  metadata: Record<string, unknown>;
  snapshot_url?: string | null;
  subjects: BehaviorIncidentSubject[];
  evidence: BehaviorEvidence[];
}

export type FallLiveStatus = "normal" | "fall_risk" | "fall" | "no_detection" | "unavailable";

export interface FallLiveSummary {
  frame_index?: number;
  source_frame_index?: number;
  age_frames?: number;
  is_stale?: boolean;
  is_interpolated?: boolean;
  status: FallLiveStatus;
  fall_count: number;
  fall_risk_count: number;
  normal_count: number;
  person_count: number;
  top_label: string;
  top_confidence: number;
  persisted_incident_ids: number[];
}

export interface FallLiveDetection {
  frame_index?: number;
  source_frame_index?: number;
  age_frames?: number;
  is_stale?: boolean;
  is_interpolated?: boolean;
  track_id: number;
  status: "normal" | "fall_risk" | "fall";
  score: number;
  person_confidence: number;
  bbox: {
    x1: number;
    y1: number;
    x2: number;
    y2: number;
  };
  features: Record<string, number>;
  keypoints?: number[][] | null;
  incident_id?: number | null;
}
