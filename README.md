# SIH26054 — AI-Enabled Real-Time Digital Twin for Aero Piston Engines

**Problem Statement:** SIH26054 (DRDO) — AI-Enabled Real-Time Digital Twin System for
Health Monitoring, Fault Prediction and Mission Reliability Enhancement of Aero Piston
Engines used in MALE UAVs.

This repo implements a prognostics & health management (PHM) dashboard for a simulated
aero piston engine: a live "digital twin" cockpit UI, a streaming telemetry backend, and
(in later phases) a physics simulation core and ML models for anomaly detection, fault
classification, remaining-useful-life (RUL) prediction, and mission-reliability scoring.

## Project phases

- **Phase 1 (this repo, current state):** Full frontend dashboard + a lightweight mock
  backend that streams believable, fault-injectable telemetry over WebSocket at 10Hz.
  No real physics yet — see `backend/app/physics/*` stubs.
- **Phase 2:** Real physics-based engine model (thermal, lubrication, vibration, turbo,
  environment, fault models) driving the digital twin instead of the mock generator.
- **Phase 3:** ML layer — anomaly detection, fault classification, RUL prediction,
  mission-reliability model — trained in `ml-notebooks/` and served from `backend/app/ml/`.

## Repo layout

```
frontend/    Next.js 14 (App Router) + TypeScript + Tailwind — the cockpit dashboard
backend/     FastAPI (Python 3.11+) — mock telemetry WebSocket + control endpoints
docs/        architecture, physics-model reference, demo script
ml-notebooks/ (phase 3) training notebooks for the ML layer
```

See `docs/architecture.md` for the data-flow diagram and component responsibilities,
`docs/physics-model.md` for the engine/fault reference the physics layer will implement,
and `docs/demo-script.md` for a suggested 3-minute live-demo walkthrough.

## Quickstart

See the "Running locally" section at the bottom of this file, or just run:

```bash
# Terminal 1 — backend (mock telemetry server)
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Terminal 2 — frontend
cd frontend
npm install
npm run dev
```

Then open http://localhost:3000. The dashboard connects to `ws://localhost:8000/ws/telemetry`
automatically and the Control Deck panel lets you inject faults, jump mission phases, and
change simulated time speed live.
