# Redesign Brief — Smart Factory Safety Monitoring frontend

**For:** Claude Desktop, working in the `Smart_Factory_Safety_Monitoring` repo, branch `feature/interactive_dashboard`.
**Goal:** Redesign a set of components that currently read as "AI-generated," replacing them with an intentional, subject-grounded design language executed to an impeccable standard.

Read this whole brief before touching code. Do the planning pass described in **§6** *before* writing any component.

---

## 1. Context

- **Stack:** Next.js (App Router, TypeScript `.tsx`), Tailwind CSS, `lucide-react` for icons. Confirm by inspecting `frontend/package.json`, `frontend/src/app/layout.tsx`, and `frontend/src/app/globals.css` before you start.
- **Subject:** an operator-facing safety-monitoring system for a factory floor. Two distinct surfaces:
  - **Dark surface** — the live video overlay (`slate-950`-ish) where sign-detection prompts and zone drawing sit *on top of* arbitrary video. This is a control-room / operator context. Legibility over unpredictable footage and a clear urgency hierarchy matter more than decoration.
  - **Light surface** — the analytics dashboard, the 3D map detail panels, and the incident modal. A review/reporting context.
- Treat the factory-safety domain as your design source: industrial signage, hazard semantics, control-room instrumentation. That vernacular is where a distinctive-but-appropriate look comes from. **Do not** reach for the current AI-default looks (warm cream + serif + terracotta `#D97757`; near-black + single acid-green accent; broadsheet hairline columns). None of those fit an operator tool anyway.

---

## 2. What's in scope

Redesign these, and only these, unless a change forces a small adjacent edit:

| # | Component / file | What's wrong today |
|---|---|---|
| A | `components/ppe/video-tracking-overlay.tsx` → `SuggestionOverlayLayer` (~L481–590) | Floating zone-suggestion card with `Accept ✓ / Dismiss ✕`, `⚠ Suggested:` microcopy. Glyphs-in-labels, emoji-prefixed copy. |
| B | `components/ppe/video-tracking-overlay.tsx` → `PPESuggestionBanner` (~L592–660) | `⚠ Sign detected: … — PPE monitoring required`, `Enable PPE ✓`. Same tells. |
| C | `components/dashboard/zone-config-panel.tsx` (L78–83, L235–240) | Amber "Auto-zone accepted — adjust the vertices, then click **Save zones** to confirm." Over-explained instructional microcopy. |
| D | `components/factory3d/use-zone-incidents.ts` → `buildInsight()` (L97–113) | Auto-generated narrative prose ("…highest-severity behavior this period", "…dominate; most common: …"). Rendered in `zone-detail-panel.tsx` and `zone-block.tsx`. |
| E | `components/dashboard/data.ts` | Hyper-specific fake seed data (`98.2%`, `+2.4% today`, `96ms edge latency`, "Camera 07 calibration drift"). Reads as generated placeholder. |
| F | `components/analytics/analytics-dashboard.tsx` (L500–501) | "Zone Pulse" branded title + "— click a zone to filter" em-dash subtitle. |
| G | Duplicated warning string in `dashboard-shell.tsx` (L1002), `incident-detail-modal.tsx` (L70), `analytics-dashboard.tsx` (L371) | Same polished sentence copy-pasted three times. |

**Do NOT touch** (these are fine / legitimately human):
- The `?? "—"` em-dash empty-value placeholders in `incident-detail-modal.tsx`.
- The overall color discipline and component decomposition — there is *no* gradient/glassmorphism/`backdrop-blur` soup, and that restraint should stay.
- Backend, detection logic, websocket event shapes, or any `types/` contracts. This is a presentation-layer redesign; keep the data flowing exactly as it does now.

---

## 3. The mandate: what "impeccable" means here

Two jobs, weighted equally:

**(a) Remove the AI tells.** These specific patterns must be gone from the components above:
- Unicode glyphs baked into text labels (`✓ ✕ ⚠ →`). Use `lucide-react` icons (`Check`, `X`, `TriangleAlert`, etc.) as real elements, or nothing.
- Emoji-prefixed microcopy (`⚠ …`).
- Em-dash-joined instructional tails ("do X — then do Y to confirm").
- Auto-composed natural-language "insight" sentences. Show the numbers; let the operator read them.
- False-precision fake data. Replace with obvious placeholders or wire to the real source.
- The same sentence duplicated across files — hoist to one shared constant.
- `count === 1 ? "" : "s"` scattered inline — centralize in one `pluralize()`/`count()` helper and import it.

**(b) Elevate, don't just neutralize.** Removing tells leaves you at "inoffensive default," which is not the goal. Give these components a deliberate, cohesive design language (see §4–§5). The bar is: an operator glances at the screen mid-shift and instantly parses *severity, what the system wants, and what their options are* — with zero decoration that doesn't serve that.

---

## 4. Establish the design system first (do this before any component)

Produce a compact token system, written into a short `DESIGN_TOKENS.md` you create in `frontend/`, then derive every component decision from it. Cover:

- **Color / semantics.** Define a disciplined scale for the safety states this app actually has: *informational suggestion*, *pending confirmation*, *active monitoring*, *violation/alert*. Ground it in hazard/signage semantics but resist using six saturated colors — pick a restrained set and assign each a single meaning, used consistently on both surfaces. Right now orange = zone suggestion, yellow = PPE, amber = pending is three near-identical warm hues doing three different jobs; collapse and clarify that.
- **Typography.** Inspect the current setup, then set an intentional type scale with deliberate weights for: overlay chips (dense, high-contrast, legible over video), dashboard headings, data/numerals (consider tabular figures for metrics), and captions. Numerals in a monitoring tool are content — treat them as such.
- **Motion.** Define *when* motion is allowed and cap it. Prefer one purposeful transition (a suggestion appearing/being accepted) over scattered hover flourishes. Respect `prefers-reduced-motion`. Note: excess animation is itself an AI tell.
- **Signature element.** Choose the one thing this UI is remembered by and make it subject-true. Strong candidate: a unified **detection chip / prompt** component language — a single, calm, information-dense pattern for *everything the vision model surfaces* (sign suggestions, PPE prompts, zone confirmations), with an explicit state machine `suggested → accepted(pending) → active` and `dismissed`. That replaces the current three ad-hoc banners with one coherent system. Spend your boldness here; keep everything around it quiet.

---

## 5. Component-by-component direction

**A + B — the video-overlay prompts.** Unify these into the detection-chip language from §4. Requirements: readable over any video frame (needs its own contained background, not just text), a clear severity read at a glance, actions as icon+label buttons whose verbs match the outcome (see §7). The zone suggestion and the PPE suggestion are the *same kind of object* at different stages — make them feel like one system, not two unrelated banners.

**C — zone confirmation.** Replace the instructional paragraph with a compact status affordance that shows *state*, not a how-to. The operator should see "this zone is a draft / unsaved" and the save action, and infer the rest. Kill the "do X — then click Y to confirm" sentence. The action label and the resulting confirmation must use the same word (see §7).

**D — `buildInsight()`.** Remove the editorializing entirely. Replace the generated sentence with a small, honest data readout: the counts by category and, at most, a single restrained delta indicator (an arrow/number, not a claim like "highest-severity this period"). Keep it factual. Update `zone-detail-panel.tsx` and `zone-block.tsx` to consume the new readout.

**E — `data.ts`.** Decide with the user: either (i) wire these metrics to the real backend, or (ii) if they must stay mocked, make them *obviously* placeholder (rounder values, a clear `// DEMO DATA` marker) so no one mistakes them for real telemetry. Do not invent new false-precise numbers.

**F — analytics section.** Rename "Zone Pulse" to something plain and true to the panel's job. Cut the "— click a zone to filter" tail; if the interaction needs signposting, express it as a proper affordance, not a subtitle sentence.

**G — duplicated warning.** Create one shared constant (e.g. `lib/messages.ts`) for the false-positive/delete confirmation and import it in all three call sites.

---

## 6. Process to follow

1. **Inspect** the repo: `globals.css`, `layout.tsx`, existing Tailwind config, and the seven target files, so the system fits what's there.
2. **Plan** the token system and signature (§4). Write `DESIGN_TOKENS.md`.
3. **Self-critique the plan.** For each choice, ask: "would I produce this for any similar app?" If yes, it's a default — revise it and note what changed and why. Only proceed once the plan is specific to *this* brief.
4. **Build** one component fully (suggest starting with the detection chip, since A/B/C depend on it), to the quality floor in §8.
5. **Critique again** against a screenshot if your environment can render one. Remove one thing that isn't earning its place before moving on.
6. Repeat for the rest. Keep a running `REDESIGN_NOTES.md` of what you tried so later passes don't repeat it.

Do most of the exploration in your own reasoning; only surface polished options to the user.

---

## 7. Microcopy rules (apply everywhere in scope)

- Sentence case. No emoji or glyph prefixes.
- Active voice; name the outcome. A button says what happens: "Add zone," not "Accept ✓."
- **One vocabulary through a flow.** The button that says "Add zone" produces a confirmation that says "Zone added." "Enable PPE monitoring" → "PPE monitoring on." Don't switch words mid-flow.
- Name things by what the operator controls, not by system internals.
- Empty and error states give direction, not mood: what happened / what to do, in the interface's voice. No apologizing, no vagueness.
- Every word must aid comprehension. If it doesn't help someone act, cut it.

---

## 8. Quality floor (non-negotiable)

- Responsive down to mobile; overlay chips must not overflow or obscure critical video regions on small viewports.
- Visible keyboard focus on every interactive element; full keyboard operability.
- `prefers-reduced-motion` respected.
- Sufficient contrast for overlay text over arbitrary/bright video (test against a light frame, not just dark).
- No behavior/logic changes: same props, same events, same data contracts. If a refactor is needed to hit the design, keep the public interface of each component stable.
- TypeScript clean, lint clean (`eslint.config.mjs`).

---

## 9. Definition of done

- All seven items in §2 redesigned; none of the §3(a) tells remain in those files.
- `DESIGN_TOKENS.md`, `REDESIGN_NOTES.md`, and a shared messages/pluralize helper exist and are used.
- Overlay prompts, zone confirmation, and dashboard readouts share one coherent visual language.
- A short before/after summary at the end noting each change and the reasoning, so the user can review the intent, not just the diff.
