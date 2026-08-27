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
- **Phase 3 (future):** Learned anomaly detector and sequence-model RUL in place of the
  hand-weighted mapping and trend fit; calibrated reliability model.

## Repo layout

```
frontend/    Next.js 14 (App Router) + TypeScript + Tailwind — the cockpit dashboard
backend/     FastAPI — physics simulation, digital twin, PHM/ML layer
  app/physics/   engine, thermal, lubrication, vibration, turbo, atmosphere, faults
  app/twin/      healthy reference engine + residual statistics
  app/ml/        anomaly detection, fault classifier, RUL, mission reliability
  app/sim/       tick loop, mission profile (+ Phase 1 mock as a fallback)
  scripts/       headless physics validation with plots
docs/        architecture, physics-model reference, demo script
ml-notebooks/ (phase 3)
```

See `docs/architecture.md` for the data flow, `docs/physics-model.md` for the actual
equations and default parameters, and `docs/demo-script.md` for a 3-minute walkthrough.

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
python -m scripts.validate_physics --fault bearing_wear
```

Flies a 10-minute mission, injects the fault at the 3-minute mark, and writes 7 plots to
`backend/scripts/output/` — engine core, temperatures, lubrication, vibration, health,
prognostics, and twin residuals. Options: `--fault <type>` (any of the nine),
`--minutes`, `--inject-at`, `--severity`, `--ramp`.

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
