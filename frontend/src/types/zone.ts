export type ShapeType = "polygon";
export type ZoneType = "RESTRICTED" | "WALKWAY";

export interface Point2D {
  x: number;
  y: number;
  curveControl?: { x: number; y: number };
}

export interface ZoneConfiguration {
  id?: number;
  video_name: string;
  zone_name: string;
  zone_type: ZoneType;
  dwell_threshold_seconds: number;
  is_active: boolean;
  ui_shape_data: string;
  flattened_coordinates: string;
}

export interface ZoneSuggestion {
  suggestion_id: string;
  zone_type: ZoneType;
  source_class: string;
  confidence: number;
  normalized_coordinates: { x: number; y: number }[];
  frame_index: number;
}

export interface PPESuggestion {
  suggestion_id: string;
  source_class: string;
  confidence: number;
  frame_index: number;
}

export interface ZoneViolation {
  id?: number;
  zone_id?: number;
  zone_name?: string;
  zone_type?: ZoneType;
  track_id?: number;
  timestamp: string;
  video_name: string;
  frame_index: number;
  snapshot_path?: string;
}
