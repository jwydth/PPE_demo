# Design tokens — Smart Factory Safety Monitoring frontend

Compact reference for the decisions behind the redesign in `REDESIGN_BRIEF.md`. Two surfaces share these tokens: the dark video-overlay surface (control room) and the light analytics/reporting surface.

## 1. Color / semantics

The app has exactly four safety-relevant states. Before this pass, three near-identical warm hues (orange/yellow/amber) were doing three different jobs. Collapsed to:

| State | Meaning | Token | Where it already existed |
|---|---|---|---|
| **Suggestion** | The vision model noticed something and is offering an action; nothing has happened yet. | `sky-400` / `sky-300` | New — replaces the orange zone-suggestion outline and the yellow PPE banner. |
| **Pending** | An operator choice exists as a draft and needs an explicit save. | `amber-400` / `amber-300` | Kept — this is genuinely a different state from "suggestion" (a suggestion has no draft yet; pending means something is staged). |
| **Active** | Monitoring is running / a control is on. | `lime-200` (brand accent) | Kept — already the app's primary-action and active-state color (header mark, primary buttons, "Drawing Active"). |
| **Violation / alert** | A logged hazard condition. | `red-500` / `red-400` | Kept — already used for non-compliant tracking boxes and delete actions. |

Rule going forward: a color only ever means one of these four things, on both surfaces. Neutral chrome stays slate.

## 2. Typography

Existing stack: Geist Sans (UI text) + Geist Mono (`--font-geist-mono`), already used for numerals in the analytics dashboard (axis ticks, KPI values). Extending that discipline:

- **Overlay chips** (dark surface, over arbitrary video): `text-xs`/`text-[11px]`, `font-semibold`/`font-bold`, opaque `slate-950/95` background — never text directly on video with no backing shape.
- **Dashboard headings**: existing `text-sm`/`text-base`/`text-lg font-semibold text-slate-950` scale — unchanged, already disciplined.
- **Numerals / metrics**: `font-mono` with tabular figures wherever a number is content to be scanned (counts, deltas, timestamps) — not just chart axes. Applied to the new zone incident readout (§ item D).
- **Captions / helper text**: `text-xs text-slate-500` (light surface), `text-xs text-slate-400` (dark surface).

## 3. Motion

One purposeful transition: a detection chip fading and easing in when the model surfaces a suggestion (`chip-in`, 160ms ease-out, translateY(4px)→0). No hover flourishes beyond the existing simple color transitions on buttons. `prefers-reduced-motion: reduce` disables it, matching the pattern already established in `analytics-dashboard.tsx`. Defined once in `globals.css` so every chip instance shares it — not re-declared per component.

## 4. Signature element — the detection chip

Everything the vision model surfaces as a suggestion (a sign it read, a PPE prompt) is the same kind of object at a different stage, so it gets one component: `components/ppe/detection-chip.tsx`.

State machine: `suggested → accepted` (the chip's job ends; downstream state — a saved zone, an enabled toggle — is owned by the feature, not the chip) or `suggested → dismissed`.

Shape: icon (industrial-signage-appropriate — `Signpost`, not a generic warning triangle, because the source of every suggestion here is literally a physical sign the model read) + one bold title line + optional inline content (e.g. an editable name field) + one row of icon-labeled actions. Tone is always `suggestion` (sky) — that is the entire point of collapsing the old three-color scheme: every object built from this component announces "here's what I found, your move" in one consistent voice.

Two call sites compose it differently (point-anchored over a polygon vs. a full-width banner for multiple simultaneous prompts), but both use the same container tokens, the same `ChipActionButton`, and the same tone — so they read as one system, not two banners that happen to be near each other.

## 5. Microcopy

See brief §7. In practice: button verbs name the artifact they create (`Add zone`, `Enable PPE Detection` — matching the exact toggle labels already on screen, e.g. "PPE Detection", "Zone Monitoring"), not the UI action on the button itself (`Accept`).
