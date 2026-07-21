# Incident Analytics — Implementation Plan

> **Purpose:** Integrate the standalone `incident-analytics.jsx` dashboard (currently mock data)
> into the Smart Factory Safety Monitoring codebase with **live data**, plus two new features:
> **click an incident to view details** and **delete an incident (false positive)**.
>
> **How to execute:** Work top-to-bottom. Phases are ordered by dependency — do not start a phase
> until the ones it depends on are done and their acceptance criteria pass. Each task lists the exact
> files to touch. Run the existing test/lint gates after each phase (see §10).
>
> **Scope:** Full dashboard live, including analytics/aggregation endpoints.
>
> **Reference:** the source dashboard is the uploaded `incident-analytics.jsx`. Treat its mock
> constants (`ZONES`, `CATEGORIES`, `ZONE_STATS`, the `setInterval` generator, the 60-day random
> dataset) as things to **replace**, and its layout/styling/component structure as the **target UI**.

---

## 0. Ground truth — current state of the codebase

There is **no unified "incident" table**. Incidents live in three separate tables with uneven fields:

| Concern | Table / model | Timestamp | Has `status`? | Has `severity`? | Zone link | Evidence | Child tables |
|---|---|---|---|---|---|---|---|
| PPE | `ppe_violations` (`app/models/ppe_violation.py`) | `occurred_at` | ❌ | ❌ | ❌ only `camera_id` | `snapshot_path` (single) | `ppe_violation_subjects` (bbox, missing_equipment, conf) |
| Zone | `zone_violations` (`app/models/zone_violation.py`) | `occurred_at` | ✅ `OPEN` default | ✅ nullable | ✅ `physical_zone_id`, `camera_zone_view_id`, `zone_name`, `zone_type` | `snapshot_path` | — |
| Behavior | `behavior_incidents` (`app/models/behavior_incident.py`) | `started_at` / `ended_at` | ✅ `NEW/REVIEWED/RESOLVED/FALSE_POSITIVE` | ✅ `LOW/MEDIUM/HIGH/CRITICAL` | ❌ only `camera_id` | `behavior_evidence` table (MinIO `object_key`) | `behavior_incident_subjects` (bbox, keypoints, features) |

**Important:** `zone_violations.zone_type` (`RESTRICTED` / `SLIPPERY` / `WALKWAY`) is the **violation
type**, *not* the physical area. The physical area the dashboard buckets by ("Production Floor",
"Warehouse Intake", …) is the **camera's location** = its home zone (see Phase 1).

### What already exists (reuse — do not rebuild)

- **Merged feed:** `frontend/src/lib/ppe-api.ts → getSafetyEvents()` already fetches PPE + zone +
  behavior and returns them sorted by timestamp. This is the Live Feed source.
- **List endpoints:** `GET /violations`, `GET /zone-violations`, `GET /behavior-incidents`.
- **Behavior detail:** `GET /behavior-incidents/{id}` already exists (`app/routers/fall_detection.py`).
- **Deletes:** `DELETE /violations/{id}` (PPE) and `DELETE /zone-violations/{id}` (zone) exist and are
  wired on the frontend (`deleteViolation`). `DELETE /violations` bulk-deletes across all three types.
- **Camera resolution:** `PPEViolationService._resolve_camera_id` (`app/services/ppe_violation_service.py`)
  does **lazy get-or-create** of a `Camera` from the stream `source_key`, so every incident gets a
  `camera_id` (auto-created camera if needed). Zone/behavior services follow the same pattern.

### What's missing (this plan builds it)

- A camera → zone link (`cameras.home_zone_id`) + a way to assign it.
- A unified/normalized incident read layer.
- Detail endpoints for PPE and zone; single-delete for behavior.
- Analytics/aggregation endpoints (trend, summary, compare).
- Frontend page wired to all of the above, replacing mock data.

---

## 1. Locked decisions & assumptions

**Locked (from product owner):**
1. Build the **full** dashboard, including analytics endpoints.
2. False positive = **hard delete** (remove the row permanently). No soft-delete/status needed.
3. Zone attribution = **camera's home zone**: every incident rolls up to the zone of the camera that
   recorded it.

**Assumptions baked in — `⚠️ CONFIRM` with owner, but proceed with these defaults if unanswered:**
- **`⚠️ CONFIRM` PPE severity rule** (PPE has no severity; derive one):
  - missing helmet **and** vest → `High`
  - missing helmet only **or** vest only → `Medium`
  - proximity violation → `Low`
  - PPE never `Critical` by default.
- **Severity label casing:** standardize on **Title case** (`Critical/High/Medium/Low`) everywhere.
  Behavior stores uppercase (`HIGH`) — map on read.
- **`⚠️ CONFIRM` KPIs without a data source:** *PPE Compliance %* and *People On Shift* have no
  backing data today. Default: render as `—` / hidden with a `TODO`, and compute only *Open Incidents*
  and *Active Cameras* (which are derivable). Do **not** fabricate values.
- **Per-incident status** is dropped from the UI (PPE has none; hard-delete removes the "resolved vs
  open" lifecycle need). If the owner wants status shown, revisit — it's non-blocking.
- **Zone colors** are assigned **client-side** (real `physical_zones` have name + type + `is_active`
  but no color).

---

## 2. Target — unified incident contract

Every read path (feed, detail, analytics) normalizes the three tables into one shape.
**IDs collide across tables**, so the client must key on `(category, id)`.

```
UnifiedIncident:
  id: int
  category: "ppe" | "zone" | "behavior"
  type: str            # PPE violation_type | zone zone_type | behavior behavior_type (display label)
  severity: "Critical" | "High" | "Medium" | "Low"
  timestamp: datetime  # ppe/zone occurred_at ; behavior started_at
  camera_id: int | null
  zone_id: int | null       # resolved home zone (physical_zones.id) ; null => "Unassigned"
  zone_name: str            # physical zone name ; "Unassigned" when null
  camera_label: str         # e.g. cameras.name or source_key
  snapshot_url: str | null  # resolved absolute/servable URL
```

**Normalization rules:**
- **zone** — join `incident.camera_id → cameras.home_zone_id → physical_zones`. `NULL` → `"Unassigned"`.
  (Zone incidents also carry their own `physical_zone_id`; still bucket by the camera's home zone for
  consistency across all three categories, per the locked decision.)
- **severity** — behavior: uppercase→Title. zone: use stored `severity`, Title-case, default `Medium`
  if null. ppe: derive per the rule in §1.
- **type** — pass through each table's type field as the display label (matches the mock's
  `CATEGORIES[*].types`).
- **snapshot** — ppe/zone `snapshot_path`; behavior newest `behavior_evidence.object_key`. Run through
  the existing URL resolver (`toAbsoluteUrl` on the client; server returns a servable path/`/snapshots`
  mount or MinIO URL as today).
- **timestamp** — map to one field; sort DESC for feeds.

**Build it once, in one place.** Recommended: a Postgres **view** `incident_unified` (fast SQL
group-bys for analytics). If you prefer to stay in Python, a repository method that UNIONs + normalizes
is acceptable but will be slower for aggregation. Whichever you pick, all of §3–§5 read through it.

---

## 3. Phase 1 — Camera → home zone (prerequisite; nothing zone-bucketed works without it)

**Goal:** each camera belongs to one physical zone; incidents inherit it.

**Backend**
- [ ] Add `home_zone_id: int | None` FK to `cameras` → `physical_zones.id`, `ondelete="SET NULL"`,
      nullable, indexed. File: `backend/app/models/camera.py`.
- [ ] Migration: since the project initializes tables via `python -m app.db.init_db` (no Alembic seen),
      add the column there / in the schema init. Files: `backend/app/db/init_db.py`,
      `backend/docs/database_schema.dbml` (keep the approved schema doc in sync).
- [ ] Camera service + repo: add `set_home_zone(camera_id, zone_id)` and include `home_zone_id` in
      camera reads. Files: `backend/app/services/camera_service.py`,
      `backend/app/repositories/camera_repository.py`.
- [ ] Endpoints: `GET /cameras` (list with `home_zone_id`), `PUT /cameras/{id}/home-zone` (body
      `{ zone_id }`). New router `backend/app/routers/cameras.py`; register in `backend/app/main.py`.
- [ ] Ensure auto-created cameras (`_resolve_camera_id`) remain valid with `home_zone_id = NULL`
      (they will → they land in "Unassigned").

**Frontend**
- [ ] The camera-config panel currently writes only to `localStorage` (`DEFAULT_CAMERAS`,
      `handleSaveCameraConfig` in `frontend/src/components/dashboard/dashboard-shell.tsx`). Bridge it to
      the backend: bind each stream to its backend camera by `source_key`, and let the user pick a home
      zone per camera, persisted via `PUT /cameras/{id}/home-zone`.
- [ ] Add API helpers `getCameras()`, `setCameraHomeZone(id, zoneId)` in
      `frontend/src/lib/ppe-api.ts`.

**Acceptance**
- A camera can be assigned a zone; the assignment survives a page reload (persisted in DB, not
  localStorage). Incidents from that camera resolve to that zone in a manual DB/query check.

---

## 4. Phase 2 — Unified incident layer

**Goal:** one normalized read layer per §2.

**Backend**
- [ ] Create `incident_unified` view (or Python union) implementing the §2 shape + normalization,
      including the `cameras.home_zone_id → physical_zones` join and the PPE severity derivation.
- [ ] Add a schema `UnifiedIncidentRead` (`backend/app/schemas/`), and a repository/service to query it
      with filters: `zone_id`, `category`, `severity`, `date_from`, `date_to`, `limit`.
- [ ] Unit-test the normalization (severity casing, PPE derivation, Unassigned fallback).

**Acceptance**
- Querying the layer returns rows from all three tables in one shape, correctly zoned and severity-normalized.

---

## 5. Phase 3 — Detail view (Feature 1: click to view details)

**Goal:** clicking a feed row (and panel items) opens a detail modal.

**Backend — add the two missing detail endpoints (behavior's already exists):**
- [ ] `GET /violations/{id}` → PPE detail **including `subjects`** (bbox, missing_equipment, confidence).
      File: `backend/app/routers/detection.py` + `ppe_violation_service`/repo (add `get_by_id`).
- [ ] `GET /zone-violations/{id}` → zone detail with zone metadata. Same router + zone service/repo.
- [ ] Reuse `GET /behavior-incidents/{id}` as-is.

**Frontend**
- [ ] API helpers `getViolation(id)`, `getZoneViolation(id)`, `getBehaviorIncident(id)` in `ppe-api.ts`
      (behavior list helper already exists — add the single-fetch).
- [ ] A shared `IncidentDetailModal` keyed by `(category, id)` that calls the right fetch and renders:
      snapshot image (via `toAbsoluteUrl` / evidence), timestamp, zone, camera, severity, type, and
      per-category fields (PPE: subjects/missing equipment; zone: zone_name/type; behavior:
      evidence + keypoints/metadata). It also hosts the delete button (Phase 4).
- [ ] Add `onClick` + `cursor-pointer` to feed rows in the dashboard (the `filteredFeed.map(...)` block
      of `incident-analytics.jsx`, once ported). Open the modal with that row's `(category, id)`.

**Acceptance**
- Clicking any incident opens a modal with its real details and snapshot. All three categories render
  without errors, including incidents with a missing snapshot (graceful placeholder).

---

## 6. Phase 4 — Delete / false positive (Feature 2: hard delete)

**Goal:** user can permanently delete a reviewed false positive.

**Backend — add the one missing route:**
- [ ] `DELETE /behavior-incidents/{id}` (single). Add `delete(incident_id)` to
      `backend/app/repositories/behavior_incident_repository.py` (only `delete_all` exists today) and a
      service method; wire the route in `backend/app/routers/fall_detection.py`. Cascade to
      `behavior_incident_subjects` + `behavior_evidence` (relationships already `cascade_delete=True`).
- [ ] PPE (`DELETE /violations/{id}`) and zone (`DELETE /zone-violations/{id}`) already exist — reuse.

**Frontend**
- [ ] API helpers: `deleteViolation(id)` exists; add `deleteZoneViolation(id)` and
      `deleteBehaviorIncident(id)`; add a `deleteIncident(category, id)` dispatcher.
- [ ] Delete action on both the feed row and the detail modal → confirm dialog (copy must state the
      deletion is **permanent**; a "Mark as false positive" label is fine as long as confirm explains it
      removes the record) → optimistic removal from the feed + close modal + refetch analytics.

**Acceptance**
- Deleting an incident removes it from the feed immediately, it does not reappear after refresh, and the
  row is gone from the DB (verify for each of the three categories).

---

## 7. Phase 5 — Analytics endpoints

**Goal:** back the charts with real aggregation over the unified layer. All read through Phase 2.

**Backend — suggested endpoint set (new router `backend/app/routers/analytics.py`):**
- [ ] `GET /analytics/summary?range=24H|7D|30D&zone={id|all}` → powers **KPIs**, **Zone Pulse** totals,
      **Severity & Type Breakdown**, **Zone Comparison**. Returns: per-zone totals, per-severity counts,
      per-type counts, grand total, and "active zones" (zones with an incident in the last N minutes).
- [ ] `GET /analytics/trend?range=&bucket=hour|day&zone=` → time-bucketed series **stacked by zone**
      (24H⇒hourly, 7D/30D⇒daily). Shape mirrors the mock's `trendData` rows: `{ date, <zoneId>: n, … }`.
- [ ] `GET /analytics/compare?mode=week|month&zone=` → current window vs prior window: totals, daily
      trajectory (`current`/`prior`), and per-severity split (feeds "Period Comparison").
- [ ] Ensure indexes on the timestamp + `camera_id` columns are used (they're already indexed on all
      three tables — verify the view doesn't defeat them).

**Acceptance**
- Each endpoint returns real counts that reconcile with row counts in the DB for a known range/zone.
  Switching `range`/`zone`/`mode` changes the numbers correctly.

---

## 8. Phase 6 — Frontend wiring (replace the mock)

**Goal:** port `incident-analytics.jsx` into the app and swap every mock source for real data.

- [ ] Add as a new route/page (e.g. `frontend/src/app/analytics/page.tsx`) in the existing Next.js app.
- [ ] Replace inlined mock constants with real sources: use `frontend/src/components/dashboard/data.ts`
      and `frontend/src/types/*` for zones/categories/severity; reuse shared components
      (`metric-card.tsx`, `top-bar.tsx`, `zone-sidebar.tsx`) instead of the copies embedded in the mock.
- [ ] Delete the mock engines: `mulberry32`/`rand`, `DAILY_DATA_60`, `HOURLY_DATA`, `ZONE_STATS`, the
      `makeIncident` `setInterval` feed, and `initialFeed`.
- [ ] Wire panels to endpoints:
  - **Live Feed** ← `getSafetyEvents()` (poll on an interval, e.g. every few seconds). *(Later refinement
    only: the WebSocket stream in `useLiveStream.ts` emits `violation`/`zone_violation`/`behavior_incident`
    events, but those are per-active-stream, not global — polling is the correct first implementation.)*
  - **KPIs / Zone Pulse / Severity & Type / Zone Comparison** ← `/analytics/summary`.
  - **Incident Volume Trend** ← `/analytics/trend`.
  - **Period Comparison** ← `/analytics/compare`.
- [ ] Keep the zone filter (`selectedZone`) and `timeRange` state, but have them drive query params to
      the analytics endpoints instead of filtering mock arrays.
- [ ] Assign zone colors client-side (map physical zone id/name → color).

**Acceptance**
- The page renders entirely from live endpoints; no mock arrays remain; zone filter + time range
  re-query and update all panels; feed updates on its poll interval.

---

## 9. Phase 7 — KPIs

- [ ] Compute the derivable KPIs: **Open Incidents** (count, optionally critical+high) and
      **Active Cameras** (`cameras.is_active` and/or cameras with recent incidents) from real data.
- [ ] `⚠️ CONFIRM` **PPE Compliance %** and **People On Shift**: render `—`/hidden with a `TODO`
      unless the owner provides a data source. Do not fabricate.

**Acceptance**
- Real KPIs reconcile with the DB; placeholder KPIs are clearly non-fabricated.

---

## 10. Testing & verification (run after every phase)

- [ ] Backend: `cd backend && python -m pytest` and `python -m ruff check app tests`
      (baseline per `REFACTOR_NOTES.md` is 118 passed / 8 pre-existing failures — don't regress it).
- [ ] Frontend: `cd frontend && npm run build` (must stay clean) and eslint.
- [ ] Add tests: normalization unit tests (Phase 2), detail + delete route tests per category
      (Phases 3–4), analytics count-reconciliation tests (Phase 5). Test dirs: `backend/tests/…`
      mirroring existing structure (`services/`, routers tests).
- [ ] Manual E2E: assign a camera to a zone → generate/seed incidents on that camera → confirm they
      appear in the right zone bucket, open detail, delete, and see analytics update.

---

## 11. Risks & gotchas

- **localStorage vs backend cameras:** the frontend camera config is currently localStorage-only.
  Zone attribution will silently do nothing unless Phase 1 bridges each stream to a backend camera and
  persists `home_zone_id`. This is the #1 risk.
- **Unassigned bucket:** auto-created cameras (lazy `_resolve_camera_id`) have no zone. The dashboard
  must show an "Unassigned" zone or those incidents vanish from zone-bucketed views.
- **ID collisions:** the same integer id exists across all three tables. Every client key, detail fetch,
  and delete call must carry `category` alongside `id`.
- **Severity/type inconsistency:** normalize in exactly one place (Phase 2). Don't scatter casing/derivation
  logic across endpoints.
- **Snapshot sources differ:** ppe/zone use `snapshot_path`; behavior uses MinIO evidence `object_key`.
  Resolve both to servable URLs; handle missing snapshots gracefully.
- **Do not cross the refactor boundary:** per `REFACTOR_NOTES.md`, `app/services/ppe/` and
  `zone_service.py` must not import each other; anything needing both belongs in
  `app/services/video_pipeline/`. New analytics code reads models/repos, so keep it in its own
  service/router and avoid reaching into those detection internals.

---

## 12. File-touch index (quick reference)

**Backend — new**
- `app/routers/cameras.py`, `app/routers/analytics.py`
- `incident_unified` view (migration/init) + `app/schemas/*` for unified/analytics reads

**Backend — edit**
- `app/models/camera.py` (+`home_zone_id`), `app/db/init_db.py`, `backend/docs/database_schema.dbml`
- `app/services/camera_service.py`, `app/repositories/camera_repository.py`
- `app/routers/detection.py` (+`GET /violations/{id}`, `GET /zone-violations/{id}`)
- `app/routers/fall_detection.py` (+`DELETE /behavior-incidents/{id}`)
- `app/repositories/behavior_incident_repository.py` (+single `delete`), behavior service
- `app/services/ppe_violation_service.py`, `zone_violation_service.py` (add `get_by_id`)
- `app/main.py` (register new routers)

**Frontend — new**
- `src/app/analytics/page.tsx` (ported dashboard)
- `IncidentDetailModal` component

**Frontend — edit**
- `src/lib/ppe-api.ts` (add: cameras helpers, single-incident fetches, zone/behavior delete helpers,
  analytics fetches; reuse `getSafetyEvents`, `deleteViolation`, `API_URL`, `toAbsoluteUrl`)
- `src/components/dashboard/dashboard-shell.tsx` (camera-config → backend zone assignment)
- reuse `src/components/dashboard/{metric-card,top-bar,zone-sidebar}.tsx`, `data.ts`, `types/*`

---

### Execution order (TL;DR)
**1** camera→home-zone (schema + assignment) → **2** unified layer → **3** detail endpoints + modal →
**4** behavior single-delete + delete UI → **5** analytics endpoints → **6** port page & replace mock →
**7** KPIs. Test/lint gates after each. Confirm the two `⚠️ CONFIRM` items with the owner when reached;
proceed with the documented defaults if no answer.
