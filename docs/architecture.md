# Architecture

## Data flow (Phase 2 — physics simulation + digital twin + PHM)

```
                        ┌──────────────────────────────────────────────────────┐
                        │  app/sim/mission_profiles.py                          │
                        │  phase state machine -> commands only:                │
                        │  throttle, target altitude, airspeed                  │
                        └───────────────┬──────────────────────────────────────┘
                                        │ identical commands to BOTH plants
                    ┌───────────────────┴───────────────────┐
                    ▼                                       ▼
   ┌────────────────────────────────┐        ┌────────────────────────────────┐
   │ REAL ENGINE                     │        │ DIGITAL TWIN                    │
   │ app/physics/plant.EnginePlant   │        │ app/twin/digital_twin.py        │
   │  engine_model  (mean-value)     │        │ same EnginePlant class,         │
   │  thermal_model (CHT/oil ODEs)   │        │ FaultState permanently ZERO     │
   │  lubrication_model (P_oil)      │        │                                 │
   │  vibration_model (1 kHz + FFT)  │        │ = "what this engine would be    │
   │  turbo_model   (boost lag)      │        │    doing if nothing were wrong" │
   │  <- FaultState (fault_models)   │        │                                 │
   └───────────────┬─────────────────┘        └───────────────┬────────────────┘
                   │  real channels                            │ healthy channels
                   └───────────────┬───────────────────────────┘
                                   ▼
                    residual[ch] = real[ch] − twin[ch]        15 channels
                                   │
                                   ▼
              ┌────────────────────────────────────────────┐
              │ app/twin/residual_analysis.py               │
              │ EWMA mean + variance per channel            │
              │ z = |mean| / std      (time-constant in     │
              │ SIMULATED seconds, so 20x demo speed does   │
              │ not slow detection down)                    │
              │ feature_vector() <- shared with training    │
              └───────────┬────────────────────┬────────────┘
                          │                    │
                          ▼                    ▼
       ┌──────────────────────────────┐  ┌──────────────────────────────────┐
       │ app/ml/anomaly_detector.py    │  │ app/ml/fault_classifier.py        │
       │ z gates "is it real?",        │  │ RandomForest -> fault type        │
       │ magnitude gives "how bad?",   │  │ (joblib artifact; falls back to   │
       │ direction rejects wrong-signed│  │  "unknown/monitoring" if absent   │
       │ evidence                      │  │  or low confidence)               │
       │ -> per-subsystem HI (0-100)   │  └──────────────┬───────────────────┘
       └───────────┬───────────────────┘                 │
                   ▼                                     │
       ┌───────────────────────────────┐                 │
       │ app/ml/rul_predictor.py        │                 │
       │ linear + exponential fit to HI │                 │
       │ history, extrapolate to HI=40  │                 │
       └───────────┬───────────────────┘                 │
                   ▼                                     │
       ┌───────────────────────────────┐                 │
       │ app/ml/mission_reliability.py  │                 │
       │ R = exp(-(t_remaining/RUL)^2.5)│                 │
       │ -> GO / CAUTION / NO-GO        │                 │
       └───────────┬───────────────────┘                 │
                   ▼                                     ▼
       ┌────────────────────────────┐        ┌────────────────────────────┐
       │ TelemetryFrame              │        │ GET /twin/diagnosis         │
       │ (schema UNCHANGED from      │        │ residuals, twin values,     │
       │  Phase 1)                   │        │ anomaly scores, classifier  │
       │ -> WebSocket /ws/telemetry  │        │ prediction                  │
       └────────────┬───────────────┘        └────────────────────────────┘
                    │ 10 Hz JSON
                    ▼
       frontend/lib/websocket.ts -> hooks/useTelemetryStream.ts -> lib/store.ts (zustand)
                    │
                    ▼         all dashboard components read from the store
```

### Why the residual layer exists

A raw threshold on oil pressure fires every time the engine throttles back for loiter.
The *residual* against a twin that also throttled back stays at zero until something is
genuinely wrong. That is what makes the diagnosis invariant to throttle, altitude and
mission phase — and it is why the twin is stepped with the same commands rather than
being a lookup table of "nominal" values.

### Division of labour in the PHM layer

- **Residual monitor** answers *is this deviation statistically real?* (z-score)
- **Anomaly detector** answers *how bad, and which subsystem?* (magnitude × direction)
- **Fault classifier** answers *which specific fault?* — the part the first two
  legitimately cannot resolve. A clogged injector and a misfire both produce EGT spread;
  only the classifier separates them, using the full residual feature vector.
- **RUL predictor** answers *how long have I got?* (HI trend extrapolation)
- **Mission reliability** answers *should I continue?* (survival over remaining mission)

### `active_faults` reports ground truth, not the classifier

The `TelemetryFrame.active_faults` field carries the **injected** fault state, because the
frontend's ControlDeck uses it to drive its per-fault "tap to clear" toggle — a
misclassification would make the control surface lie about what is actually running. The
classifier's opinion is served separately from `GET /twin/diagnosis`, where it can be
compared against ground truth. Merging the two would need a frontend change, which Phase 2
explicitly does not make.

## Time handling

One wall-clock tick is 100 ms. Each tick advances `100 ms × time_scale` of simulated time
in fixed 20 ms integration sub-steps (so 20× runs 100 sub-steps per tick). Filter time
constants in the residual monitor, anomaly detector and RUL predictor are all expressed in
*simulated* seconds and converted per update via `alpha = 1 − exp(−dt/tau)` — otherwise
running the demo at 20× would make the PHM layer appear twenty times slower to react.

## Backend module responsibilities

| Module | Responsibility |
|---|---|
| `app/main.py` | FastAPI app, CORS, routers, starts the simulation task (or the mock if `USE_MOCK=true`) |
| `app/core/config.py` | Settings: CORS, tick rate, `USE_MOCK` |
| `app/core/engine_params.py` | **Every** tunable engine constant — nothing hardcoded in the models |
| `app/core/models.py` | Pydantic `TelemetryFrame` contract, mirrors `frontend/lib/types.ts` |
| `app/physics/environment.py` | ISA atmosphere, `density_ratio()` |
| `app/physics/engine_model.py` | Mean-value core: breathing, combustion, torque, speed dynamics, EGT |
| `app/physics/thermal_model.py` | CHT and oil-temperature ODEs, cooling-flap scheduling |
| `app/physics/lubrication_model.py` | Vogel viscosity, oil pressure vs. RPM and wear |
| `app/physics/vibration_model.py` | 1 kHz vibration synthesis + rfft feature extraction |
| `app/physics/turbo_model.py` | Boost spool lag, compressor discharge temperature |
| `app/physics/fault_models.py` | `FaultState`: severities, ramping, cylinder targeting |
| `app/physics/plant.py` | `EnginePlant` — bundles all five models; used by real *and* twin |
| `app/sim/mission_profiles.py` | Phase state machine, altitude/airspeed trajectory, remaining time |
| `app/sim/simulation_loop.py` | The tick loop: sub-stepping, PHM chain, frame assembly, broadcast |
| `app/sim/mock_generator.py` | Phase 1 scripted generator, retained as a demo-safety fallback |
| `app/twin/digital_twin.py` | Healthy reference plant + residual computation |
| `app/twin/residual_analysis.py` | EWMA statistics, z-scores, shared ML feature layout |
| `app/ml/anomaly_detector.py` | Per-subsystem anomaly scores and health indicators |
| `app/ml/fault_classifier.py` | RandomForest inference with confidence fallback |
| `app/ml/rul_predictor.py` | HI trend fitting and extrapolation to failure |
| `app/ml/mission_reliability.py` | Weibull survival over the remaining mission |
| `app/ml/train/*` | Offline data generation and classifier training |
| `app/api/ws_telemetry.py` | `/ws/telemetry` connection manager and broadcast |
| `app/api/control.py` | `/control/*` — fault inject/clear, throttle, time-scale, phase |
| `app/api/twin_diagnostics.py` | `/twin/diagnosis` — residuals and classifier output |
| `app/api/health.py` | `/health` liveness |
| `scripts/validate_physics.py` | Headless mission + fault, writes validation plots |

## Frontend component responsibilities

Unchanged from Phase 1 — no frontend file was modified in Phase 2.

| Component | Responsibility |
|---|---|
| `app/page.tsx` | Composes the dashboard, mounts `useTelemetryStream` |
| `components/dashboard/MissionHeader` | Mission clock, phase, GO/CAUTION/NO-GO banner |
| `components/dashboard/HealthScoreGauge` | Animated radial gauge + subsystem bars |
| `components/dashboard/EngineCutaway3D` | Procedural react-three-fiber cutaway, firing order, EGT glow |
| `components/dashboard/MissionReliabilityCard` | Reliability score and recommendation |
| `components/dashboard/RULPanel` | RUL readout |
| `components/dashboard/EngineVitalsGrid` | Sparkline tiles with healthy-band border glow |
| `components/dashboard/TelemetryStrip` | 60 s multi-line time series |
| `components/dashboard/VibrationSpectrum` | Per-cylinder vibration RMS bars |
| `components/dashboard/FaultAlertFeed` | Colour-coded fault log |
| `components/dashboard/ControlDeck` | Throttle, time-scale, phase jump, fault injection |
| `lib/types.ts` | `TelemetryFrame` contract (mirrored by the backend) |
| `lib/websocket.ts` | Reconnecting WebSocket client |
| `lib/store.ts` | Zustand store: 600-frame rolling buffer + control actions |

## Phase 3 (future)

The ML layer is scaffolded but only the classifier is a trained model. Phase 3 would add:
a learned anomaly detector (autoencoder reconstruction error over residuals) replacing the
hand-weighted subsystem mapping in `anomaly_detector.py`; a sequence model (LSTM/TCN) for
RUL in place of trend extrapolation; and a calibrated reliability model trained on
simulated mission outcomes rather than a fixed Weibull shape.
