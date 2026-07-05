import { Point2D } from "@/types/zone";

/**
 * Checks if a point is inside a polygon using the Ray Casting algorithm.
 */
export function isPointInPolygon(point: Point2D, polygon: Point2D[]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const xi = polygon[i].x;
    const yi = polygon[i].y;
    const xj = polygon[j].x;
    const yj = polygon[j].y;

    const intersect =
      yi > point.y !== yj > point.y &&
      point.x < ((xj - xi) * (point.y - yi)) / (yj - yi) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

/**
 * Checks if two line segments (p1, p2) and (p3, p4) intersect.
 * Returns true only if they intersect at a point that is NOT an endpoint.
 */
export function doSegmentsIntersect(
  p1: Point2D,
  p2: Point2D,
  p3: Point2D,
  p4: Point2D
): boolean {
  const x1 = p1.x,
    y1 = p1.y,
    x2 = p2.x,
    y2 = p2.y;
  const x3 = p3.x,
    y3 = p3.y,
    x4 = p4.x,
    y4 = p4.y;

  const denom = (y4 - y3) * (x2 - x1) - (x4 - x3) * (y2 - y1);
  if (denom === 0) return false; // Parallel

  const ua = ((x4 - x3) * (y1 - y3) - (y4 - y3) * (x1 - x3)) / denom;
  const ub = ((x2 - x1) * (y1 - y3) - (y2 - y1) * (x1 - x3)) / denom;

  // We use a small epsilon for strict interior intersection if desired,
  // or just > 0 and < 1 to allow touching at endpoints.
  return ua > 0 && ua < 1 && ub > 0 && ub < 1;
}

/**
 * Checks if two polygons overlap.
 * Overlap occurs if:
 * 1. Any edge of polygon A intersects any edge of polygon B.
 * 2. Polygon A is completely inside polygon B.
 * 3. Polygon B is completely inside polygon A.
 */
export function doPolygonsOverlap(polyA: Point2D[], polyB: Point2D[]): boolean {
  if (polyA.length < 3 || polyB.length < 3) return false;

  // 1. Check edge intersections
  for (let i = 0; i < polyA.length; i++) {
    const a1 = polyA[i];
    const a2 = polyA[(i + 1) % polyA.length];
    for (let j = 0; j < polyB.length; j++) {
      const b1 = polyB[j];
      const b2 = polyB[(j + 1) % polyB.length];
      if (doSegmentsIntersect(a1, a2, b1, b2)) return true;
    }
  }

  // 2. Check if any vertex of A is inside B
  for (const p of polyA) {
    if (isPointInPolygon(p, polyB)) return true;
  }

  // 3. Check if any vertex of B is inside A
  for (const p of polyB) {
    if (isPointInPolygon(p, polyA)) return true;
  }

  return false;
}
