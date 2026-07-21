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
