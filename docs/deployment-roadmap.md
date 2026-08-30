# Deployment Roadmap

From the simulated prototype in this repository to a CAN-bus-fed, edge-deployed system
running against a real MALE UAV engine and a real ground control station.

The organising idea is that **most of this system does not need to change**. The physics
twin, the residual layer, the anomaly detector, the classifier, the RUL and reliability
models and the entire dashboard all operate on `RawEngineData` and `TelemetryFrame`. What
changes is where those two structures come from. That is why
`backend/app/ingestion/adapter_interface.py` exists and why it exists *now*, before there
is any hardware to talk to.

```
   TODAY                                    TARGET
   ─────                                    ──────
   SimulatedAdapter                         CANBusAdapter
   (physics sim)                            (SocketCAN / J1939 / FADEC)
        │                                        │
        └──────────► RawEngineData ◄─────────────┘
                          │
                   (unchanged from here down)
                          ▼
              digital twin + residuals
                          ▼
              anomaly / classifier / RUL
                          ▼
                   TelemetryFrame
                          ▼
                  WebSocket → dashboard
```

---

## Stage 1 — Hardware-in-the-loop on a test cell (current + next step)

**Goal:** prove the pipeline against a real engine on a bench before anything flies.

- Implement `CANBusAdapter` per the docstring in `app/ingestion/adapter_interface.py`:
  `python-can` over SocketCAN, decoding either J1939 PGNs or a vendor DBC with `cantools`.
- Run the bus reader on its own thread with a latest-value-per-signal cache. Signals
  arrive at different rates (RPM at 100 Hz, oil temperature at 1 Hz); `read_frame()` must
  snapshot, never block.
- **Populate `RawEngineData.valid` honestly.** A stale or missing signal must be marked
  invalid, not passed through as its last value or as zero. A dropped CAN frame that
  reads as "oil pressure 0 kPa" is indistinguishable from catastrophic oil loss, and the
  PHM layer will do exactly what you would expect with that input.
- Add a vibration path. Accelerometers rarely appear on CAN; expect SPI/I2S into the edge
  computer at 1–10 kHz. Fill `vibration_samples` + `vibration_sample_rate_hz` and the
  existing FFT feature extraction handles it unchanged.
- **The adapter must be strictly read-only.** Never transmit onto an engine control bus.

**Calibrate the twin against the real engine.** This is the substantial work, not the
plumbing. Every coefficient in `app/core/engine_params.py` was tuned for plausible
*relative* behaviour, not against a dynamometer. Run the real engine through a sweep of
speed/load/altitude-equivalent points, then fit: volumetric efficiency, friction
coefficients, thermal masses and conductances, the oil viscosity curve, and the
compressor map. Until this is done the residuals measure "how wrong our model is" rather
than "how degraded this engine is", and every diagnosis inherits that error.

## Stage 2 — Edge deployment on the air vehicle

**Goal:** run the twin onboard so diagnosis survives loss of the datalink.

- Target class: Jetson Orin Nano / Raspberry Pi CM4 with a CAN HAT, or the mission
  computer if it has headroom. The tick loop is light — the FFT dominates, and it is a
  1024-point transform at 10 Hz.
- Run the full twin + PHM stack onboard, not just data forwarding. A UAV that loses its
  link must still know its own engine is failing; that judgement is what triggers an
  autonomous return-to-base.
- Downlink budget: the full `TelemetryFrame` at 10 Hz is far too much for a constrained
  satcom link. Send health, RUL, reliability and advisories at ~1 Hz, raw channels at a
  reduced rate, and burst full-rate data only around an anomaly. The recording layer
  (`app/db/`) already stores everything locally for post-flight retrieval.
- Persist to onboard storage exactly as `app/db/repository.py` does now, and offload the
  SQLite file after landing. Mission replay and reporting then work unchanged on real
  flights.

## Stage 3 — Ground control station integration

- The dashboard becomes a GCS panel rather than a standalone page. It already consumes a
  single WebSocket and holds no server state, so embedding it is mostly a routing concern.
- Replace the single shared token (`app/core/security.py`) with real identity — see
  Security below.
- Fleet view: the current schema is single-airframe. Add a `vehicle_id` to the frame and
  a per-vehicle connection registry. The database schema already separates missions, so
  the change is additive.

### The Test Bench becomes pre-flight mission planning

Phase 4's Test Bench is, structurally, a pre-flight planning tool that happens to be flying
a simulated engine. In a real GCS the same two endpoints answer the questions a mission
commander asks before a sortie is authorised, and almost nothing about them changes.

**What generalises unchanged.** `POST /simulate/scenario` already takes the exact inputs a
planner has in hand — cruise altitude, forecast outside air temperature, sortie duration, a
throttle profile, and the engine's current condition — and returns a GO/CAUTION/NO-GO
trajectory with the specific limits that would be exceeded and when. It runs 200–750× real
time on one core, so a planner can compare a dozen profiles interactively. The optimizer
answers the follow-on question — *given that, what should the FADEC actually be set to?* —
and returns the answer with a life cost attached rather than a bare number.

**What has to change, and it is all upstream of the models.**

| Concern | Today | In a real GCS |
|---|---|---|
| Engine condition | `initial_fault_severities` typed into a form | Read from the airframe's stored health state — the last mission's residuals and RUL, not an operator's guess |
| Weather | One altitude and one temperature held constant | Forecast profile along the planned route: temperature and pressure per waypoint, feeding the same `ambient_temperature_c` argument per leg |
| Throttle profile | Constant or a single ramp | Derived from the planned route and payload — the autopilot's own power schedule, not a hand-drawn curve |
| Airspeed | A throttle-to-airspeed schedule in `scenario_engine.py` | The airframe's drag polar. This is the one genuine gap: it is why "max endurance" and "max range" cannot currently be separated, and why the endurance preset is honest about using the range objective |
| Fuel | Litres burned | Reserve margin against the planned route, with diversion fuel |
| Authority | An operator clicks "Apply to Live Engine" | A recommendation into the mission plan, subject to the same authorisation as any other pre-flight limit — see the command-path row in Security below |

**Run it in its own process.** On the ground today the Test Bench shares an interpreter
with the live simulation, and `app/core/compute_budget.py` documents what that costs — a
multi-second search pulls the telemetry broadcast from 9.3 Hz to 7.7 Hz with a 185 ms
worst-case gap. On an edge computer with a fraction of the cores, that trade stops being
acceptable. The fix is structural rather than fiddly: both Phase 4 entry points are pure
functions of their arguments (`run_scenario(params)`, `optimize_operating_point(...)`) with
no dependency on `app.state`, so moving them behind a process pool or a separate service is
a routing change, not a rewrite. Planning does not have to run on the aircraft at all — it
is a ground activity, and running it on the GCS removes the contention question entirely.

**Fleet dispatch is the same call with a different loop.** The optimizer already answers
"can this engine hold cruise power at these conditions within limits?" and returns
`feasible: false` with the failing check when it cannot. Run it across a fleet's stored
health states and the same code answers "which airframes can fly tonight's profile, and
which need maintenance first" — which is a dispatch decision, not a monitoring one, and is
where a PHM system stops being a dashboard and starts saving flying hours.

**What must be calibrated before any of this is trusted.** The stress-rate index behind
every life-impact figure is a physically-motivated ordering — Arrhenius terms on head and
oil temperature, squared vibration for fatigue, inverse-square oil pressure for film
thickness — normalised so nominal cruise reads 1.0. It ranks operating points correctly
against each other. Turning "+174% projected life" into an overhaul interval a maintenance
organisation would plan against needs run-to-failure data, and the same caveat in **What
would need genuine research** applies with full force. The right first use of this is
comparative — *this setting is gentler than that one* — not absolute.

## Stage 4 — Fleet learning

- Retrain the classifier on real labelled failures as they accumulate, rather than only on
  simulated ones. The synthetic data remains useful for classes that are rare in service.
- Compare residual trends across airframes to separate "this engine is degrading" from
  "our model is biased" — a fleet-wide common-mode residual is a modelling error, not
  fifty simultaneous failures.

**Scaling the three-UAV fleet pattern to a real squadron.** The current implementation (see
docs/architecture.md's "Fleet-Level Health Monitoring" section) proves the pattern — one
`SimulationLoop`/`ReplayEngine` pair and one lifecycle ledger per airframe, a `uav_id` on
every per-engine request, a ranked fleet-wide view — at a fixed roster of three hardcoded
ids. Getting from that to an operational squadron needs:
- **A real roster, not a constant.** `app/core/uav_ids.py::UAV_IDS` becomes a database
  table (airframe id, tail number, commissioning date, retirement status) with admin
  endpoints to add/retire an airframe, rather than a code edit and a restart.
- **One process per airframe, not one process for all of them.** `FleetRegistry` ticking
  N `SimulationLoop`s in a single asyncio loop is fine for three simulated engines sharing
  one CPU; a real squadron's edge deployment is Stage 2's "one instance per aircraft"
  model — each airframe already runs its own instance in the field, so the fleet view's job
  shifts from *simulating* N engines to *aggregating* N independent deployments' telemetry,
  which is a different architecture (a fleet-level aggregation service consuming each
  airframe's uplink, not a bigger version of `FleetRegistry`).
- **Per-tenant auth**, not one shared bearer token — Stage 3/the security section below's
  token needs to be scoped per airframe (or per squadron/operator), so one compromised
  ground link cannot inject faults or read diagnostics for an aircraft it has no business
  touching.
- **The ranking heuristic in `app/api/fleet.py::_urgency_key` earns real operational
  weighting** (mission criticality, time-to-next-sortie, spares availability) instead of
  the demo's fixed GO/CAUTION/NO-GO-then-health ordering, once there is a real maintenance
  scheduling system for it to feed.

---

## Security: from demo token to defence-grade

`app/core/security.py` implements a single shared bearer token, disabled by default. It is
deliberately minimal and is **not** presented as adequate for deployment. Its real value is
structural: every control mutation and the telemetry socket already pass through one choke
point, so the work below is contained rather than a rewrite.

What a real deployment needs:

| Concern | Today | Required |
|---|---|---|
| Transport | Plain WS/HTTP, CORS-limited | mTLS, pinned CA, no plaintext fallback |
| Identity | One shared secret | Per-airframe and per-operator certificates |
| Authorisation | All-or-nothing | Role separation — observers cannot inject faults or command |
| Token handling | `?token=` accepted on the WS handshake (browsers cannot set headers) | Short-lived ticket issued over the authenticated REST channel; query strings land in logs |
| Integrity | None | Signed telemetry frames so a recording cannot be silently altered |
| Key management | Env var | HSM or secure element, scheduled rotation, revocation |
| Audit | Application logs | Tamper-evident log of every command, with operator identity |
| Command path | Control endpoints mutate the sim | On a real vehicle these must not exist, or must be hard-gated — the PHM system is an observer, and a compromised PHM system must not be able to command an engine |

That last row is the important one. Fault injection is a simulation affordance. On real
hardware the control surface either disappears or is restricted to a ground test mode that
is physically incapable of reaching a flying vehicle.

---

## What would need genuine research

Being straight about the boundaries of the current design:

- **Model calibration** dominates the value and the effort. An uncalibrated twin produces
  confident residuals that mean nothing.
- **RUL against real run-to-failure data.** The current predictor extrapolates a health
  trend; there is no substitute for observing actual failures, and aero engines fail
  rarely by design. Expect to seed with accelerated bench testing.
- **Vibration is synthesised, not solved.** `vibration_model.py` produces a plausible
  spectrum from known fault content. Real crank/valvetrain dynamics, and real bearing
  defect frequencies, are a different modelling problem — and are the single richest
  diagnostic channel on a real engine.
- **Certification.** Nothing here has been developed to DO-178C/DO-254 or an equivalent
  process. A PHM system that influences continued-airworthiness decisions is subject to
  that scrutiny, and it constrains architecture from the start rather than being added at
  the end.
