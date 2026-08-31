# Project Handoff — VAYUDRISHTI (SIH26054 Engine Digital Twin)

**Read this file first.** It orients a new coding assistant (this project is being moved
from Claude Code to Antigravity) to what exists, what is already documented in depth
elsewhere, and what changed most recently that isn't written up anywhere yet.

This is not a replacement for the existing docs — it's a map to them, plus the parts of
the codebase those docs don't cover.

---

## 1. What this project is

**Problem statement:** SIH26054 (DRDO) — an AI-enabled real-time digital twin system for
health monitoring, fault prediction and mission reliability of aero piston engines used in
MALE UAVs.

**What's actually built:** a full-stack prognostics & health management (PHM) system —
- A physics simulation of a turbocharged 4-cylinder boxer aero engine (mean-value model:
  breathing, combustion, thermal, lubrication, vibration, turbo, electrical).
- A **digital twin**: a second, permanently-healthy copy of the same engine, stepped with
  identical commands, so `residual = real − twin` isolates genuine faults from normal
  throttle/altitude/phase changes.
- A Kalman-filter **sensor fusion** layer (CHT, RPM, oil pressure — each fused from two
  independent sources) that disambiguates a failing sensor from a failing part.
- A PHM/ML stack: anomaly detection → RandomForest fault classifier (99.7% CV accuracy,
  15 classes) → RUL predictor → Weibull mission-reliability model → rule-based maintenance
  advisor.
- A **Test Bench** ("what if?") mode: headless what-if scenario simulation and a
  bounded-search operating-point optimizer, physically constrained by the same engine
  model — entirely separate from the live WebSocket path.
- **Fleet-level monitoring**: the backend now simulates 3 independently-faulted UAVs
  (`UAV-01/02/03`) concurrently, with a ranked fleet overview and per-UAV routing
  threaded through every endpoint.
- A live 3D cockpit dashboard: React Three Fiber engine cutaway that's fully
  telemetry-reactive (colour, glow, particle flow — all ref-driven, zero React
  reconciliation per frame, confirmed by CDP profiling — see `docs/test-report.md` §F16).

**Current branch:** `feat/fleet-monitoring-and-part-inspector`. This branch added fleet
monitoring, an interactive part inspector on the 3D engine, a Flow Diagram overlay
rebuild, a full rebrand to **VAYUDRISHTI**, and a visual design pass — see §4, none of
which is written up in `docs/` yet.

---

## 2. Documentation that already exists — read these, don't re-derive them

These are extremely thorough, current, and were written by tracing actual code, not
guessed. **Trust them over re-reading the source from scratch.**

| File | Covers |
|---|---|
| `README.md` | Quick start, repo layout, endpoint table, run instructions |
| `docs/architecture.md` | **The most important file in the repo.** Full data-flow diagrams for every phase (1 through 6), a module-responsibility table for every backend file, a component-responsibility table for every frontend file, and deep prose on *why* each design decision was made (residual layer, sensor fusion, fleet registry, compute budget, etc.) |
| `docs/physics-model.md` | Every equation in `backend/app/physics/*`, with default parameter values and the tuned steady-state outputs per mission phase. If you need to touch any physics constant, this is the reference for what it currently does |
| `docs/deployment-hosting.md` | Vercel + Render split-deployment guide: CORS, WSS, env vars, Render free-tier cold-start/keepalive gotchas |
| `docs/deployment-roadmap.md` | The path from this simulated prototype to CAN-bus-fed real hardware — what changes, what doesn't, security hardening needed, fleet-scaling needed |
| `docs/test-report.md` | A hardening-sprint report: 7 real bugs found and fixed (3 were safety-inverting), with exact repro numbers. Read the "Known risks" section before assuming anything is bulletproof |
| `docs/demo-script.md` | 3-minute judge walkthrough |
| `design-system/vayudrishti/MASTER.md` + `pages/dashboard.md` | The design system driving the VAYUDRISHTI visual identity (colour tokens, typography, spacing, motion rules, anti-patterns) — **read before making any UI/styling change** |

If a future task touches physics, backend architecture, or deployment, **go to those files
first.** They are more detailed and more reliable than a fresh code read would produce.

---

## 3. Repo layout

```
backend/     FastAPI — physics sim, digital twin, sensor fusion, PHM/ML, fleet registry
  app/physics/    engine, thermal, lubrication, vibration, turbo, electrical, atmosphere, faults
  app/twin/       healthy reference engine + residual statistics
  app/fusion/     Kalman-filter fusion (CHT, RPM, oil pressure) + fusion_monitor
  app/ml/         anomaly detection, fault classifier, RUL, mission reliability, optimizer
  app/sim/        tick loop, mission profiles, scenario engine, replay engine
  app/db/         mission persistence (SQLite): missions, frames, fault events
  app/core/       config, engine params (every tunable constant), fleet registry, uav ids
  app/ingestion/  EngineDataAdapter boundary — simulated impl + documented CAN stub
  scripts/        headless physics validation + hardening/stability/classifier check harnesses
frontend/    Next.js 14 (App Router) + TypeScript + Tailwind + react-three-fiber
  app/            page.tsx (live dashboard), fleet/page.tsx, test-bench/page.tsx
  components/dashboard/engine3d/   the 3D engine cutaway — see §4 for what's new here
  components/fleet/                fleet roster/summary/trend UI
  components/testbench/            Test Bench scenario builder / results / optimizer UI
  components/lifecycle/            per-engine lifecycle/maintenance history UI
  lib/            per-domain zustand stores + REST/WS clients (lib/, lib/fleet/, lib/testbench/, lib/lifecycle/)
design-system/vayudrishti/   the VAYUDRISHTI brand/design-token spec (see §2)
docs/        architecture, physics reference, deployment, test report, demo script
ml-notebooks/    (placeholder, empty)
```

---

## 4. Recent work not yet documented anywhere (read this carefully)

The last four commits on this branch are **not covered in `docs/` or `README.md` at all**.
This section is the only writeup that exists for them.

### 4a. Rebrand: SIH26054 → VAYUDRISHTI

`frontend/lib/branding.ts` is now the single source of truth for the product name
(`VAYUDRISHTI`), tagline, and logo/emblem asset paths — every UI surface should import
from there rather than hardcoding the name. Two brand assets live in
`frontend/public/branding/`: `vayudrishti-logo.png` (full splash graphic, has a
problem-statement code baked into the ring artwork, **not rendered directly in the live
UI**) and `vayudrishti-emblem.png` (the cropped circular mark actually used in the header
and favicon). The visual identity itself (colour palette, fonts — Exo/Roboto Mono, a
"HUD / Sci-Fi FUI" style direction) is fully specified in
`design-system/vayudrishti/MASTER.md`.

### 4b. Interactive part inspector on the 3D engine cutaway

New files: `components/dashboard/engine3d/partSelection.tsx`,
`components/dashboard/engine3d/PartDetailPanel.tsx`, `lib/enginePartsRegistry.ts`, plus
new selection-aware helpers appended to `engine3dUtils.ts` (`PartVisual`, `visualFor`,
`partMaterial`, `partEmphasis`, `refreshMaterial`).

- **`lib/enginePartsRegistry.ts`** is the crossing point between the 3D geometry, the
  telemetry schema and the physics backend: one entry per clickable physical part, each
  carrying its display name, a plain-language description, which `TelemetryFrame` fields
  are relevant to it, which backend module simulates it, and which health subsystem scores
  it. This is the file to edit when adding a new clickable part.
- **`partSelection.tsx`** provides selection state via React context (not props-drilling)
  and deliberately keeps hover/appearance *out* of React state — hover sets
  `document.body.style.cursor` directly, and selection only ever touches
  `opacity`/`transparent` on materials, never colour or emissive (those stay entirely
  ref-driven from telemetry inside `useFrame`, per this scene's zero-reconciliation
  design — see `docs/architecture.md` and `docs/test-report.md` §F16 for why that matters).
- **`PartDetailPanel.tsx`** is a DOM panel (not a 3D-anchored `<Html>` node) docked beside
  the canvas, subscribed directly to the telemetry store so its numbers update live
  without touching the 3D scene's render path.
- A material-caching gotcha worth knowing if you touch this again: three.js bakes
  `#define OPAQUE` into a compiled shader program when `transparent === false`, so flipping
  `transparent` back to `true` later has no visible effect until `material.needsUpdate` is
  set — hence `refreshMaterial()`.

### 4c. Flow Diagram label overlay rebuild + sensor-fault panel cleanup

`components/dashboard/engine3d/EngineLabelsOverlay.tsx` was rebuilt. Labels used to be
`<Html>` nodes anchored directly at each part's 3D point and could visually collide when
the camera angle put two anchors close together on screen. They're now fixed
screen-space "slots" (two rows pinned to top/bottom of the canvas) each connected to its
part by a leader line whose endpoint is reprojected every frame — the slot position never
moves, so labels can never overlap regardless of camera angle. Also touches sensor-fault
badges and `ModuleMapToggle.tsx` (a flat list view of the same part registry — "what code
owns this part" — that shares selection state with the 3D view).

`FlowArrows.tsx` renders the animated flow-diagram particle streams (a single
`InstancedMesh` per path, all per-instance transforms written directly into the instance
matrix inside `useFrame` — no React state, nothing allocates per frame).

### 4d. Visual design pass on the main live dashboard

Most recent commit (`772d403`). Touched dashboard layout/styling broadly — check `git show
772d403 --stat` for the exact file list if you need to know precisely what changed;
narrative rationale for this pass hasn't been written up, only the design-system spec it
was implemented against (`design-system/vayudrishti/`).

### 4e. Fleet-level health monitoring (already documented, for completeness)

Unlike 4a–4d, this one *is* fully documented — see the "Fleet-Level Health Monitoring
(Phase 6)" section of `docs/architecture.md`, which is long and detailed (FleetRegistry,
per-UAV WebSockets, the SQLite migration helper, seeded starting wear, ranking logic,
frontend's `lib/fleet/store.ts` pattern). Don't re-derive it — it's already there.

---

## 5. Running it

```bash
# Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate   # .venv already exists in this checkout
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend
npm install                                            # node_modules already exists
npm run dev                                             # next dev — default port 3000
```

Full endpoint table, auth setup, physics validation scripts, and classifier training are
in `README.md` — don't duplicate that here, just go read it.

**Note:** `docs/architecture.md`'s "Still ahead" section flags that this project's actual
frontend dev server has historically run on port **3005** (3000 was occupied by an
unrelated project on the original dev machine) and that `CORS_ORIGINS` defaults to
`http://localhost:3000` only — check `.env` / `frontend/.env.example` against whatever port
Antigravity's dev server actually binds to, and update `CORS_ORIGINS` if it differs.

---

## 6. Housekeeping notes for whoever picks this up

- **Stray file:** `frontend/components/dashboard/engine3d/engine3dUtils.ts.tmp.72137.3024718006f7`
  is an editor/crash-recovery leftover — it's an *older* version of `engine3dUtils.ts`
  (predates the part-inspector helpers). Safe to delete; it is not referenced by any
  import.
- **Uncommitted:** `.claude/` (this tool's local config — irrelevant to Antigravity, can be
  deleted or gitignored) and the stray file above. Everything else is committed.
- **Known unresolved issues** (not bugs to "fix" reflexively — read the reasoning first):
  `docs/test-report.md`'s "Known risks" section lists 15 specific items, several
  deliberately left as-is (e.g. no UI state for "engine is recovering after a cleared
  fault" — the numbers are correct, only the explanatory UI affordance is missing; 5
  high-severity npm advisories in `next@14.2.35` that require a 2-major-version jump to
  fix). Read that section before assuming something is broken.
- **Coding conventions observed throughout this codebase**, worth preserving in any new
  work: every tunable physics constant lives in `backend/app/core/engine_params.py`
  (nothing hardcoded in model files); every file/module has a short prose comment
  explaining *why* a non-obvious design choice was made, not *what* the code does; the 3D
  scene's per-frame telemetry bindings are always ref-driven inside `useFrame`, never
  through a subscribing `useTelemetryStore(selector)` call (that pattern is reserved for
  DOM components that are meant to re-render, like `PartDetailPanel`).

---

## 7. Suggested first move for Antigravity

Read `docs/architecture.md` end to end first — it is the single highest-value file in the
repo and makes almost everything else in this handoff redundant except §4 (the
undocumented recent work) and §6 (housekeeping).
