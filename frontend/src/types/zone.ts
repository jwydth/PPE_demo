export type ShapeType = "polygon";
export type ZoneType = "RESTRICTED" | "WALKWAY" | "SLIPPERY" | "IGNORE";

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

export interface PhysicalZone {
  id: number;
  name: string;
  zone_type: ZoneType;
  is_active: boolean;
}

export interface ZoneSuggestion {
  suggestion_id: string;
  /** Source key of the camera that raised it. Stamped on by useLiveStream —
   * the backend's suggestion_id is "<class>:<grid_x>:<grid_y>" with no camera
   * in it, so two cameras seeing the same sign in the same part of frame
   * produce the same id. */
  source_key?: string;
  zone_type: ZoneType;
  source_class: string;
  confidence: number;
  normalized_coordinates: { x: number; y: number }[];
  frame_index: number;
}

export interface PPESuggestion {
  suggestion_id: string;
  /** See ZoneSuggestion.source_key. */
  source_key?: string;
  source_class: string;
  confidence: number;
  frame_index: number;
  bbox: [number, number, number, number]; // normalized x1, y1, x2, y2 in [0, 1]
}

export interface ZoneViolation {
  id?: number;
  zone_id?: number;
  camera_id?: number;
  physical_zone_id?: number;
  camera_zone_view_id?: number;
  zone_name?: string;
  zone_type?: ZoneType;
  track_id?: number;
  timestamp: string;
  video_name: string;
  frame_index: number;
  snapshot_path?: string;
  status?: string;
  severity?: string;
}
