import { Point2D, ZoneType } from "@/types/zone";

export type AnalysisPhase = "idle" | "loading" | "done" | "error";

export type DraftZone = {
  id: string;
  name: string;
  type: ZoneType;
  dwellThresholdSeconds: number;
  points: Point2D[];
};
