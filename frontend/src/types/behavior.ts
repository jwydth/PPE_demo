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
