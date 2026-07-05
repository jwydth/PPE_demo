# `ppe_detector.py` dependency map

Inventory of every top-level `def`/`class` in `app/services/ppe_detector.py`
(2307 lines, 59 top-level symbols — matches `grep -c "^def \|^class "`),
taken before any refactor code is written. Purpose: sanity-check the
`ppe/` package boundaries proposed in Step 3 and flag anything that mixes
PPE and zone-monitoring concerns before splitting.

Legend: **Concern** = `PPE` (pure), `ZONE` (touches `zone_service`), or
**`MIXED`** (touches both in the same function/class — needs a design
decision, called out in full below the table).

## Module-level imports worth noting

- `from app.services.zone_service import COORD_SCALE, ZoneViolationRecord, load_zones, get_person_foot_point, check_zone_incursion, record_zone_violation`
  — the file imports zone_service directly at module scope. Only 3 methods
  actually call these names (see MIXED section).
- `from app.services.auto_zone import SignPPERegistry, SignZoneRegistry, extract_signs`
  — sign/auto-zone-suggestion logic, used only inside `_real_video_pipeline`.
  Auto-zone is its own concern (neither pure PPE nor zone enforcement); flagged
  separately below.
- `from app.services.ppe_violation_service import open_ppe_violation_service`
  — PPE-only persistence, used by `save_violation`.

## Dataclasses

| Symbol | Line | Concern | Calls | Called by |
|---|---|---|---|---|
| `ViolationCase` | 65 | PPE | — (data holder) | `_record_violation_case`, `_find_existing_case*`, `_find_duplicate_ppe_case` |
| `WorkerState` | 78 | **MIXED (structural)** | `_empty_missing_counts` (in `__post_init__`) | almost every worker-tracking function below |

`WorkerState` carries both PPE fields (`missing_counts`, `reported_missing`,
`helmet_seen_frame`, etc.) **and** zone fields (`zone_dwell`,
`reported_zones`, `zone_last_in`) on the same object. See "Mixed-concern
findings" below — this is the deepest structural coupling in the file.

## Free functions — geometry / device (zero internal deps)

| Symbol | Line | Concern | Calls | Called by |
|---|---|---|---|---|
| `_area` | 110 | PPE | — | `_inter_area`, `_overlap_ratio`, `_bbox_iou` (via `.model_dump()`) |
| `_inter_area` | 114 | PPE | — | `_overlap_ratio`, `_bbox_iou` |
| `_overlap_ratio` | 122 | PPE | `_area`, `_inter_area` | `_build_response.best_match` (nested fn) |
| `_select_inference_device` | 129 | PPE | `torch.cuda.*` | `PPEDetector.__init__` |
| `_resolve_video_tracker` | 166 | PPE | `Path`, `BACKEND_DIR` | `_real_video_pipeline` |

## Free functions — worker tracking

| Symbol | Line | Concern | Calls | Called by |
|---|---|---|---|---|
| `_update_worker_status` | 1149 | PPE | `_find_or_create_worker`, `_merge_worker_observation`, `_update_worker_role`, `_apply_worker_role_to_person`, `_present_equipment`, `_missing_equipment`, `_relevant_missing_labels`, `_is_worker_judgeable`, `_unknown_reason`, `_suppress_recently_seen_ppe`, `_seconds_to_frames`, `_empty_missing_counts`, `_reset_missing_counts`, `_ordered_missing` | `_real_video_pipeline`, `_mock_process_video`, `_mock_stream_video` |
| `_find_or_create_worker` | 1286 | PPE | `_find_existing_worker` | `_update_worker_status` |
| `_find_existing_worker` | 1314 | PPE | `_find_spatial_worker_match` | `_find_or_create_worker` |
| `_find_spatial_worker_match` | 1353 | PPE | `_bbox_iou`, `_center_distance_ratio` | `_find_existing_worker` |
| `_merge_worker_observation` | 1377 | **MIXED** | — | `_update_worker_status` |
| `_is_worker_judgeable` | 1401 | PPE | `_seconds_to_frames`, `_bbox_height_ratio`, `_has_unclear_posture` | `_update_worker_status` |
| `_unknown_reason` | 1431 | PPE | `_seconds_to_frames`, `_bbox_height_ratio`, `_has_unclear_posture` | `_update_worker_status` |
| `_is_near_frame_edge` | 1459 | PPE | — | **none — dead code** |
| `_bbox_height_ratio` | 1470 | PPE | — | `_is_worker_judgeable`, `_unknown_reason` |
| `_is_bbox_stable` | 1476 | PPE | `_center_distance_ratio`, `_area` | **none — dead code** |
| `_has_unclear_posture` | 1492 | PPE | `_bbox_aspect_ratio`, `_bbox_height` | `_is_worker_judgeable`, `_unknown_reason` |
| `_suppress_recently_seen_ppe` | 1510 | PPE | `_seconds_to_frames`, `_last_seen_frame` | `_update_worker_status` |
| `_present_equipment` | 1526 | PPE | — | `_update_worker_status` |
| `_update_worker_role` | 1530 | PPE | — | `_update_worker_status` |
| `_apply_worker_role_to_person` | 1537 | PPE | `_equipment_status_for_label` | `_update_worker_status` |
| `_equipment_status_for_label` | 1555 | PPE | — | `_apply_worker_role_to_person` |
| `_relevant_missing_labels` | 1562 | PPE | — | `_update_worker_status` |
| `_last_seen_frame` | 1566 | PPE | — | `_suppress_recently_seen_ppe` |
| `_empty_missing_counts` | 1576 | PPE | — | `WorkerState.__post_init__`, `_update_worker_status`, `_reset_missing_counts` |
| `_reset_missing_counts` | 1580 | PPE | `_empty_missing_counts` | `_update_worker_status` |
| `_seconds_to_frames` | 1584 | PPE | — | `_update_worker_status`, `_is_worker_judgeable`, `_unknown_reason`, `_suppress_recently_seen_ppe` |

`_merge_worker_observation` (1377) resets `worker.reported_zones` /
`worker.zone_dwell` / `worker.zone_last_in` on a long tracking gap (lines
1382–1389) — see "Mixed-concern findings".

## Free functions — violation case matching / persistence

| Symbol | Line | Concern | Calls | Called by |
|---|---|---|---|---|
| `_record_violation_case` | 1589 | PPE | `_find_existing_case_match`, `_violation_type`, `_find_duplicate_ppe_case`, `_bbox_aspect_ratio`, `_violation_details`, `_save_violation_snapshot`, `save_violation` | `_real_video_pipeline`, `_mock_process_video`, `_mock_stream_video` |
| `_find_existing_case` | 1713 | PPE | `_find_existing_case_match` | **none — dead code** (only its own internal call to `_find_existing_case_match`) |
| `_find_existing_case_match` | 1722 | PPE | `_bbox_iou`, `_center_distance_ratio` | `_record_violation_case`, `_find_existing_case` |
| `_find_duplicate_ppe_case` | 1759 | PPE | `_case_violation_matches`, `_case_role_matches`, `_center_distance_ratio` | `_record_violation_case` |
| `_case_violation_matches` | 1803 | PPE | — | `_find_duplicate_ppe_case` |
| `_case_role_matches` | 1813 | PPE | — | `_find_duplicate_ppe_case` |

## Free functions — bbox geometry helpers (shared by worker + case matching)

| Symbol | Line | Concern | Calls | Called by |
|---|---|---|---|---|
| `_bbox_iou` | 1819 | PPE | `_area`, `_inter_area` | `_find_spatial_worker_match`, `_find_existing_case_match` |
| `_center_distance_ratio` | 1828 | PPE | `_bbox_diagonal` | `_find_spatial_worker_match`, `_is_bbox_stable`, `_find_existing_case_match`, `_find_duplicate_ppe_case` |
| `_bbox_width` | 1838 | PPE | — | `_bbox_aspect_ratio` |
| `_bbox_height` | 1842 | PPE | — | `_bbox_aspect_ratio`, `_has_unclear_posture` |
| `_bbox_aspect_ratio` | 1846 | PPE | `_bbox_width`, `_bbox_height` | `_has_unclear_posture`, `_record_violation_case` |
| `_format_track_ids` | 1851 | PPE | — | **none — dead code** |
| `_bbox_diagonal` | 1857 | PPE | — | `_center_distance_ratio` |
| `_ordered_missing` | 1861 | PPE | — | `_update_worker_status` |

## Free functions — response building / encoding

| Symbol | Line | Concern | Calls | Called by |
|---|---|---|---|---|
| `_encode_frame_to_base64` | 1865 | PPE | — | `_real_video_pipeline` |
| `_extract_result_boxes` | 1883 | PPE | `_box_track_id` | `_real_video_pipeline` |
| `_box_track_id` | 1913 | PPE | — | `_extract_result_boxes` |
| `_build_response` | 1923 | PPE | `_overlap_ratio` (nested `best_match`), `_best_equipment_by_person`, `_infer_role`, `_build_equipment_statuses_for_role` | `_real_process_video` (via `_collect_real_video_events`→`_real_video_pipeline`), `_real_video_pipeline` |
| `_best_equipment_by_person` | 2019 | PPE | — | `_build_response` |
| `_infer_role` | 2031 | PPE | — | `_build_response` |
| `_build_equipment_statuses_for_role` | 2043 | PPE | `_append_equipment_status` | `_build_response` |
| `_append_equipment_status` | 2081 | PPE | — | `_build_equipment_statuses_for_role` |
| `_append_unmatched_equipment` | 2119 | PPE | — | **none — dead code** |
| `_missing_equipment` | 2148 | PPE | — | `_update_worker_status` |
| `_violation_type` | 2152 | PPE | — | `_record_violation_case` |
| `_violation_details` | 2171 | PPE | — | `_record_violation_case` |
| `_append_tracking_overlay_frame` | 1085 | PPE\* | — | `_real_video_pipeline`, `_mock_process_video`, `_mock_stream_video` |

\* `_append_tracking_overlay_frame` accepts `camera_zone_view_id` /
`physical_zone_id` / `zone_name` / `zone_type` keyword params and uses
`zone_type` to decide overlay status, but never imports or calls
`zone_service` itself — the zone data is handed to it as plain values by
its (mixed) callers. Classified as pure PPE/response-building; flagged here
because it's the shape of the "shared vocabulary" between the two features.

## Free functions — snapshot / persistence (shared)

| Symbol | Line | Concern | Calls | Called by |
|---|---|---|---|---|
| `_save_violation_snapshot` | 2182 | **SHARED** | `SNAPSHOT_DIR`, `cv2.*` | `_record_violation_case` (PPE, no `polygon`/`zone_type`) **and** `zone_service.record_zone_violation` (ZONE, called back with `polygon`/`zone_type` — see below) |
| `save_violation` | 2242 | PPE | `open_ppe_violation_service` | `_record_violation_case` |
| `_video_metadata` | 2273 | infra (neither) | `cv2.VideoCapture` | `_real_process_video`, `_real_video_pipeline`, `_mock_process_video`, `_mock_stream_video` |

## `PPEDetector` class (line 201) — one top-level symbol, many methods

| Method | Line | Concern | Calls | Called by |
|---|---|---|---|---|
| `__init__` | 205 | PPE | `_select_inference_device`, `_load_model`, `_load_sign_model` | external routers |
| `_load_model` | 212 | PPE | `ultralytics.YOLO` | `__init__` |
| `_load_sign_model` | 234 | PPE (loads model used by auto-zone) | `ultralytics.YOLO` | `__init__` |
| `predict` | 251 | PPE | `_mock_predict` or `_real_predict`\*\* | routers/detection.py |
| `process_video` | 256 | PPE (dispatch) | `_mock_process_video` or `_real_process_video` | routers/detection.py |
| `_real_process_video` | 278 | PPE (orchestration) | `_video_metadata`, `_collect_real_video_events` | `process_video` |
| `_collect_real_video_events` | 327 | PPE (async→sync bridge) | `_real_video_pipeline` | `_real_process_video` |
| `stream_video` | 372 | dispatch | `_mock_stream_video` or `_real_video_pipeline` | routers/streaming.py |
| **`_real_video_pipeline`** | 405 | **MIXED** | see below | `_collect_real_video_events`, `stream_video` |
| `_mock_predict` | 763 | PPE | `_build_response` | `predict`, `_mock_process_video`, `_mock_stream_video` |
| **`_mock_process_video`** | 784 | **MIXED** | see below | `process_video` |
| **`_mock_stream_video`** | 925 | **MIXED** | see below | `stream_video` |

\*\* `predict` calls `self._real_predict`, which is **not defined anywhere
in this file** — grep confirms no `_real_predict` definition exists in
`ppe_detector.py`. This looks like a latent bug (dead branch: `predict`
would raise `AttributeError` if `self.model is not None`) unrelated to the
refactor. **Flagging per the ground rules rather than fixing it** — this is
a behavior question, not a structural one.

## Mixed-concern findings (need your sign-off before Step 3/4)

1. **`WorkerState` dataclass (line 78)** — holds PPE fields
   (`missing_counts`, `reported_missing`, `helmet_seen_frame`, …) and zone
   fields (`zone_dwell`, `reported_zones`, `zone_last_in`) on one object.
   Every worker-tracking function that receives a `WorkerState` therefore
   has an implicit dependency on the zone data shape even if it never reads
   it. Per the plan's default resolution ("split into a PPE half and a zone
   half, called from a shared orchestrator"), the cleanest split is: keep
   `WorkerState` (PPE fields only) in `ppe/worker_tracking.py`, and track
   zone dwell/reported-zone state in a separate structure keyed by the same
   worker identity (e.g. `id(worker)` or `track_id`), owned by
   `video_pipeline/`. This does touch call sites in the three mixed methods
   below — flagging before writing code.

2. **`_merge_worker_observation` (line 1377)** — pure PPE function (worker
   re-identification/bbox merge) that also resets `worker.reported_zones` /
   `worker.zone_dwell` / `worker.zone_last_in` on a long gap (lines
   1382–1389), justified by a zone-specific comment about occlusion by
   signs. If `WorkerState` is split per (1), this reset logic needs to move
   to the zone-side of the orchestrator and be invoked alongside (not
   inside) the PPE merge step.

3. **`_real_video_pipeline` (line 405, ~360 lines)** — the core offender:
   PPE worker/violation logic (`_update_worker_status`,
   `_record_violation_case`) is interleaved frame-by-frame with zone loading
   and enforcement (`load_zones`, `check_zone_incursion`,
   `record_zone_violation`, `ZoneViolationRecord`, the "no walkway" sentinel
   zone, retroactive zone checks over `foot_history`) **and** a third
   concern — auto-zone/sign-suggestion logic (`SignZoneRegistry`,
   `SignPPERegistry`, `extract_signs`). This is the method Step 4 names as
   the seed of `video_pipeline/`.

4. **`_mock_process_video` (line 784)** and **`_mock_stream_video` (line
   925)** — the no-model fallback paths duplicate the same PPE+zone
   interleaving as `_real_video_pipeline`, but against a plain
   `cv2.VideoCapture` loop instead of the YOLO tracker stream, and without
   the auto-zone/sign-suggestion pass. They call `load_zones` and
   `check_zone_incursion` directly (not `record_zone_violation` — the mock
   paths never persist zone violations, only annotate overlay frames with
   zone info). These need the same PPE/zone split as (3) but are simpler
   (no persistence, no sign registry).

5. **`_save_violation_snapshot` (line 2182)** — a single function used by
   *both* features: PPE calls it directly with no `polygon`/`zone_type`;
   zone code calls it indirectly by passing it as `save_snapshot_fn` into
   `zone_service.record_zone_violation`, which invokes it with
   `polygon=zone.poly, zone_type=zone.zone_type` to draw the zone outline on
   the snapshot. This is exactly the "function used by more than one
   feature area" case — it shouldn't move into `ppe/` (would make
   `video_pipeline`/`zone_service` reach back into `ppe/` internals just for
   this one snapshot helper) or into `zone_service` (PPE snapshots don't
   need zone drawing). Recommend: move it to
   `video_pipeline/` (or a small shared `snapshot.py` at the
   `app/services/` level that both `ppe/` and `zone_service`/
   `zone_monitoring/` are allowed to import — the one exception to the
   one-way dependency rule, since it has no PPE- or zone-specific logic
   itself, just image annotation).

## Auto-zone / sign-suggestion code (third concern, not PPE or zone)

`_real_video_pipeline` also drives `SignZoneRegistry` / `SignPPERegistry` /
`extract_signs` (imported from `app.services.auto_zone`, not shown in the
table above since they're not defined in this file). This is a third
concern distinct from both PPE compliance and zone enforcement — it
generates *suggestions* for new zones/PPE rules from detected safety signs.
It's already isolated in its own module (`auto_zone.py`), so no split
decision is needed there, but `video_pipeline/` will need to orchestrate
all three (PPE, zone, auto-zone-suggestions) rather than just two.

## Verification

- `grep -c "^def \|^class " app/services/ppe_detector.py` → **59**, matches
  every row in the tables above (2 dataclasses + 1 class + 56 free
  functions).
- Dead code identified during mapping (not fixed — behavior preserved):
  `_is_near_frame_edge`, `_is_bbox_stable`, `_find_existing_case`,
  `_format_track_ids`, `_append_unmatched_equipment` — none of these five
  have any caller anywhere in `app/`, `tests/`, or `scripts/`.
- Latent bug identified during mapping (not fixed): `predict()` calls
  `self._real_predict`, which doesn't exist in this file.
