# Implementation Spec — Interactive 3D Factory Blueprint for the Incident Log

> **For:** Claude Code, working inside the `Smart_Factory_Safety_Monitoring` repo
> (frontend: Next.js 16 / React 19 / Tailwind v4 / TypeScript).
> **Goal:** Add an interactive 3D floor-plan view of the factory. Each zone is a
> clickable block; clicking a zone filters the existing incident log to that zone
> and shows a small "insight" summary. This is a **frontend-only** feature that
> consumes existing APIs — no backend or DB changes are required for the first
> version.

---

## 0. Context & current-state facts (read before coding)

These are verified facts about the existing codebase. Do not assume anything
beyond them.

- The dashboard entry point is `frontend/src/components/dashboard/dashboard-shell.tsx`.
- Incident data is already fetched by `getSafetyEvents()` in
  `frontend/src/lib/ppe-api.ts`. It returns a merged, time-sorted array of three
  incident kinds: `ViolationReport` (PPE) `|` `ZoneViolation` `|` `BehaviorIncident` (falls).
- The view switcher type lives in `frontend/src/components/dashboard/top-bar.tsx`:
  `export type DashboardView = "feeds" | "violations";`
- Icons use `lucide-react` (already a dependency). Styling is Tailwind v4.
- **Three.js is NOT yet installed.** It must be added.
- **Critical data reality — zone fields are inconsistent across incident types:**
  - `ZoneViolation` HAS `zone_id?: number` and `zone_name?: string`.
  - `ViolationReport` (PPE) does **NOT** have a zone field. It has
    `video_name?`, `track_id?`, `frame_index?`.
  - `BehaviorIncident` (falls) does **NOT** have a zone field. It has
    `camera_id?`, `video_name?`, `track_id?`.
  - Therefore incidents must be mapped to a zone via a **camera/source → zone**
    resolver, not by reading a zone field off every incident (see §4).

### Single-camera reality (important)

Right now the system streams from **one camera angle only**. Two more cameras/
zones are planned but not implemented yet. The design below MUST:

1. Work correctly and look complete with a single active camera today.
2. Route **all** current incidents (which come from that one camera) into the
   correct single active zone.
3. Render the other two zones as **"not yet monitored"** (visually dimmed,
   count = 0, not clickable into an empty log — show a "camera not connected"
   note instead).
4. Require only a one-line config change to activate a second/third zone later.

---

## 1. Dependencies

Add to `frontend`:

```bash
cd frontend
npm install three @react-three/fiber @react-three/drei
npm install --save-dev @types/three
```

Use **`@react-three/fiber`** (declarative React renderer for Three.js) — do NOT
hand-roll raw Three.js with manual `requestAnimationFrame`, because this is a
React 19 codebase and r3f is the idiomatic fit. `@react-three/drei` provides
`OrbitControls`, `Html`, and `RoundedBox` helpers used below.

---

## 2. New files to create

```
frontend/src/components/factory3d/
├── factory-layout.ts          # Blueprint coordinates + camera→zone config (SINGLE source of truth)
├── factory-3d-view.tsx        # The <Canvas> scene + orbit controls + click handling
├── zone-block.tsx             # One extruded zone mesh (hover/select/heat states)
├── zone-detail-panel.tsx      # Side panel: filtered incident log + insight for the selected zone
├── use-zone-incidents.ts      # Hook: fetch getSafetyEvents(), group by zone, compute aggregates
└── factory-3d-view.module.css # (optional) only if a style can't be expressed in Tailwind
```

One file is edited: `dashboard-shell.tsx` (+ `top-bar.tsx` for the nav tab).

---

## 3. `factory-layout.ts` — the single source of truth

This file encodes the blueprint geometry (traced to scale from the proposal's
floor-plan image) and the camera→zone mapping. Everything else reads from here.

**Coordinate system:** top-down floor plan mapped to the Three.js X/Z plane
(Y is height/up). Units are arbitrary "scene units"; keep the whole building
roughly within X∈[-6, 8], Z∈[-4, 4]. The values below are proportional to the
blueprint (Z01 top-left, Z02 bottom-left, Z03 full-height right, plus a utility
room column on the far right).

```ts
// factory-layout.ts
export type ZoneId = "Z01" | "Z02" | "Z03";

export interface ZoneDef {
  id: ZoneId;
  label: string;               // "Production area"
  /** center [x, z] on the floor plane */
  position: [number, number];
  /** [width (x), depth (z)] footprint */
  size: [number, number];
  /** base hue used for the block + heat ramp (hex) */
  color: string;
  /**
   * Source keys that belong to this zone. An incident is assigned to this zone
   * when its camera_id (number) OR video_name / source_key (string) matches an
   * entry here. This is how single-camera routing works today.
   */
  sources: {
    cameraIds?: number[];
    // match if the incident's video_name/source_key CONTAINS any of these
    videoNameIncludes?: string[];
  };
  /** false => zone shown dimmed as "not yet monitored" (no active camera) */
  active: boolean;
}

// ⚠️ SINGLE-CAMERA CONFIG:
// Today only Z01 has a live camera. Point its `sources` at the actual stream.
// The RTSP source in this project is rtsp://localhost:8554/mystream (see README),
// so videoNameIncludes: ["mystream"] catches live incidents; add the uploaded
// video filename too if you also analyze files. When camera 2 / 3 come online,
// set active: true and fill their `sources`.
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

// Static building shell (walls with gate gaps, utility rooms, restricted zone)
// traced from the blueprint. These are non-interactive decoration.
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

// Utility room outlines (electrical, maintenance, first aid, toilet) on far-right column
export const UTILITY_ROOMS: { centerZ: number; depthZ: number }[] = [
  { centerZ: -2.985, depthZ: 2.03 },
  { centerZ: -1.085, depthZ: 1.77 },
  { centerZ: 0.78, depthZ: 1.96 },
  { centerZ: 2.88, depthZ: 2.24 },
];
export const UTILITY_COLUMN = { size: [1.57, 0.4, 8] as [number, number, number], position: [6.785, 0.2, 0] as [number, number, number] };

// Restricted-zone outline inside Z03
export const RESTRICTED_ZONE = { size: [2.12, 0.05, 1.63] as [number, number, number], position: [4.32, 0.55, 1.285] as [number, number, number] };
```

> **Provenance note for the report:** these coordinates were pixel-measured from
> the blueprint PNG and converted with a fixed scale factor. It's a to-scale
> proportional reconstruction of a 2D top-down image, not a CAD survey. State
> this in the capstone writeup.

---

## 4. `use-zone-incidents.ts` — data hook

Responsibilities:

1. Fetch incidents via existing `getSafetyEvents()`.
2. Assign each incident to a `ZoneId` (or `null` if unmatched).
3. Compute per-zone aggregates (count, severity mix, top behavior, simple trend).
4. Return a lookup keyed by `ZoneId`, plus the raw grouped incidents for the
   detail panel.

**Zone assignment order (this is the core of single-camera routing):**

```
For an incident:
  1. If it is a ZoneViolation and has zone_name/zone_id ->
       match to the ZoneDef whose id or label matches (direct hit).
  2. Else look at camera_id (BehaviorIncident) ->
       find ZoneDef where sources.cameraIds includes it.
  3. Else look at video_name / source_key (PPE + fall + zone) ->
       find ZoneDef where sources.videoNameIncludes has a substring match.
  4. Else -> unassigned (null). In single-camera mode, optionally fall back to
       the single active zone so nothing is lost; make this a boolean option
       `assignUnmatchedToActiveZone` defaulting to true while only one camera exists.
```

Type sketch:

```ts
import { getSafetyEvents } from "@/lib/ppe-api";
import { ZONES, type ZoneId } from "./factory-layout";

export type AnyIncident = Awaited<ReturnType<typeof getSafetyEvents>>[number];

export interface ZoneAggregate {
  zoneId: ZoneId;
  total: number;
  ppeCount: number;
  zoneCount: number;
  fallCount: number;
  // "insight" — a single derived sentence, computed here (rule-based, no ML)
  insight: string;
  // count in the most recent 24h vs previous 24h, for a trend arrow
  trendDelta: number;
  incidents: AnyIncident[];
}

export function useZoneIncidents(options?: { assignUnmatchedToActiveZone?: boolean }): {
  aggregates: Record<ZoneId, ZoneAggregate>;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}
```

**Discriminating incident kind at runtime** (they're a union without a `kind`
field, so detect by shape):

- It's a `ZoneViolation` if it has `zone_id` or `zone_type`.
- It's a `BehaviorIncident` if it has `behavior_type`.
- Otherwise treat as `ViolationReport` (PPE) — it has `violation_type`.

**Insight rule (v1, keep simple and honest):** compose one sentence from the
aggregate, e.g.:

- If `fallCount > 0`: `"{fallCount} fall event(s) detected — highest-severity behavior this period."`
- Else if `ppeCount >= zoneCount`: `"{ppeCount} PPE violations dominate; most common: {mostCommonViolationType}."`
- Else: `"{zoneCount} zone incursions, mostly around the restricted area."`
- If `total === 0`: `"No incidents recorded."`

Do not fabricate time-of-day clustering unless the timestamps actually support
it. If you want a time insight, compute the modal hour bucket from real
`timestamp`s and only surface it when one bucket clearly dominates (e.g. holds
>40% of incidents).

---

## 5. `zone-block.tsx` — one interactive zone mesh

A single zone rendered as a slightly-extruded rounded box on the floor plane.

Props: `zone: ZoneDef`, `aggregate: ZoneAggregate`, `selected: boolean`,
`hovered`, and handlers `onHover`, `onSelect`.

Behavior:

- Position the box at `[position[0], 0.25, position[1]]`, scale
  `[size[0], 0.5, size[1]]`.
- **Heat coloring:** if `zone.active`, blend from the zone base color toward a
  warning red as incident count rises (e.g. lerp by `Math.min(total / 15, 1)`).
  If `!zone.active`, render gray at low opacity (~0.35) and skip hover/heat.
- On hover (active zones only): raise the block slightly (`y += 0.08`) and show a
  `<Html>` tooltip from drei with `label`, `total`, and a one-line insight.
- On click (active zones only): call `onSelect(zone.id)`.
- Inactive zones: on hover show tooltip `"Camera not connected — monitoring planned"`; on click do nothing (or surface a small toast).
- Add a `<Text>` (drei) label centered on each block with the zone id + label.

Accessibility: since this is a canvas, mirror the selected-zone state into the
DOM detail panel (§6) so keyboard/screen-reader users get the same information.
Also provide a plain DOM row of zone buttons above or beside the canvas as a
non-3D fallback selector.

---

## 6. `zone-detail-panel.tsx` — the incident log for a zone

This is where the "incident log" requirement is satisfied. When a zone is
selected:

- Header: zone label, total count, trend arrow (▲/▼ from `trendDelta`), and the
  derived insight sentence.
- Body: the filtered incident list for that zone. **Reuse the existing incident
  row/card rendering** rather than inventing a new one — check
  `frontend/src/components/ppe/result-panels.tsx` (`IncidentCard`) and
  `analysis-result-panel.tsx`, and render each `AnyIncident` through the closest
  existing component. If those components need a specific shape, write a tiny
  adapter, don't duplicate their markup.
- Empty state for an active zone with 0 incidents: `"No incidents recorded for this zone yet."`
- For an inactive zone: `"This zone has no connected camera yet. Monitoring is planned."` (don't show an empty incident list).

Clicking an incident's snapshot should open the existing snapshot/lightbox
behavior if one exists; otherwise link `snapshot_url` in a new tab.

---

## 7. `factory-3d-view.tsx` — the scene

Compose everything:

```tsx
"use client";
// <Canvas camera={{ position: [6, 9, 11], fov: 45 }}>
//   <ambientLight intensity={0.85} />
//   <directionalLight position={[6, 12, 8]} intensity={0.55} />
//   <FloorPad />                         // large gray base box
//   {WALLS.map(...)} {GATES.map(...)}    // static shell
//   <UtilityColumn /> {UTILITY_ROOMS.map(outline)}
//   <RestrictedZoneOutline />
//   {ZONES.map(z => <ZoneBlock zone={z} aggregate={aggregates[z.id]} ... />)}
//   <OrbitControls enablePan={false} minPolarAngle={0.3} maxPolarAngle={1.4}
//                  minDistance={9} maxDistance={28} target={[0.6, 0, 0]} />
// </Canvas>
```

Layout: a two-column flex — canvas on the left (min-height ~420px, rounded,
`bg` matching the dashboard's panel surface), `zone-detail-panel` on the right.
On narrow screens stack them.

State owned here: `selectedZoneId: ZoneId | null`, `hoveredZoneId`. Pull data
from `useZoneIncidents()`. Default `selectedZoneId` to the single active zone
(`Z01`) on first load so the panel isn't empty in single-camera mode.

Add a manual refresh button (calls the hook's `refresh()`), and optionally poll
`getSafetyEvents()` every 15–30s so the model stays current with the live stream.

**SSR caveat (Next.js):** react-three-fiber must run client-side. The component
already starts with `"use client"`. When importing it into `dashboard-shell`,
import it dynamically to avoid SSR issues:

```ts
import dynamic from "next/dynamic";
const Factory3DView = dynamic(() => import("@/components/factory3d/factory-3d-view").then(m => m.Factory3DView), { ssr: false });
```

---

## 8. Wiring into the dashboard

1. In `top-bar.tsx`: extend the view union and nav map.
   ```ts
   export type DashboardView = "feeds" | "violations" | "factory3d";
   // add to navViewByLabel, e.g. "3D Map": "factory3d"
   ```
   Add a nav item labeled "3D Map" (icon: `Boxes` or `Building2` from lucide-react).

2. In `dashboard-shell.tsx`: it already tracks the active view (`activeView` /
   `onViewChange` passed to `TopBar`). Add a branch that renders `<Factory3DView />`
   when `activeView === "factory3d"`, sibling to the existing feeds/violations
   rendering. Do not disturb the existing branches.

3. No changes to `ppe-api.ts` are required for v1 — reuse `getSafetyEvents()`.

---

## 9. Acceptance criteria (definition of done)

- [ ] `npm run dev` builds with no TypeScript errors; `npm run lint` passes.
- [ ] A "3D Map" tab appears in the top bar and renders the scene.
- [ ] The three zones render in blueprint-accurate relative positions, with the
      building shell (walls, gates, utility rooms, restricted-zone outline).
- [ ] Only `Z01` is interactive/heat-colored; `Z02` and `Z03` render dimmed with
      a "camera not connected" tooltip. (Verifiable by flipping `active` in
      `factory-layout.ts`.)
- [ ] All current incidents from the single stream are routed to `Z01` and the
      count shown on the block matches the number in the detail panel.
- [ ] Clicking `Z01` shows its incident log (reusing existing incident row
      components) plus the derived insight sentence and a trend arrow.
- [ ] Hovering a zone shows a tooltip with label + count.
- [ ] Flipping `Z02.active = true` and adding a `cameraIds`/`videoNameIncludes`
      entry activates that zone with zero further code changes.
- [ ] A non-3D DOM fallback (zone buttons + detail panel) also selects zones, so
      the feature is usable without WebGL.

---

## 10. Explicitly out of scope for v1 (note for later)

- No backend/DB changes; no new endpoints. (A future `zone_id` on PPE/behavior
  incidents would remove the need for source-matching in §4 — worth a follow-up
  ticket.)
- No per-machine/equipment interactivity — zones are the click target.
- No realistic 3D assets (glTF models); primitive boxes/cylinders only.
- Camera 2 and 3 onboarding — handled purely by editing `factory-layout.ts`
  when those streams exist.

---

## 11. Suggested commit sequence

1. `chore(frontend): add three, @react-three/fiber, @react-three/drei`
2. `feat(factory3d): add blueprint layout config and static building shell`
3. `feat(factory3d): add interactive zone blocks with hover/select`
4. `feat(factory3d): add zone-incident hook with single-camera source routing`
5. `feat(factory3d): add zone detail panel reusing existing incident rows`
6. `feat(dashboard): add "3D Map" view and wire Factory3DView`
7. `docs: note blueprint coordinate provenance and camera-onboarding steps`
