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
  bbox: BoundingBox;
  confidence: number;
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
