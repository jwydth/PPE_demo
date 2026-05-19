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

export interface Summary {
  total_persons: number;
  compliant: number;
  violations: number;
  inference_ms: number;
}

export interface DetectionResponse {
  detections: Detection[];
  summary: Summary;
}
