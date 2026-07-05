# Refactor Prompt — Smart Factory Safety Monitoring

Paste everything below into Claude Code / Claude Desktop, pointed at the
`jwydth/Smart_Factory_Safety_Monitoring` repo on the `develop` branch.

---

## Prompt

You are refactoring the repository `Smart_Factory_Safety_Monitoring` (branch
`develop`). This is a FastAPI + SQLModel backend and a Next.js + TypeScript
frontend for PPE compliance and zone-incursion monitoring.

**Do not change behavior.** This is a structural refactor only: same API
routes, same request/response schemas, same DB schema, same detection logic.
Every step must end with tests passing. If a step would require changing
behavior to make tests pass, stop and ask me instead of guessing.

Work through the steps below **in order**, one at a time. After each step:
1. Run the verification commands listed for that step.
2. Show me a short summary of what changed and the verification output.
3. **Do not run `git commit` or `git push` yourself.** Leave the changes
   staged/unstaged in the working tree and tell me a suggested commit
   message (the one given below, or a close variant if you had to deviate).
   I will review the diff and commit it myself.
4. Wait for me to confirm I've committed before starting the next step —
   don't start Step N+1 on top of an uncommitted Step N.

If at any point you find a function that's used by more than one feature
area (PPE detection vs. zone monitoring), tell me explicitly — don't just
silently pick one module for it.

---

### Step 0 — Safety net

- Create a new branch off `develop` called `refactor/modular-structure`.
- Confirm the test suite passes *before* any changes, and record the
  baseline: `cd backend && python -m pytest -q` and `cd frontend && npm run
  build`.
- If either fails on a clean checkout, stop and tell me — don't refactor on
  top of a broken baseline.

**Verify:** baseline pytest + npm build output pasted back to me.
No commit needed for this step (no changes yet).

---

### Step 1 — Root-level cleanup (low risk, do first)

- Move `check_model.py` and `test_ws.py` from the repo root into
  `backend/scripts/`, alongside the existing `check_gpu_runtime.py`. Update
  any relative-path assumptions inside them (e.g. `sys.path.append` calls)
  so they still run correctly from their new location.
- Move `context.md` and `automatically_updated_zone.md` into a new
  `docs/dev-notes/` folder. Update the link in `README.md`'s
  "Documentation" section if these files are referenced anywhere.
- Remove the `docker-compose.yml` line from `.gitignore` (it's already
  tracked in git, so the ignore rule is misleading dead weight).
- Double check `backend/requirements.txt` and
  `backend/requirements-cuda-cu132.txt` for conflicting pinned versions of
  the same package; flag any conflicts to me rather than resolving them
  yourself.

**Verify:** `git status` shows only moves/deletes, nothing broken;
`python backend/scripts/check_model.py --help` (or equivalent smoke check)
still runs without import errors.
**Suggested commit message:** `[REFACTOR] root: move debug scripts and dev notes out of repo root`

---

### Step 2 — Map the current `ppe_detector.py` dependency graph

Before splitting anything, produce a written inventory (as a markdown file
`backend/docs/ppe_detector_refactor_map.md`, not code yet):

- List every top-level function/class in
  `backend/app/services/ppe_detector.py`.
- For each one, note: what it calls, what calls it, and whether it touches
  zone-monitoring code (imports from `app.services.zone_service`) or is
  pure PPE logic.
- Explicitly flag anything that mixes PPE and zone concerns in the same
  function — these are the trickiest to split cleanly and need a design
  decision (usually: split the function into a PPE half and a zone half,
  called from a shared orchestrator).

Show me this map before writing any new code, so I can sanity-check the
proposed boundaries.

**Verify:** map file exists and covers 100% of the original file's
top-level symbols (`grep -c "^def \|^class " ppe_detector.py` should match
the count of entries you catalogued).
**Suggested commit message:** `[REFACTOR] backend: document ppe_detector dependency map before split`

---

### Step 3 — Split `ppe_detector.py` into a feature-scoped package

Target structure (create under `backend/app/services/`):

```
ppe/
  __init__.py          # re-exports PPEDetector so existing imports keep working
  constants.py         # labels, colors, MISSING_LABEL_ORDER — zero internal deps
  geometry.py           # bbox math: area, IoU, overlap, distance, stability
  device.py             # inference device + tracker path resolution
  worker_tracking.py     # WorkerState + matching/merging/judgeability logic
  violation_matching.py  # ViolationCase + dedup/matching + persistence (save_violation)
  response_builder.py    # response/overlay assembly, frame encoding, video metadata
  detector.py             # PPEDetector class — orchestrates the above
```

Rules for this split:
- **Dependency direction is one-way:** `constants` → `geometry`/`device` →
  `worker_tracking` → `violation_matching` → `response_builder` →
  `detector`. A module may only import from modules to its left in that
  list. If you find you need the reverse, stop and tell me — it usually
  means a function belongs one level earlier than you placed it.
- Anything that imports from `app.services.zone_service` must **not** move
  into this `ppe/` package — flag it back to me; it belongs in Step 4's
  orchestration layer instead.
- Preserve every function's exact name, signature, and behavior. Only the
  file it lives in changes.
- `ppe/__init__.py` must re-export `PPEDetector` (and any other names
  external code imports directly) so that existing imports like
  `from app.services.ppe_detector import PPEDetector` and
  `from app.services import ppe_detector as ppe` keep working without
  touching every call site. Decide whether to keep the old
  `app/services/ppe_detector.py` path as a thin re-export shim pointing at
  `app/services/ppe/__init__.py`, or update the ~9 call sites
  (`routers/detection.py`, `routers/streaming.py`,
  `scripts/check_gpu_runtime.py`, and 6 test files) directly — pick
  whichever keeps the diff smaller, and tell me which you chose.
- Delete the old monolithic file only after every test that referenced it
  passes against the new package.

**Verify:**
- `cd backend && python -m pytest -q` — full suite green, same pass count
  as the Step 0 baseline.
- `python -m ruff check app tests` clean.
- Manually confirm no module in `ppe/` imports `zone_service`:
  `grep -rn "zone_service" backend/app/services/ppe/` returns nothing.

**Suggested commit message:** `[REFACTOR] backend: split ppe_detector.py into ppe/ package by concern`

---

### Step 4 — Introduce the orchestration boundary between PPE and zone monitoring

- Rename/reorganize `backend/app/services/zone_service.py` into a
  `zone_monitoring/` package **only if** it's grown large enough to warrant
  it (check its current line count first — if it's still a single
  reasonably-sized file, leave it alone; don't split for the sake of
  splitting).
- Create `backend/app/services/video_pipeline/` containing the code that
  currently lives inline in `PPEDetector.process_video` /
  `_real_process_video` / `_collect_real_video_events` where it calls into
  zone-checking functions (`load_zones`, `check_zone_incursion`,
  `record_zone_violation`, etc.) per frame. This becomes the **only** place
  in the codebase allowed to import from both `app.services.ppe` and
  `app.services.zone_service`.
- After this step, add a one-line comment at the top of `ppe/__init__.py`
  and `zone_service.py` stating "This module must not import from the
  other PPE/zone module — cross-feature logic belongs in
  `video_pipeline/`." This is documentation, not enforcement, but makes the
  rule visible to future editors.

**Verify:** same as Step 3 (pytest, ruff, plus confirm
`grep -rn "zone_service" backend/app/services/ppe/` and
`grep -rn "ppe_detector\|services.ppe" backend/app/services/zone_service.py`
(or `zone_monitoring/`) both return nothing outside `video_pipeline/`).
**Suggested commit message:** `[REFACTOR] backend: introduce video_pipeline as the ppe/zone orchestration boundary`

---

### Step 5 — Frontend: break up `dashboard-shell.tsx`

Current file: `frontend/src/components/dashboard/dashboard-shell.tsx`
(~1,985 lines), with a `CameraPanel()` function containing 30+ `useState`
hooks covering upload, zone drawing, live streaming, and auto-zone
suggestions.

- Extract the already-standalone presentational functions (`TopBar`,
  `ZoneSidebar`, `ZoneButton`, `MetricCard`, `IconButton`) into their own
  files under `frontend/src/components/dashboard/` (one component per
  file, named after the component).
- Extract state + effects out of `CameraPanel` into custom hooks under a
  new `frontend/src/hooks/` folder:
  - `useDetectionUpload.ts` — file upload state + image/video predict calls
  - `useZoneDrawing.ts` — draw/modify/drag/curve/point-insert state machine
  - `useLiveStream.ts` — WebSocket connection lifecycle, live/RTSP toggle,
    dynamic PPE/zone setting sync
  - `useAutoZoneSuggestions.ts` — sign-detection-driven zone/PPE suggestions
- `CameraPanel` should end up as a composition of these hooks plus JSX,
  not a monolith. Target under ~300 lines for the final `CameraPanel`.
- Do not change any prop shapes consumed by child components
  (`BoundingBoxView`, `FileUpload`, `PPESuggestionBanner`,
  `SuggestionOverlayLayer`, `TrackingOverlayLayer`) unless required — if
  required, tell me why before doing it.

**Verify:**
- `cd frontend && npm run build` succeeds.
- `npx tsc --noEmit` clean.
- `npm run lint` (eslint config already in repo) clean.
- Manually smoke-test in dev mode (`npm run dev`) that: image upload still
  works, zone drawing still works, live stream toggle still works — walk me
  through what you checked.

**Suggested commit message:** `[REFACTOR] frontend: extract hooks and subcomponents from dashboard-shell.tsx`

---

### Step 6 — Final pass

- Re-run the full verification suite from Step 0 (backend pytest, frontend
  build) one more time end-to-end.
- Produce a short `REFACTOR_NOTES.md` at repo root (or append to
  `docs/dev-notes/`) summarizing: what moved where, and — importantly — the
  import rule ("`ppe/` and `zone_service`/`zone_monitoring/` never import
  each other; `video_pipeline/` is the only bridge") so future contributors
  don't reintroduce the coupling.
- Do not delete the `refactor/modular-structure` branch or merge it — leave
  that decision to me.

**Verify:** full pytest + npm build green.
**Suggested commit message:** `[REFACTOR] docs: add refactor notes summarizing new module boundaries`

---

## Ground rules for the whole session

- One step at a time. Wait for my confirmation before moving to the next.
- Never change public API routes, request/response schemas, or DB models
  in this refactor. If a test fails and the only fix is a behavior change,
  stop and ask.
- **I will do all commits myself, one per step.** Never run `git commit`,
  `git add -A && git commit`, or `git push`. Just leave the working tree
  with the step's changes ready for me to review and commit.
- All suggested commit messages must follow this style: `[TAG] scope:
  short description`, e.g. `[REFACTOR] backend: split ppe_detector.py into
  ppe/ package`. Use `[REFACTOR]` for structural changes, `[FIX]` only if
  you had to correct a genuine bug along the way, `[ADD]`/`[REMOVE]` for
  additions/deletions of files. Keep the scope lowercase (`backend`,
  `frontend`, `root`, `docs`).
- If a function genuinely can't be cleanly assigned to one module without
  duplicating logic, tell me the tradeoff instead of picking silently.
