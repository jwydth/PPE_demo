# What changed since the last codebase overview

**Baseline:** `docs/CODEBASE_OVERVIEW.md`, whose narrative is written "as of
2026-08-10" and which was last committed in `e62f094` (2026-08-14).
**Head:** `b088657` (2026-09-04), branch `develop`, working tree clean.

**Range:** `git diff e62f094..HEAD` — 10 commits, 105 files, **+8,977 / −844**.

This is a delta document, not a replacement. It records what moved, why, and
which files to read. The baseline overview is still correct for everything not
listed here — but the sections flagged in §10 have drifted and should be
re-read against the code before you rely on them.

> Sourcing note: the "why" for each change comes from the commit messages, which
> in this range are unusually detailed. Every structural claim (new file, new
> endpoint, new column, new setting, renamed default) was re-verified against the
> code at `b088657`. Two commits (`00abe30` "UI modifications", `89e2157`
> "Incident panel update") have no body, so their entries below are derived from
> their diffs alone.

---

## Commits in range

| Commit | Date | Subject |
| --- | --- | --- |
| `b088657` | 2026-09-04 | [FIX] Exempt janitors from slippery-zone labels in the tracking overlay |
| `fe94876` | 2026-08-30 | [FIX] Suggestion lifecycle, camera identity, and incident panel feedback |
| `c7653a4` | 2026-08-30 | [FIX] Correct incident zone attribution, filtering, and dashboard chrome |
| `9591860` | 2026-08-26 | *(subject line malformed — see §9)* |
| `96513a7` | 2026-08-24 | [ADD] Filter the Live Incident Panel by severity, type, and time |
| `ae9803d` | 2026-08-22 | [FIX] Eliminate black camera feed on dashboard reload |
| `89e2157` | 2026-08-16 | [UPDATE] Incident panel update |
| `00abe30` | 2026-08-15 | [ADD] UI modifications |
| `53247bd` | 2026-08-15 | [FIX] AI-disclaimer badge + VI/EN reports + camera/severity filters + zone sidebar sync |
| `bfb2450` | 2026-08-14 | [FIX] Emit frame events for behavior-only streams, fix WS settings race |

---

## 1. Database schema — three new columns, no migration framework

The baseline overview's §5 (data model) is now incomplete.

### `area_zone_id` on all three incident tables

Added to `ppe_violations`, `zone_violations`, and `behavior_incidents`
(`backend/app/models/{ppe_violation,zone_violation,behavior_incident}.py`), all
declared `FK -> physical_zones.id`, `ON DELETE SET NULL`, indexed, nullable.

**Why it exists:** incidents previously resolved their zone at *read* time by
following `camera_id -> cameras.home_zone_id`. Since `camera_id` is
`ON DELETE SET NULL`, deleting a camera dumped its entire recorded history into
"Unassigned", and reassigning a camera's home zone retroactively rewrote the
zone of every incident that predated the move. The zone is now frozen onto the
row at write time, with a fallback to the camera's current home zone for older
rows.

Do not confuse it with `zone_violations.physical_zone_id`, which is the *drawn*
zone (RESTRICTED / WALKWAY / SLIPPERY / IGNORE) a person entered. `area_zone_id`
is the AREA zone the camera belonged to.

### `severity` now actually written for zone violations

`zone_violations.severity` was nullable and **nothing on the write path ever set
it**, so every incursion ever recorded stored `NULL`. The read layer coerced
`NULL` to "Medium", and the dashboard's Open Incidents figure counts
Critical + High — so a RESTRICTED-zone incursion was detected, stored, and then
never appeared in the number an operator actually watches. The write path now
derives it (`incident_normalization.default_zone_severity`,
`zone_violation_service.py`).

### `language` on `report_schedules`

`String(8)`, `NOT NULL`, `server_default="en"` — `"en" | "vi"`.

### Migration scripts (this project has no Alembic)

Schema is plain `SQLModel.metadata.create_all()`, which creates whole missing
tables and never alters an existing one. Three new **idempotent, safe to re-run**
patch scripts follow the existing `scripts/apply_new_indexes.py` pattern:

- `backend/scripts/add_incident_area_zone.py` — adds the column to all three
  tables **and** backfills it, including re-attaching rows orphaned by a camera
  deletion.
- `backend/scripts/backfill_zone_violation_severity.py` — fills the historical
  `NULL` severities using the same mapping the write path now uses, so history
  and new incidents agree. Rows that already carry a severity are left alone.
- `backend/scripts/add_report_language_column.py` — adds `report_schedules.language`.

**Anyone running an existing database must run all three.** A fresh database
created after these changes already has the columns via `create_all()`.

---

## 2. New API surface

Two new endpoints, plus new query parameters. Baseline overview §8 is stale.

| Endpoint | File | Notes |
| --- | --- | --- |
| `GET /safety-events` | `routers/detection.py` | Server-side paginated, filtered merged incident feed. Returns `SafetyEventsPage`. |
| `GET /stream-snapshot?video_name=` | `routers/streaming.py` | JPEG poster frame of the latest composed frame. **404s past `ANNOTATED_SNAPSHOT_MAX_AGE_SECONDS`** rather than presenting stale footage as the current view of the floor. |

New query params on `GET /analytics/incidents`: `camera_id`, `severity`.
New field on `AnalyticsSummary`: `live_cameras` (defaults to `0`).

### `active_cameras` vs `live_cameras` — deliberately different numbers

`schemas/analytics.py` now documents these as two distinct figures, and they are
not interchangeable:

- **`active_cameras`** — cameras that recorded an incident inside the requested
  `range`. Historical. This is what the PDF report's coverage narrative wants.
- **`live_cameras`** — cameras whose source is being streamed *right now*.
  Point-in-time, independent of `range`. This is what the dashboard KPI shows.
  Defaults to `0` so report-side constructors, describing a past period where
  "streaming right now" is meaningless, don't have to supply it.

### Analytics handlers are now sync `def`, not `async def`

`routers/analytics.py` deliberately declares its handlers `def`.
`AnalyticsService` does synchronous SQLAlchemy work and aggregates in Python; as
`async def` that ran directly on the asyncio event loop and blocked the whole
server — a single `/analytics/summary` call pushed `/health` from a 4ms median to
**1549ms**, and every camera WebSocket stalled with it. FastAPI runs `def`
handlers in its threadpool instead. **Do not "fix" these back to `async def`.**

---

## 3. New backend modules

| File | Purpose |
| --- | --- |
| `app/services/incident_feed_service.py` | Server-side pagination for the merged feed: one `UNION ALL` over a `(category, id, timestamp)` projection across the three incident tables, with `ORDER BY`/`LIMIT`/`OFFSET` applied to the union, then hydrating only that page's rows. `id DESC` is part of the sort key so same-timestamp bursts have a stable order instead of appearing on two pages or none. |
| `app/services/incident_filters.py` | The severity rules expressed **as SQL**, so filtering happens before `LIMIT`/`OFFSET`. |
| `app/repositories/incident_filters.py` | Shared filter predicates for the repositories. |
| `app/schemas/incident_feed.py` | `SafetyEventsPage` and friends. |
| `app/services/live_streams.py` | Process-wide registry of sources currently streaming (`claim_stream` / `release_stream` / `active_stream_sources`). Lives in services, not the router, so analytics can read live-stream state without importing a router. In-process only — under multi-worker uvicorn each worker sees only its own connections. |
| `app/services/reporting/i18n.py` | Static EN/VI translation table for report *chrome* only. |

### The severity rule now has three encodings — keep them in sync

Severity is not a single stored column: PPE derives it from `violation_type`
text, zone reads a nullable free-text column, behavior reads an uppercase column
with a different default. That rule now exists in **three** places:

1. `app/services/incident_normalization.py` — the read layer (pre-existing).
2. `app/services/incident_filters.py` — the same rule as SQL (new).
3. `frontend/src/lib/incident-severity.ts` — a client-side mirror (new).

`backend/tests/services/test_incident_filters.py` asserts that for every stored
row the SQL predicate selects exactly the rows the normalizer labels with that
severity. **Change one, change all three.**

The actual derivation rules — which are not written down anywhere else — are in
**[Appendix A](#appendix-a--how-severity-is-derived)**.

### What `i18n.py` deliberately does *not* translate

Incident type labels, zone names, and camera names stay untranslated — they come
from `incident_normalization.py` and user-configured data. Severity and category
words get their own small report-local maps in `pdf_renderer.py` /
`report_data.py`, since they are single words reused across table cells, chart
legends, and canvas-drawn text rather than one-shot `Paragraph` strings. Unknown
key or unknown language both fall back to English rather than raising, so a
missing translation degrades gracefully instead of crashing PDF generation.

---

## 4. Streaming: warm-start, model pool, and the reload black screen

Baseline overview §9 needs re-reading — the lifecycle changed materially.

### Grace windows (`ae9803d`)

`CameraFrameHub` used to stop its `cv2.VideoCapture` the instant its last
subscriber left, and `AnnotatedStreamPublisher` tore down its
ffmpeg -> mediamtx -> HLS chain with it. A page reload therefore paid a full RTSP
handshake plus an HLS cold start every time, during which the manifest 404s and
the player can only sit and retry.

Both now stay warm for a configurable grace period and are reattached to by the
next connection (`FrameHubRegistry`, `acquire_publisher`). **Shutdown still tears
everything down immediately, so no ffmpeg is orphaned.**

### New settings (`backend/app/core/config.py`)

| Setting | Default | Meaning |
| --- | --- | --- |
| `MAX_CONCURRENT_STREAMS` | **1 -> 8** | Now a *pure safety ceiling*. Instances load on demand, so raising it reserves no VRAM. Also the max number of cameras that can stream at once. |
| `MODEL_POOL_PREWARM` | `3` | How many instances are built and warmed at startup. Keep >= the number of cameras normally streaming at once. |
| `FRAME_HUB_IDLE_GRACE_SECONDS` | `60.0` | How long a camera's capture thread stays connected after its last subscriber leaves. `0` restores the old immediate-teardown behavior. At 8s a slightly-slow reload still fell off the edge and paid a full cold start. |
| `ANNOTATED_PUBLISHER_IDLE_GRACE_SECONDS` | `60.0` | Same, for the ffmpeg/HLS publisher. |
| `ANNOTATED_SNAPSHOT_MAX_AGE_SECONDS` | `10.0` | Past this age `/stream-snapshot` 404s instead of serving a stale frame. |

`FALL_BEHAVIOR_MIN_CONFIDENCE` moved `0.50 -> 0.70`.

### Model pool now grows on demand

`app/services/ppe/detector.py` previously sized the whole pool at startup, which
made `MAX_CONCURRENT_STREAMS` a number that had to be hand-raised for every
camera added: set it too low and the extra camera's stream silently retried
forever behind a busy pool; set it high enough for future cameras and every
unused slot still cost VRAM at boot. The pool now tracks a ceiling
(`_pool_size`) separately from what actually exists (`_pool_created`) and grows
in `_grow_pool()`.

### Postgres connect timeout

`app/db/session.py` now passes `connect_timeout=5` for `postgresql` URLs. libpq
blocks in `connect()` indefinitely by default; since DB-backed endpoints are
`async def` calling sync SQLAlchemy, that block landed on the event loop and
froze the whole server — **including already-running camera WebSockets**. Failing
fast turns an unrecoverable hang into a 503.

### Other pipeline fixes

- **Dwell timing** (`c7653a4`): dwell is now credited per *processed* frame rather
  than per *stride* frame. A 1.5s threshold was running at a third of real time on
  a 24fps source.
- **IGNORE-zone crash** (`c7653a4`): frame dimensions are now read *before* the
  IGNORE-zone filter, which crashed on the first frame of any camera with a zone
  configured. Covered by the new `backend/tests/test_ignore_zone_filter.py`.
- **Behavior silence** (`c7653a4`): a source below the classifier's canonical FPS
  produced nothing with no error anywhere. The reason is now surfaced on the
  worker, and the real loader exception is carried instead of asserting a missing
  scikit-learn.
- **Behavior-only streams** (`bfb2450`): a `"frame"` event is now yielded when PPE
  and zone are both off, so behavior-only cameras stop freezing on the last image.
- **WS settings race** (`bfb2450`): `update_settings` is sent on `ws.onopen`, not
  only via the sync effect, so connections opened before per-camera feature config
  loads don't run with stale ppe/zone/fall flags.
- **Overlay aspect ratio** (`bfb2450`): `tracking_overlay` now defaults to 16:9
  instead of 1000x1000.

---

## 5. Zone logic

### Janitor exemption was incomplete (`b088657`)

The janitor exemption for SLIPPERY zones was applied to the two paths that
*persist* a violation (live dwell counter, retroactive foot-history check) but
not to the loop that builds the tracking-overlay payload. A janitor mopping a
slippery area therefore got a red bounding box reading "Zone: Slippery area —
Slippery" for an incident that was never recorded. Fixed at the single source in
`video_pipeline/__init__.py` — the overlay `zone_type` feeds both the
server-drawn label in `annotated_stream` and the client-drawn one in
`video-tracking-overlay`, so no frontend change was needed.

RESTRICTED zones are untouched: janitors are still flagged there, and still
flagged for PPE they are actually missing.

### Auto-zone suggestion lifecycle (`fe94876`)

Suggestions were decided from an in-memory `SignZoneRegistry` that died with the
WebSocket and was never told when the user accepted or deleted a zone. It got
this wrong in both directions: an accepted zone was re-suggested once a looped
video brought the sign back around (the emitted track is pruned after ~6s
off-screen, and a drifting centre mints a new `suggestion_id`), while a deleted
zone stayed suppressed for the rest of the connection.

Suggestions are now decided from the **saved zones**
(`auto_zone.is_already_zoned()` — dropped when a zone of the same type already
covers most of the suggestion's footprint), so both actions take effect on the
next sign frame and survive a reconnect. `dismissed` is split out from `emitted`,
so a dismissal stays final while a covering zone merely *parks* the suggestion.

### Camera identity normalization (`fe94876`)

Saving a zone matched cameras on the raw `video_name` while every other service
normalizes first — so a zone saved for `rtsp://localhost:8554/stream2` would not
find the camera at `rtsp://127.0.0.1:8554/stream2`, minting a duplicate row for
one physical stream. The sibling lookup had the same flaw with a quieter symptom
(a spurious "Camera not found"). Also: clearing saved zones never sent
`reload_zones`, so the running pipeline kept enforcing deleted polygons until the
next reconnect.

---

## 6. Frontend

Baseline overview §16–§22 have drifted. `dashboard-shell.tsx` alone changed by
~1,221 lines.

### New files

| File | Purpose |
| --- | --- |
| `src/lib/incident-severity.ts` | Client mirror of the backend severity rules — used by incident cards, the detail modal, and the filter chips so the three don't drift. |
| `src/hooks/useModalDismiss.ts` | The keyboard contract every modal owes its users: Escape closes, focus starts inside, Tab can't walk out, focus returns to the opener. Written once because both dialogs had `role="dialog"` and `aria-modal` but no Escape handler and no focus management — the only way out was clicking the backdrop, which stranded keyboard users while Tab quietly walked the page behind the overlay. |
| `src/components/dashboard/monitoring-status.tsx` | Live/idle/down/pending status badge for the header. |
| `src/components/dashboard/confirm-delete-all-dialog.tsx` | Replaces a bare `window.confirm` for "delete every logged incident" — requires typing `DELETE`, and states the record count. The browser's default dialog put its confirm button under the cursor, so a mis-aimed double-click near Refresh was two clicks from an empty incident store. |

**Removed:** `src/components/dashboard/icon-button.tsx`.

### Incident panel filtering (`96513a7`)

A collapsible filter bar: multi-select Type (PPE/Zone/Behavior), multi-select
Severity (Critical/High/Medium/Low), and a time range (24h/7d/30d/custom).
Collapsed by default; remembers the visitor's expand/collapse choice in
`localStorage`. Filtering moved **from the browser into the database** — the old
client-side approach filtered over the newest `FEED_LIMIT` rows, so selecting a
zone whose incidents fell outside that window showed an empty feed while the
chart still counted them. Each incident card now carries a severity badge.

New in `src/lib/ppe-api.ts`: `SafetyEventsPage`, `SafetyEventsFilters`,
`getSafetyEventsPage()`.

### Panel feedback honesty (`fe94876`)

The paging banner was gated on `isFetching` alone, so every refresh flashed a
green "Fetching results · page N" for the two or three milliseconds a request
took — announcing a page change that wasn't happening. Now gated on the rendered
page still being the one being left. The refresh icon had the opposite problem —
bound to the same near-instant flag, it produced no visible feedback at all. It
now confirms with a checkmark and an "Updated …" label, and shows a spinner only
once a request passes **300ms**, so it says "wait" only when there is one.

### Camera count reconciliation (`fe94876`)

Active Cameras counted rows in the `cameras` table while Configure cameras
rendered a `localStorage` list, so a row the backend created on its own appeared
in the count but nowhere in the UI. Registered cameras are now adopted into the
configured list (listed, not streamed) so the two agree by construction, and a
stray row is removable from the screen that shows it.

### Reload black screen, frontend half (`ae9803d`)

- `dashboard-shell.tsx` seeds the cached camera list in a **layout effect** so
  streaming starts on the first painted frame, with the backend reconcile still
  running afterwards to self-heal drift. A `useState` initialiser was tried first
  and rejected: the component is SSR'd, so reading `localStorage` during render
  tripped a hydration mismatch.
- `ll-hls-video.tsx` replaced a flat 750ms manifest retry with an escalating
  backoff from 150ms, reset on a successful manifest parse.
- `<video poster>` is wired to the new `/stream-snapshot` endpoint. On 404 the
  player falls back to its previous black background, so this cannot regress a
  reload.

### Matrix view (`96513a7`)

`useLiveStream` now answers "is this stream being viewed" by matching the RTSP
source key directly (`selectedCameraSources`) instead of looking the camera up by
id in a separate `cameras` array, which could miss or mismatch.

### Header rework (`c7653a4`)

Removed the handler-less action buttons and chevron menu, made the nav reachable
below `lg` with `aria-current`, merged the KPI row into the title bar, and gave
the status panels a dark variant (`PanelSurface = "light" | "dark"`) for use
inside the camera panel. `result-panels.tsx` now exports `EmptyState`,
`LoadingState`, and `ErrorState`.

Also: saved zones and sign suggestions are now keyed by the camera they belong to
(they leaked across cameras during a switch).

### AI disclaimer + VI/EN reports (`53247bd`)

- A bottom-left badge on every live/annotated camera tile (matrix and single
  view) stating the footage is AI-generated demo content, not real De Heus
  factory video. Gated on the existing `annotated` prop, so uploaded-file review
  (real footage) never shows it.
- A language selector for PDF/email reports, plus a fix to the incident table's
  column widths, which silently overflowed for wider labels ("Nghiêm Trọng",
  "Trung Bình", English "Behavior") since Severity/Category were plain,
  non-wrapping table cells.
- Zone sidebar: `cameraCountsByZone` counted every camera assigned to a zone
  regardless of its Active checkbox, so a deactivated camera's zone still showed
  a green dot and a stale camera count. Now only active cameras are counted.
- A "Behavior detection unavailable" banner surfaces in the dashboard
  (`bfb2450`).

---

## 7. Weights

- **Deleted:** `backend/weights/best_behavior_model.joblib` (11.5 MB).
- **Added:** `backend/weights/behavior.joblib.truncated.bak` (659 KB).
- `FALL_BEHAVIOR_MODEL_PATH` default renamed `weights/best_behavior_model.joblib`
  -> `weights/behavior.joblib`.
- `.gitignore` now ignores `backend/weights/*.joblib`.

**Consequence:** the real behavior model is no longer tracked in git. A fresh
clone will not have `weights/behavior.joblib`, and behavior detection will not
load until it is supplied out of band. The `.truncated.bak` file is not a usable
model.

---

## 8. Tests

New: `test_incident_filters.py`, `test_live_streams.py`, `test_ignore_zone_filter.py`.

Substantially extended: `test_annotated_stream.py` (+175), `test_auto_zone.py`
(+115), `test_incident_service.py` (+124), `test_frame_hub.py` (+49) — the last
covering hub/publisher reuse within the grace window, teardown once it elapses,
and poster freshness gating.

`9591860` reports **391 backend tests passing with 10 pre-existing failures**
(verified there against a clean checkout), ruff clean on changed files, tsc
clean, and frontend lint holding at a **35-problem baseline**. *These figures are
quoted from that commit message and were not re-run for this document.*

---

## 9. Commit hygiene — worth fixing

- **`9591860` has a malformed subject line.** Its first line is
  `  video overlay, or are disabled states, and are deliberately unchanged.` —
  a fragment from the middle of the intended body. The actual subject was lost,
  so that change (analytics `live_cameras`, the Postgres connect timeout, the
  delete-all confirmation dialog, `live_streams.py`) is invisible in
  `git log --oneline`. This is the single largest gap in the range's history.
- **`bfb2450` and `e62f094` are near-duplicate commits** with the same subject
  and overlapping bodies, one minute apart.
- **`00abe30` ("UI modifications") and `89e2157` ("Incident panel update") have
  no bodies**, despite `00abe30` touching 15 files across both backend and
  frontend, including schema files (`schemas/detection.py`, `schemas/zone.py`)
  and the zone service. Their intent is not recoverable from the log.

---

## 10. Sections of `CODEBASE_OVERVIEW.md` to re-read before trusting

| Section | Why |
| --- | --- |
| §5 Data model | Three new columns; `area_zone_id` changes how zone attribution works. |
| §8 API routers | Two new endpoints, new analytics query params, handlers changed to sync `def`. |
| §9 Detection & streaming | Grace-window lifecycle, on-demand model pool, `MAX_CONCURRENT_STREAMS` semantics inverted from "pool size" to "ceiling". |
| §10 Zone monitoring & auto-zone | Suggestion lifecycle now reads saved zones, not an in-memory registry. |
| §11 Incidents & analytics | Feed pagination moved server-side; `active_cameras` vs `live_cameras` split. |
| §12 Reporting | EN/VI i18n layer added. |
| §14 Scripts / tests / weights | Three new migration scripts; behavior weights no longer in git. |
| §16–§22 Frontend | Largest churn in the range; three components/hooks added, one removed. |

---

## Appendix A — how severity is derived

Severity drives the dashboard's headline number, the filter chips, the report
PDFs, and the badge color on every incident card — but **there is no single
`severity` column** you can read to get it. Each of the three incident
categories answers the question differently, and two of the three compute it at
read time. This appendix is the rule in full.

Source of truth: `backend/app/services/incident_normalization.py`.

### The four levels

`VALID_SEVERITIES = ("Critical", "High", "Medium", "Low")` — Title case is the
canonical form. Anything arriving in another casing is title-cased before
comparison; anything unrecognized falls back to the category's default rather
than raising.

### PPE violations — derived from text, never stored

`ppe_violations` has **no severity column at all**. It is derived from the
`violation_type` string, lowercased and trimmed, in this order (first match
wins):

| Test on `violation_type` | Severity | Example |
| --- | --- | --- |
| contains `proximity` | **Low** | `proximity_violation` |
| contains `_and_` or ` and ` | **High** | `missing_helmet_and_vest` |
| anything else | **Medium** | `missing_helmet` |

The reasoning is "how many rules is this person breaking at once": several
missing items is worse than one, and a proximity warning is the softest signal
of the three.

**PPE is never Critical.** This is deliberate, not an oversight — so a Critical
filter asks the PPE table for *nothing* (`false()`), not for everything. Order
matters: `proximity` is checked before `_and_`, so a type containing both is
Low. The Python and SQL encodings agree on this because the SQL `High` predicate
is written `NOT proximity AND multiple`.

### Zone violations — stored, defaulted on read *and* now on write

`zone_violations.severity` is a **nullable free-text** column. Two separate
rules apply to it, and it is worth keeping them straight:

**On write** (`default_zone_severity`, new in this range — see §1):

| `zone_type` | Stored severity |
| --- | --- |
| `RESTRICTED` | **High** |
| `SLIPPERY` | **Medium** |
| `WALKWAY` | **Medium** |
| anything else / missing | **Medium** |

`RESTRICTED` is the only High because a keep-out zone exists precisely to catch
entry; walkway and slippery incursions are advisory by comparison.

**On read** (`normalize_zone_severity`): title-case the stored value and accept
it if it is one of the four; **otherwise Medium**. `NULL` also becomes Medium.

This read-side default is exactly what made the bug in §1 invisible for so long:
because nothing ever wrote the column, every incursion read back as a plausible
"Medium" rather than as obviously-missing data. A restricted-zone entry looked
like a mild event instead of a High one.

### Behavior incidents — stored uppercase, defaults to High

`behavior_incidents.severity` is nullable but **does** have a column default of
`HIGH`, and the pipeline writes it explicitly (`fall_detector.py`):

| Behavior label | Stored severity |
| --- | --- |
| `running` | **Medium** |
| everything else (fall, faint, collapse) | **High** |

On read (`normalize_behavior_severity`): title-case the stored value, accept it
if recognized, **otherwise High**. Note this default differs from zone's — a
missing behavior severity is assumed serious, because the events this category
records (a person on the floor) generally are.

### Why the defaults differ, and why the SQL has to know

Zone defaults to Medium, behavior defaults to High. That asymmetry is the reason
`incident_filters.py` keeps two "explicit" lists:

```python
_ZONE_EXPLICIT     = ("CRITICAL", "HIGH", "LOW")     # Medium is the fallback
_BEHAVIOR_EXPLICIT = ("CRITICAL", "MEDIUM", "LOW")   # High is the fallback
```

Filtering for a category's *default* level cannot be a simple `severity = 'X'`
— it must also match `NULL` and every unrecognized value, or the filtered feed
would silently drop rows the UI displays as that level. So:

- Filtering zone for **Medium** -> `severity IS NULL OR upper(severity) NOT IN (CRITICAL, HIGH, LOW)`
- Filtering behavior for **High** -> `severity IS NULL OR upper(severity) NOT IN (CRITICAL, MEDIUM, LOW)`
- Every other level -> plain `upper(trim(severity)) = 'X'`

An empty severity list means "nothing was asked for", which is **not** the same
as "asked for nothing" — it leaves the category unfiltered (`severity_filter`
returns `None`).

### What consumes this

- **Open Incidents KPI** = `Critical + High` (`analytics_service.py:106`). This
  is why the zone-severity bug mattered: restricted-zone incursions were stored
  `NULL` -> read as Medium -> never counted in the one number an operator
  watches.
- **Filter chips** in the incident panel, via the SQL predicates, so filtering
  happens before `LIMIT`/`OFFSET`.
- **Badge colors**, via `SEVERITY_BADGE_CLASS` / `SEVERITY_ACTIVE_CLASS` /
  `SEVERITY_DOT_CLASS` in `incident-severity.ts`. The active-chip text colors are
  picked per swatch to clear 4.5:1 contrast (white on Critical/High/Low, dark on
  the lighter Medium amber).

### If you change the rule

Change all three encodings together — Python read layer, SQL predicates, TS
mirror — then run `backend/tests/services/test_incident_filters.py`, which
asserts the first two agree for every stored row. Nothing automatically checks
the TS mirror against the Python, so that one is on you. And if you change a
*write*-side default (`default_zone_severity`), existing rows keep their old
value: add a backfill script alongside
`scripts/backfill_zone_violation_severity.py`.
