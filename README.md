# SIH26054 — AI-Enabled Real-Time Digital Twin for Aero Piston Engines

**Problem Statement:** SIH26054 (DRDO) — AI-Enabled Real-Time Digital Twin System for
Health Monitoring, Fault Prediction and Mission Reliability Enhancement of Aero Piston
Engines used in MALE UAVs.

A prognostics & health management (PHM) system for a turbocharged 4-cylinder aero piston
engine: a physics simulation, a digital twin that flies a healthy reference engine
alongside the real one, an ML layer that turns the difference between them into health
scores / fault diagnosis / remaining useful life / mission reliability, and a live cockpit
dashboard.

## Project phases

- **Phase 1 (done):** Full frontend dashboard + a mock backend streaming scripted
  telemetry over WebSocket at 10 Hz.
- **Phase 2 (done — current state):** Real physics replaces the mock. Mean-value engine
  model, thermal/lubrication/vibration/turbo models, ISA atmosphere, a digital-twin
  residual layer, and a PHM stack (anomaly detection, RandomForest fault classification,
  RUL extrapolation, Weibull mission reliability). **The WebSocket contract is byte-for-byte
  identical to Phase 1 — no frontend file changed.**
- **Phase 3 (done — current state):** Mission recording and replay (SQLite), electrical +
  injection-timing + combustion-stability physics, **sensor faults** (corrupt the reading,
  not the engine) with physical-vs-sensor disambiguation, BSFC efficiency tracking,
  rule-based maintenance advisories, post-mission reports, bearer-token auth, and a
  CAN-bus ingestion adapter interface. Schema extended with optional fields only — every
  Phase 1/2 panel renders unchanged.
- **Phase 4 (future):** Learned anomaly detector and sequence-model RUL in place of the
  hand-weighted mapping and trend fit; calibrated reliability model.

## Repo layout

```
frontend/    Next.js 14 (App Router) + TypeScript + Tailwind — the cockpit dashboard
backend/     FastAPI — physics simulation, digital twin, PHM/ML layer
  app/physics/   engine, thermal, lubrication, vibration, turbo, atmosphere, faults
  app/twin/      healthy reference engine + residual statistics
  app/ml/        anomaly detection, fault classifier, RUL, mission reliability
  app/sim/       tick loop, mission profile (+ Phase 1 mock as a fallback)
  app/db/        mission persistence (SQLite): missions, frames, fault events
  app/ingestion/ EngineDataAdapter boundary — simulated impl + documented CAN stub
  scripts/       headless physics validation with plots
docs/        architecture, physics-model reference, demo script
ml-notebooks/ (phase 3)
```

See `docs/architecture.md` for the data flow, `docs/physics-model.md` for the actual
equations and default parameters, `docs/demo-script.md` for a 3-minute walkthrough, and
`docs/deployment-roadmap.md` for the path from this prototype to a CAN-fed, edge-deployed
system.

---

## Running locally

### 1. Backend (physics simulation)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --reload --port 8000
```

Serves `ws://localhost:8000/ws/telemetry` at 10 Hz, plus `/control/*`, `/twin/diagnosis`
and `/health`.

> **Demo-safety fallback:** `USE_MOCK=true uvicorn app.main:app --port 8000` serves the
> Phase 1 scripted generator instead of the physics model. Same contract, no physics.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:3000>. The dashboard connects automatically; the Control Deck
injects faults, jumps mission phases, and changes simulated time speed.

### 3. Validate the physics headlessly (no frontend needed)

```bash
cd backend
source .venv/bin/activate

# Fault scenario — 10-min mission, fault injected at 3 min. Writes plots 01..07.
python -m scripts.validate_physics --fault bearing_wear

# Rapid throttle transient 20% -> 100% -> 20%. Writes plot 08 and prints time constants.
python -m scripts.validate_physics --scenario throttle-transient
```

Plots land in `backend/scripts/output/` — engine core, temperatures, lubrication,
vibration, health, prognostics, twin residuals, and the throttle transient. Options:
`--fault <type>` (any of the eleven), `--minutes`, `--inject-at`, `--severity`, `--ramp`.

The database is created automatically on first backend start (`backend/data/telemetry.db`)
— there is no separate migration step.

### 4. Train the fault classifier (optional)

The backend runs fine without it — the classifier reports `unknown/monitoring` and the
anomaly detector still produces health scores. To enable named fault diagnosis:

```bash
cd backend
source .venv/bin/activate

python -m app.ml.train.generate_training_data --episodes 14   # ~20 min, writes training_data.npz
python -m app.ml.train.train_classifier                       # writes app/ml/artifacts/fault_classifier.joblib
```

Restart the backend to pick up the saved model. Inspect its live opinion at
<http://localhost:8000/twin/diagnosis>.

---

## Quick reference

| Endpoint | Purpose |
|---|---|
| `WS /ws/telemetry` | 10 Hz `TelemetryFrame` stream |
| `POST /control/fault` | `{type, severity, ramp_seconds}` |
| `POST /control/clear-fault` | `{fault_type}` |
| `POST /control/throttle` | `{value}` 0–1, overrides the mission profile |
| `POST /control/time-scale` | `{factor}` e.g. 1 / 5 / 20 |
| `POST /control/phase` | `{phase}` climb / cruise / loiter / descent |
| `GET /twin/diagnosis` | Residuals, twin reference values, anomaly scores, classifier prediction |
| `GET /health` | Liveness |
| `POST /control/sensor-fault` | `{type, severity, ramp_seconds}` — corrupts a *reading*, not the engine |
| `POST /control/clear-sensor-fault` | `{fault_type}` |
| `POST /control/scenario` | `{scenario}` standard / hot_weather / cold_soak |
| `POST /control/ambient-temperature` | `{ambient_temperature_c}` (null restores ISA) |
| `POST /control/mission/start` | `{profile_name, notes}` — begins recording |
| `POST /control/mission/end` | Ends recording, returns the mission report |
| `GET /control/missions` | List recorded missions |
| `GET /control/missions/{id}/report` | Mission debrief JSON |
| `POST /control/replay/start` | `{mission_id, speed_factor}` |
| `POST /control/replay/stop` | Return to live |

### Authentication (optional)

Disabled by default so the demo runs with no setup. To enforce:

```bash
TELEMETRY_AUTH_ENABLED=true TELEMETRY_TOKEN=<secret> uvicorn app.main:app --port 8000
```

Then set `NEXT_PUBLIC_TELEMETRY_TOKEN=<secret>` for the frontend. `/control/*` requires
`Authorization: Bearer <token>`; the WebSocket also accepts `?token=` because browsers
cannot set headers on a handshake. This is a hackathon-grade shared secret — see
`docs/deployment-roadmap.md` for what a real deployment requires.
