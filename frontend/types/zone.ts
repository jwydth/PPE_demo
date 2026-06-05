export type ShapeType = 'polygon' | 'circle' | 'ellipse' | 'freeform';
export type ZoneType = 'RESTRICTED' | 'WALKWAY' | 'FORKLIFT_PATH';

export interface Point2D {
  x: number;
  y: number;
}

export interface ZoneConfiguration {
  id?: number;
  video_name: string;
  zone_name: string;
  zone_type: ZoneType;
  dwell_threshold_seconds: number;
  is_active: boolean;
  ui_shape_data: string; // JSON string
  flattened_coordinates: string; // JSON string
}

export interface ZoneViolation {
  id?: number;
  zone_id: number;
  zone_name?: string;
  zone_type?: ZoneType;
  track_id: number;
  timestamp: string;
  video_name: string;
  frame_index: number;
  snapshot_path?: string;
}

export interface CameraCalibration {
  video_name: string;
  source_points: string; // JSON string of 4 points
}
