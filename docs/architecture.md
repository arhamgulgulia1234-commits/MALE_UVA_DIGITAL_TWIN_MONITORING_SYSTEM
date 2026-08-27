# Architecture

## Data flow (Phase 1 — mock backend)

```
┌─────────────────────────────┐        10 Hz, 100ms tick        ┌───────────────────────────┐
│  backend/app/sim             │ ──────────────────────────────▶ │ backend/app/api/           │
│  simulation_loop.py          │   TelemetryFrame (pydantic)     │ ws_telemetry.py            │
│  - mission phase state       │                                  │ WebSocket /ws/telemetry     │
│    machine (climb→cruise→    │                                  │ broadcasts frame to all     │
│    loiter→descent)           │                                  │ connected clients            │
│  - sine baseline + noise     │                                  └─────────────┬───────────────┘
│    per signal                │                                                 │ JSON over WS
│  - active fault ramps        │                                                 ▼
│  - health/RUL/reliability    │                                  ┌───────────────────────────┐
│    derivation                │                                  │ frontend/lib/websocket.ts   │
└───────────────▲──────────────┘                                  │ reconnecting WS client       │
                 │ mutates shared sim state                        └─────────────┬───────────────┘
                 │                                                                │ parsed TelemetryFrame
┌────────────────┴──────────────┐                                                ▼
│ backend/app/api/control.py    │                                 ┌───────────────────────────┐
│ POST /control/fault           │ ◀── fetch() from ControlDeck ── │ frontend/hooks/            │
│ POST /control/clear-fault     │                                  │ useTelemetryStream.ts      │
│ POST /control/throttle        │                                  │ subscribes once, pushes     │
│ POST /control/time-scale      │                                  │ frames into the zustand     │
└────────────────────────────────┘                                 │ store                       │
                                                                     └─────────────┬───────────────┘
                                                                                   ▼
                                                                     ┌───────────────────────────┐
                                                                     │ frontend/lib/store.ts       │
                                                                     │ zustand store: rolling      │
                                                                     │ buffer (600 frames / 60s),  │
                                                                     │ latest frame, connection    │
                                                                     │ status, control actions     │
                                                                     └─────────────┬───────────────┘
                                                                                   ▼
                                                    all dashboard components read from the store
```

## Phase 2+ change

`backend/app/twin/digital_twin.py` will become the orchestrator: it owns the same tick
loop but calls into `app/physics/*` (real equations) instead of `app/sim/simulation_loop.py`
(sine+noise mock), and `app/twin/residual_analysis.py` + `app/ml/*` will compute health,
faults, and RUL from physics residuals instead of scripted ramps. The WebSocket contract
(`TelemetryFrame`) and the frontend do not change — this is the seam the phase boundary is
designed around.

## Frontend component responsibilities

| Component | Responsibility |
|---|---|
| `app/layout.tsx` | Root HTML shell, fonts, global providers |
| `app/page.tsx` | Composes the dashboard layout, mounts `useTelemetryStream` |
| `components/dashboard/MissionHeader` | Mission clock, current phase, GO/CAUTION/NO-GO banner |
| `components/dashboard/HealthScoreGauge` | Large animated radial gauge for `health.overall_score` |
| `components/dashboard/EngineCutaway3D` | react-three-fiber procedural cylinder-bank visualization, firing order animation, EGT-driven glow |
| `components/dashboard/MissionReliabilityCard` | `mission_reliability.score` + recommendation |
| `components/dashboard/RULPanel` | `rul_minutes` countdown / gauge |
| `components/dashboard/EngineVitalsGrid` | Sparkline tiles for RPM, per-cylinder EGT, CHT, oil press/temp, fuel flow, boost |
| `components/dashboard/TelemetryStrip` | Scrolling multi-line time-series (last 60s) |
| `components/dashboard/VibrationSpectrum` | Rolling per-cylinder vibration RMS bar chart |
| `components/dashboard/FaultAlertFeed` | Timestamped, color-coded fault log, newest first |
| `components/dashboard/ControlDeck` | Throttle, time-scale, phase jump, fault injection/clear — the live-demo control surface |
| `lib/types.ts` | Shared `TelemetryFrame` contract (mirrors backend pydantic models) |
| `lib/websocket.ts` | Reconnecting WebSocket client |
| `lib/store.ts` | Zustand store: rolling buffer + latest state + control actions |
| `hooks/useTelemetryStream.ts` | Wires `websocket.ts` → `store.ts`, exposes connection status |

## Backend module responsibilities

| Module | Responsibility |
|---|---|
| `app/main.py` | FastAPI app, CORS, router mounting, startup of the sim loop |
| `app/api/ws_telemetry.py` | `/ws/telemetry` WebSocket endpoint, connection manager/broadcast |
| `app/api/control.py` | `/control/*` REST endpoints mutating shared sim state |
| `app/api/health.py` | `/health` liveness endpoint |
| `app/sim/simulation_loop.py` | Phase 1 mock telemetry generator (state machine + fault ramps) |
| `app/sim/mission_profiles.py` | Phase timing/targets table used by the simulation loop |
| `app/core/config.py` | Settings (CORS origins, tick rate, etc.) |
| `app/physics/*` | Phase 2 — real equations, see `docs/physics-model.md` |
| `app/twin/*` | Phase 2 — digital twin orchestration + residual analysis |
| `app/ml/*` | Phase 3 — anomaly detection, fault classification, RUL, mission reliability models |
