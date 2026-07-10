// Blueprint geometry + camera→zone mapping. Everything else in factory3d/
// reads from here — this is the single source of truth for the 3D view.
//
// Coordinate system: top-down floor plan mapped to the Three.js X/Z plane
// (Y is height/up). Units are arbitrary "scene units"; the whole building
// spans roughly X∈[-6, 8], Z∈[-4, 4]. Values are proportional to the
// proposal's blueprint image (Z01 top-left, Z02 bottom-left, Z03 full-height
// right, plus a utility room column on the far right).
//
// Provenance: these coordinates were pixel-measured from the blueprint PNG
// and converted with a fixed scale factor — a to-scale proportional
// reconstruction of a 2D top-down image, not a CAD survey.

export type ZoneId = "Z01" | "Z02" | "Z03";

export interface ZoneDef {
  id: ZoneId;
  label: string;
  /** center [x, z] on the floor plane */
  position: [number, number];
  /** [width (x), depth (z)] footprint */
  size: [number, number];
  /** base hue used for the block + heat ramp (hex) */
  color: string;
  /**
   * Source keys that belong to this zone. An incident is assigned to this
   * zone when its camera_id (number) OR video_name / source_key (string)
   * matches an entry here. This is how single-camera routing works today.
   */
  sources: {
    cameraIds?: number[];
    /** match if the incident's video_name/source_key CONTAINS any of these */
    videoNameIncludes?: string[];
  };
  /** false => zone shown dimmed as "not yet monitored" (no active camera) */
  active: boolean;
}

// ⚠️ SINGLE-CAMERA CONFIG:
// Today only Z01 has a live camera. Its `sources` point at the actual
// stream — the RTSP source in this project is rtsp://localhost:8554/mystream
// (see README), so videoNameIncludes: ["mystream"] catches live incidents;
// "mp_" catches uploaded-video analysis runs too.
// When camera 2 / 3 come online: flip `active: true` and fill in `sources`
// below — no other code changes are required.
export const ZONES: ZoneDef[] = [
  {
    id: "Z01",
    label: "Production area",
    position: [-1.99, -2.0],
    size: [8.02, 4.0],
    color: "#378ADD",
    sources: { cameraIds: [1], videoNameIncludes: ["mystream", "mp_"] },
    active: true,
  },
  {
    id: "Z02",
    label: "Warehouse",
    position: [-1.99, 2.0],
    size: [8.02, 4.0],
    color: "#1D9E75",
    sources: { cameraIds: [2] },
    active: false, // no camera yet
  },
  {
    id: "Z03",
    label: "Packing area",
    position: [4.01, 0.0],
    size: [3.98, 8.0],
    color: "#D85A30",
    sources: { cameraIds: [3] },
    active: false, // no camera yet
  },
];

// Static building shell (walls with gate gaps) traced from the blueprint.
// Non-interactive decoration.
export const WALLS: { size: [number, number, number]; position: [number, number, number] }[] = [
  { size: [4.33, 1.4, 0.15], position: [-3.82, 0.7, -4] }, // north wall, left of gate
  { size: [5.19, 1.4, 0.15], position: [3.4, 0.7, -4] },   // north wall, right of gate
  { size: [4.33, 1.4, 0.15], position: [-3.82, 0.7, 4] },  // south wall, left of gate
  { size: [5.19, 1.4, 0.15], position: [3.4, 0.7, 4] },    // south wall, right of gate
  { size: [0.15, 1.4, 8], position: [-6, 0.7, 0] },        // west wall
  { size: [0.15, 1.4, 8], position: [6, 0.7, 0] },         // east wall
];

export const GATES: { size: [number, number, number]; position: [number, number, number] }[] = [
  { size: [1.65, 0.1, 0.4], position: [-0.515, 0.75, -4] }, // north gate
  { size: [1.65, 0.1, 0.4], position: [-0.515, 0.75, 4] },  // south gate
];

// Utility room outlines (electrical, maintenance, first aid, toilet) on the
// far-right column.
export const UTILITY_ROOMS: { centerZ: number; depthZ: number }[] = [
  { centerZ: -2.985, depthZ: 2.03 },
  { centerZ: -1.085, depthZ: 1.77 },
  { centerZ: 0.78, depthZ: 1.96 },
  { centerZ: 2.88, depthZ: 2.24 },
];
export const UTILITY_COLUMN = {
  size: [1.57, 0.4, 8] as [number, number, number],
  position: [6.785, 0.2, 0] as [number, number, number],
};

// Restricted-zone outline inside Z03.
export const RESTRICTED_ZONE = {
  size: [2.12, 0.05, 1.63] as [number, number, number],
  position: [4.32, 0.55, 1.285] as [number, number, number],
};
