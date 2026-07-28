export interface Camera {
  id: number;
  name: string;
  source_key: string;
  source_uri: string | null;
  home_zone_id: number | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Feature {
  id: number;
  key: string;
  name: string;
  description: string | null;
  is_active: boolean;
}

export interface CameraFeatureConfig {
  id: number;
  camera_id: number;
  feature_key: string;
  feature_name: string;
  is_enabled: boolean;
  config_params: Record<string, any> | null;
}
