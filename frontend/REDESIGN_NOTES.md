# Redesign notes

Working log for the pass described in `REDESIGN_BRIEF.md`. See `DESIGN_TOKENS.md` for the system this implements.

## Process

1. Read `globals.css`, `layout.tsx`, `package.json` — Tailwind v4 (`@theme inline`, no separate config file), Geist Sans/Mono already wired, `lucide-react` already the only icon set in use. No gradient/glass soup anywhere; that discipline was worth preserving as-is.
2. Traced every consumer of the seven target files before changing anything (`SuggestionOverlayLayer`/`PPESuggestionBanner` usage in `dashboard-shell.tsx`; `ZoneAggregate.insight` consumers in `zone-detail-panel.tsx`, `zone-block.tsx`, and the default-aggregate fallback in `factory-3d-view.tsx`) so the "same props, same data contracts" constraint held exactly.
3. Built the signature element (`DetectionChip`) first, then the two components that consume it (A, B), then C, D, E, F, G.

## Self-critique — what I rejected as a "default"

- First instinct for the suggestion tone was to keep orange (the existing zone-suggestion color) and just clean up the copy. Rejected: orange/yellow/amber for three different states was exactly the problem the brief called out. Collapsing all "the model found something, nothing has happened yet" states into one `sky` tone is a real semantic decision, not just a coat of paint — it also required recoloring the SVG polygon/bbox highlights in `video-tracking-overlay.tsx`, not just the chip copy, so the highlight-on-video and the chip-over-video read as the same object.
- First instinct for the chip icon was `TriangleAlert` — the generic "this is an AI warning component" default. Rejected in favor of `Signpost`: every suggestion in this system originates from the model reading a physical sign, so the icon should say that, not "generic caution."
- For item D, first instinct was to keep `buildInsight()` but strip the editorializing words while keeping one sentence. Rejected — the brief is explicit that a generated sentence is the tell, not just its adjectives. Replaced with a 3-up count readout (`ppeCount` / `zoneCount` / `fallCount`, already computed and available on `ZoneAggregate` — the sentence was recomputing data that already existed as numbers) plus the existing `trendDelta` arrow, unchanged.
- For item E, considered inventing "rounder" fake numbers (e.g. "97%", "50") per the brief's fallback option. Rejected in favor of the em-dash placeholder pattern the analytics dashboard *already* uses for its own unwired KPIs (`"—"` / "Not yet tracked") — reusing an existing, deliberate pattern beats inventing a second one that still risks being read as real.
- Found "TODO: not tracked" already live in `analytics-dashboard.tsx`'s KPI trend strings while fixing E — a literal dev-artifact string facing the operator. Small adjacent fix, in scope per the brief's own allowance ("unless a change forces a small adjacent edit").

## Adjacent edits (small, forced)

- `metric-card.tsx`: trend text was hardcoded emerald regardless of content; once `data.ts` started reporting "Not yet tracked" placeholders that needed to not read as a positive trend, so trend color now follows the metric's own `tone`.
- `zone-config-panel.tsx`: introduced one local `DraftZoneStatus` subcomponent (not a new file — it's two call sites in one file) rather than duplicating the pill markup twice.

## Verification

- `npm run lint` and `npx tsc --noEmit` run clean after the pass (see final summary).
- No prop, event, or data-contract signature changed on any exported component.
