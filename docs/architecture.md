# Architecture

## Data flow (physics + twin + PHM + persistence, replay and ingestion)

Phase 4's Test Bench runs this same chain headless and off to one side; see
[Test Bench & Operating-Point Optimizer](#test-bench--operating-point-optimizer-phase-4).

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

### Phase 3 additions to the flow

```
  ingestion boundary            persistence                 replay
  ────────────────────          ───────────                 ──────
  EngineDataAdapter             mission session active?     GET /control/missions
   ├─ SimulatedAdapter  ──┐       └─ repository.save_frame   POST /control/replay/start
   └─ CANBusAdapter (stub)│          (batched, 50 frames)          │
                          │       fault inject/clear                │ stored frames
                          ▼          └─ save_fault_event            ▼
                    RawEngineData                          ReplayEngine
                          │                                  │ same broadcast path,
                          ▼                                  │ is_replay = true
                  (physics -> twin -> PHM)  ────────────────►├──► /ws/telemetry
                          │                                  │
                          │  sensor faults applied HERE      │
                          │  (after physics, after twin)     │
                          ▼                                  │
                   TelemetryFrame ─────────────────────────► ┘
                          │
                          ├─► efficiency_analysis  (BSFC + trend)
                          ├─► maintenance_advisor  (what to actually do)
                          └─► mission_report       (on mission end)
```

### Where sensor faults enter, and why it matters

Every Phase 2 fault corrupts the **engine**. A Phase 3 sensor fault corrupts the
**reading**. That difference dictates its position in the pipeline:

```
physics computes true state
    -> digital twin computes its healthy prediction
        -> SENSOR FAULT APPLIED to the reported values only
            -> residual = reported - twin     <- still shows an anomaly
                -> anomaly detector / classifier
```

Because the corruption lands after the twin, a drifting EGT probe produces a residual that
looks superficially like a real combustion problem. What separates them is *correlation
structure*: a real mechanism moves every physically-linked channel together, while a
sensor fault moves exactly one and leaves its physical neighbours where the twin predicted.
`app/ml/fault_classifier.py` checks that directly via `PHYSICAL_CHANNEL_GROUPS`.

One subtlety worth recording: `egt_mean_c` and `egt_spread_c` are deliberately *not*
treated as corroborating each other. They are derived from the same thermocouples, so one
bad probe moves both — they are mathematically coupled, not physically coupled, and
counting one as evidence for the other would let a single failed sensor masquerade as a
real fault.

The uncorrupted state is kept in `DiagnosticSnapshot.true_state` and exposed only on
`GET /twin/diagnosis`, never on the telemetry stream — the dashboard has to infer
sensor-vs-physical the way a real ground station would, not be handed the answer.

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

## Test Bench & Operating-Point Optimizer (Phase 4)

Phases 1–3 answer *what is this engine doing, and is anything wrong with it?* Phase 4 adds
the other half of the question a mission planner actually has: **what would happen if?**

It is a second mode, not a second panel. `frontend/app/test-bench/page.tsx` is its own
route, and it deliberately does **not** mount `useTelemetryStream` — no WebSocket is opened
on that page at all, so there is no code path by which a scenario chart could receive a
live frame or a live gauge a simulated one. A persistent amber "SIMULATION — NOT LIVE DATA"
banner makes that visible rather than merely true: a chart of cylinder head temperature
looks identical whether the numbers came from a running engine or a hypothetical one, and
confusing the two in front of an evaluator would be worse than not building the feature.

### The two halves

```
   ┌───────────────────────────────────────────────────────────────────────────┐
   │  POST /simulate/scenario          app/sim/scenario_engine.py               │
   │                                                                            │
   │  altitude, OAT, duration, throttle profile,                                │
   │  pre-existing wear, faults scheduled mid-run                               │
   │            │                                                               │
   │            ▼                                                               │
   │  relax_to_steady_state()  ── warm start, both plants                       │
   │            │                                                               │
   │            ▼                                                               │
   │  SAME stack as the live loop, with no wall clock and no socket:            │
   │    EnginePlant + DigitalTwin  →  ResidualMonitor  →  AnomalyDetector       │
   │    →  RULPredictor  →  MissionReliabilityModel  →  MaintenanceAdvisor      │
   │            │                                                               │
   │            ▼                                                               │
   │  TelemetryFrame[]  +  PASS / CAUTION / FAIL summary  →  scenario_runs      │
   └───────────────────────────────────────────────────────────────────────────┘

   ┌───────────────────────────────────────────────────────────────────────────┐
   │  POST /optimize/operating-point   app/ml/operating_point_optimizer.py      │
   │                                                                            │
   │  objective ∈ {max_range, max_power, max_engine_life, balanced}             │
   │  search over throttle × afr_trim × injection_timing_trim                   │
   │    coarse bounded grid  →  bounded Nelder-Mead (multi-start)               │
   │    objective = relax_to_steady_state() on the SAME physics                 │
   │    HARD constraints: CHT, EGT, oil temp, oil pressure                      │
   │            │                                                               │
   │            ▼                                                               │
   │  recommended setpoint  +  structured comparison against the book setting   │
   └───────────────────────────────────────────────────────────────────────────┘
```

### Why the physics is stepped differently here

The live loop integrates everything at 20 ms because it has to resolve manifold filling
(τ = 120 ms). Reaching *thermal* equilibrium at that step takes about 2 000 simulated
seconds — 100 000 sub-steps — which is fine once and hopeless as an optimizer's objective
function, where it is needed hundreds of times.

`app/physics/steady_state.py` exists because this plant's fast and slow dynamics are
genuinely decoupled: `EngineModel.step()` takes throttle, altitude, airspeed, ambient
temperature and the fault state, and **no temperature from the thermal model**. Head and
oil temperature are downstream of the thermodynamic core, never upstream of it. So the
engine is settled at a fine step, its heat output then becomes a constant boundary
condition, and the two thermal ODEs are relaxed at a 10 s step sized for their own ~250 s
and ~440 s time constants. Same model objects, same equations, each stepped at the rate it
actually needs — about 20 ms of CPU per operating point instead of several seconds.

The scenario engine uses the same routine to *warm start*: `EnginePlant.reset()` leaves a
cold crank with oil pressure at its 15 kPa floor, and integrating forward from there makes
the first minutes of every scenario a start-up transient — an oil gauge still on its way up
gets recorded as a lubrication excursion the engine never had.

Scenario runs are integrated at 20–50 ms sub-steps (widening under a fixed budget for long
durations, never past the manifold stability limit) and sampled at ~1 Hz, capped at 1800
returned frames. A 20-minute scenario computes in about 5 s, an hour in about 7 s, four
hours in about 20 s — roughly 200–750× real time.

### Refusing rather than extrapolating

`validate_scenario_params()` returns *reasons*, not a boolean, and a 400 carries them:

```
altitude_m=11000 is outside the modelled range 0-8000 m. Above the tropopause the ISA
column becomes isothermal and the turbocharger map in turbo_model.py is no longer being
used near its calibrated region.

ambient_temperature_c=45.0 is ISA+102 K at 11000 m (ISA there is -56.5 degC). The cooling
and density corrections are only modelled for ISA-40 K to ISA+50 K.
```

The 8 000 m ceiling is the modelled service ceiling of *this powerplant*, not of the ISA
model: above it the mean-value engine makes under ~14 kW, which will not sustain a MALE
UAV, and the thermal model — which has no oil thermostat and only a throttle-scheduled
cooling flap — settles the oil below freezing. The mixture trim is bounded the same way,
to the 12.4–16.5 AFR band the schedule already covers, because a mean-value model has no
oxygen limit on the rich side and would otherwise predict ever more power from ever more
fuel. An answer produced by extrapolating past a model's calibrated region looks exactly
like a real answer, and a planner has no way to tell.

### Hard safety bounds, and what wear does to them

CHT, EGT, oil temperature and oil pressure limits live in `engine_params.py` in two bands:
`*_caution_*` (the edge of the normal envelope, matching `HEALTHY_BANDS` in
`frontend/lib/types.ts`, so an amber tile and a caution excursion agree) and `*_limit_*`
(the red line). The optimizer treats the red lines as **hard**: the returned point is
always re-evaluated and its per-limit margins reported, and where no candidate is safe the
result says `feasible: false` with the failing checks attached rather than dressing up the
least-bad point as a recommendation.

`current_health_state` enters twice. Through the *physics* — candidates are evaluated on an
engine that actually carries that wear, so a worn bearing's lost oil pressure is in every
number. And through the *limits* — worn hardware gets lower CHT/EGT/oil-temp ceilings, a
higher oil-pressure floor, and a reduced continuous power rating. That last one is what
makes `max_power` on a degraded engine recommend *less* power than the book setting rather
than winding the throttle open: at 2 400 m the derated ceiling falls 47.4 → 41.0 → 35.6 →
31.4 kW as accumulated wear rises 0 → 0.30 → 0.55 → 0.75, and past that the honest answer
becomes "no safe setpoint exists".

### The mixture and timing levers

Phase 2 fixed AFR and injection timing to internal schedules. Phase 4 exposes a *trim* on
each — the two extra search variables — and both are genuine physics in this model, not
new equations bolted on for the optimizer:

- **Mixture.** Leaning improves combustion efficiency toward stoichiometric but moves EGT
  toward its peak and, because a lean charge sends a larger share of its residual heat into
  the head rather than out of the pipe (`heat_to_head_afr_exponent`), raises CHT sharply.
  Enriching is a charge-cooling lever that costs fuel. This is exactly why real aero engines
  run rich of peak at high power.
- **Timing.** Nominal is MBT, so any trim costs combustion efficiency. What it buys is a
  CHT-versus-EGT trade: advance keeps heat in the cylinder, retard sends it out of the valve.

Both are set on the digital twin as well as the real engine, for the same reason ambient
temperature is: a commanded setpoint is an operating *condition*, not a fault. A twin still
flying the book mixture while the engine runs trimmed would show the difference as a
residual, and the PHM layer would report an operator's deliberate lean as a developing fuel
fault.

### How this satisfies the problem statement

**PS Section E — environmental and throttle-transition simulation.** Section E asks the
system to simulate operation across altitude, ambient temperature and throttle transitions.
Phase 3 could *apply* a hot day to the live simulation and wait for it in real time. The
Test Bench lets an operator specify altitude, outside air temperature, a throttle profile
(constant, or a ramp from X% to Y% over Z minutes then held) and the engine's existing
condition, and get the whole trajectory back in seconds — with the throttle transition
integrated at the same 20 ms sub-step the live loop uses, so manifold filling, turbo spool
and thermal lag are all resolved rather than interpolated. The PASS/CAUTION/FAIL verdict is
taken from the *worst* point of the run, because a sortie that dipped into NO-GO halfway
through and recovered is precisely the one worth knowing about before flying it.

**Autonomous maintenance advisory systems.** The Phase 3 advisor reacts to what has already
happened. The Test Bench makes it *predictive*: a scenario carries the same
`MaintenanceAdvisor` output at scenario end, so a planner can ask "if I fly this profile on
the engine I have, what will the ground crew be told afterwards?" before committing to it.
The optimizer extends the same idea to a decision rather than a warning — every
recommendation comes with an estimated life impact if sustained, so "back off 20% power"
carries a number attached to it instead of being a hunch.

**Physics-informed AI.** The optimizer's objective function *is* the physics model. There
is no surrogate, no regression fitted to simulation outputs, no black box asked to guess a
setpoint — every candidate is evaluated by relaxing the real `EnginePlant` to steady state,
and the constraints that bound the search are the same thermal and lubrication equations
the digital twin runs. That is what makes the safety guarantee meaningful: the optimizer
cannot propose a setpoint the engine model itself predicts would exceed a limit, because
the model is what evaluated it. The stress-rate index the life objective minimises is
likewise physically motivated — Arrhenius-style doubling laws on head and oil temperature,
squared vibration amplitude for fatigue, inverse-square oil pressure for film thickness —
and normalised so nominal cruise reads 1.0. It is a defensible *ordering* of operating
points, not a calibrated life model; see `docs/deployment-roadmap.md`.

### What is deliberately not shared with the live path

- The scenario engine builds its own `EnginePlant` and `DigitalTwin`. It never touches
  `app.state.sim`, never broadcasts, and never writes to `telemetry_frames`. A scenario can
  run while a mission is recording and neither notices.
- Both Phase 4 routers hand their CPU-bound work to a worker thread (`run_in_threadpool`)
  through the single slot in `app/core/compute_budget.py`. State mutation stays on the
  event loop, preserving the single-threaded invariant the simulation loop relies on.

### What heavy Test Bench work costs the live stream

Getting the compute off the event loop's *ordering* is necessary but not sufficient: it
still holds the GIL, and the live tick — which is itself several milliseconds of Python
per 100 ms — has to win the interpreter back to broadcast. Measured on a 16-core host with
a mission recording, against a 120-minute scenario followed by an optimiser search:

| | cadence | p50 gap | p95 gap | worst gap |
|---|---|---|---|---|
| Idle | 9.3 Hz | 108 ms | 110 ms | 112 ms |
| First attempt (threadpool only) | 6.7 Hz | — | — | **1048 ms** |
| \+ one compute slot, 1 ms switch interval | 7.3 Hz | — | — | 418 ms |
| \+ explicit GIL yields | **7.7 Hz** | 124 ms | 173 ms | **185 ms** |
| Recovered, load finished | 9.2 Hz | 109 ms | 110 ms | 112 ms |

The residual ~1.6 Hz is the honest cost of two CPU-bound Python workloads sharing one
interpreter, and it recovers the moment the request finishes: no frames are dropped, no
socket is disconnected, and the dashboard's 600-frame buffer absorbs a 185 ms hiccup
without a visible discontinuity. Moving the Test Bench into its own process would remove
it entirely and is the right answer if this ever runs on constrained edge hardware — see
`docs/deployment-roadmap.md`.
- `scenario_runs` is a separate table from `missions`. A mission records something that
  happened; a scenario records something that was *asked*. Merging them would put
  hypothetical flights into the maintenance history and force the mission report, the
  replay engine and every hours count to learn to exclude them.
- The optimizer only ever *reads* live health. Applying a recommendation is an explicit
  operator action through `/control/apply-preset` or `/control/setpoint`. A PHM system that
  could silently retrim a running engine because it thought that was a good idea is exactly
  the design `docs/deployment-roadmap.md` argues against.

## Sensor Fusion (Phase 5)

The PS names "sensor fusion" directly in its Technical Expectations. Before this phase,
what the system actually had was a correlation heuristic:
`app/ml/fault_classifier.py::classify_source` asked "did this channel's physically-linked
neighbours move with it?" and called that "sensor fault" when the answer was no. That is
a reasonable inference, but it is an *inference about absence* — it never compares two
independent readings of the same physical quantity against each other, because before
this phase nothing in the system produced two independent readings of anything. Phase 5
adds an actual Kalman-filter fusion layer for three channels, so the disambiguation this
project has claimed since Phase 3 is now backed by a number instead of a heuristic.

**The filter.** `app/fusion/kalman_filter.py` is one small, reusable scalar Kalman filter
— `predict()` (a process model, or a random walk if none is supplied) then one or more
sequential `update()` calls, each exposing its own innovation and gain. It knows nothing
about CHT, RPM or oil pressure; each fusion channel below is a thin, differently-shaped
wrapper around the same equations. `predict()` scales its process variance by `dt_s /
REFERENCE_DT_S` rather than adding a fixed amount per call — every fusion channel is
stepped once per tick, and a tick's simulated duration ranges from ~20 ms to several
seconds depending on the demo's time-acceleration setting. Missing this scaling was a
real bug caught by `scripts/stability_checks.py`: the fused RPM trajectory at 20x
diverged 214 rpm from the identical mission at 1x (tolerance 30 rpm) before this was
anchored to simulated seconds the same way every other time constant in this codebase
already is (`ResidualMonitor`, `AnomalyDetector`, `RULPredictor`).

**CHT — two independent probes, no process model.** `app/fusion/cht_fusion.py` adds a
second, independently-noised simulated CHT probe next to the existing one — each drawn
from the same true physical temperature (`thermal_model.py`) but corrupted separately, so
either can fail without the other. The "process model" is deliberately just a random
walk with a small Q: CHT has enough thermal mass that it cannot move far between ticks, so
two sensors arguing about where it currently sits is the entire estimation problem.

A plain Kalman filter is *not* naturally robust to one sensor lying — it blends
measurements by their declared variance, and that variance does not shrink just because a
sensor started drifting. Verified directly while building this: without correction, a
secondary probe drifting 40+ degC away from truth pulled the fused estimate roughly
halfway to it, which defeats the entire point of fusing two sensors. The fix
(`kalman_filter.adaptive_measurement_variance`, paired with a slow `InnovationTracker` per
sensor) inflates a sensor's *effective* variance when its own recent disagreement with the
fused estimate has grown well beyond its noise floor — using only that sensor's own
behaviour, never ground truth. That is what lets the fused estimate stay within a fraction
of a degree of the true CHT throughout an isolated secondary-probe fault (see
`backend/scripts/output/09_sensor_fusion.png`), while the secondary's own innovation
against the fused estimate grows into a large, isolated, unmistakable signal.

**RPM — two different physical principles, not two instruments.** `app/fusion/rpm_fusion.py`
fuses the tachometer against an RPM estimate derived from the vibration model's own
FFT-detected dominant frequency (`RPM = f_fire * 120 / n_cylinders` for this 4-stroke,
4-cylinder engine — inverting the same relation `vibration_model.py` uses to *synthesise*
the signal, read back out of the rolling buffer's own spectrum rather than the ground
truth used to generate it). This pair has a genuine, stated limitation: with only two
sources and no third reference, the filter cannot determine *which* of two persistently
disagreeing sources is correct — it settles toward whichever one it trusted more before
the disagreement started, which is not the same thing as identifying the truth. Resolving
that needs the independent evidence `app/ml/fault_classifier.py::_classify_rpm` actually
checks: whether cylinder-level vibration RMS (an amplitude measurement this fusion pair
never looks at) is independently elevated. Elevated RMS alongside a disagreeing
vibration-derived RPM reads as a real mechanical issue; a disagreeing tachometer with RMS
untouched reads as `rpm_sensor_stuck`.

A second, narrower issue surfaced and is worth recording even though it is now resolved:
`scripts/stability_checks.py` compares a mission flown at 1x time-scale against the
identical mission at 20x, and RPM (uniquely among the three fused channels) diverged
during one specific moment — a sharp mission-phase transition (loiter-to-descent) with two
faults ramping simultaneously — because the vibration-derived RPM comes from an FFT
snapshot taken once per *tick*, and a tick's simulated duration is 20x longer at 20x than
at 1x. During a genuinely fast transient, that coarser snapshot samples a different
instant of the same transition at each time-scale, and occasionally locked onto a
spurious spectral bin under `bearing_wear`'s broadband noise entirely (one such tick
implied ~7500 rpm against a true ~2000 rpm). Three fixes closed it in sequence: clamping
the vibration-derived estimate to a physically plausible RPM range (catching the outlier
before it can be trusted at all), widening the vibration source's measurement variance
when the fused RPM's own rate of change is high (`rpm_vibration_transient_widening_per_
rpm_s`, de-weighting the coarse snapshot specifically during a transient), and the
dt-scaling fix described above. The same scenario went from 214 rpm of divergence to 10.6
rpm (tolerance 30) — `scripts/stability_checks.py` now passes cleanly on every channel.

**Oil pressure — the textbook predict/update pair.** `app/fusion/oil_pressure_fusion.py`
is the one channel with an actual process model: `lubrication_model.py`'s own pressure
equation, evaluated at zero wear, is the PREDICT step, and the raw sensor is the UPDATE
step. The interesting lever is `process_variance_q`, which widens the moment
`bearing_wear` or `oil_pump_degradation` crosses a small suspicion threshold — read
directly from `FaultState`, the same ground truth every other physics module already
reads to compute its own outputs, at the same physics layer (the classifier downstream
never sees `FaultState`, only this fusion's innovation and gain). Widening Q shifts trust
toward the sensor, which produces two distinguishable signatures for the same symptom
(a persistent innovation): a sensor fault leaves Q narrow and the gain low, because
nothing has told the filter its own model is wrong; a real lubrication fault widens Q and
the gain visibly climbs toward 1, because the zero-wear model's own prediction confidence
is what actually degraded. `oil_pressure_kalman_gain` — and its deviation from a slow
healthy baseline, `oil_pressure_gain_deviation` — is exposed on `TelemetryFrame` and fed to
the classifier specifically so that shift is visible as data, not just as an internal
implementation detail.

**What actually consumes the fused value.** `SimulationLoop.tick()` writes each fused
result back onto `PlantState` (`real_state.cht_c`, `.rpm`, `.oil_pressure_kpa`) *before*
`DigitalTwin.compare()`, the residual monitor, the anomaly detector and the RUL predictor
ever run. None of those four modules changed at all — they still read the same three
attributes they always have. What changed is that those attributes now hold a Kalman
best-estimate from two independent sources instead of one noisy sensor, for every
downstream consumer, with no separate code path for "the fused version" to be forgotten or
bypassed.

**Classifier features.** `app/fusion/fusion_monitor.py` gives the six fusion signals
(two CHT innovations, two RPM innovations, the oil-pressure innovation and its gain
deviation) the identical EWMA-mean/z/std treatment `app/twin/residual_analysis.py` gives
real-vs-twin residuals — deliberately a *separate* small monitor rather than a forced fit
into that one, because a fusion innovation is a different comparison axis entirely (raw
sensor vs. fused estimate, never involving the digital twin, which has no sensors to fuse
in the first place). `app/ml/fault_classifier.py::classify_source` checks these six
signals *before* falling back to the original correlation heuristic, and it has to: fusion
is specifically built to keep a fused channel's residual against the twin looking normal
even while one of its two sources lies, so waiting for that channel to become the
"loudest moved" residual — the correlation heuristic's own trigger — would miss exactly
the case this layer exists to catch. Every channel Part B did not build a fusion pair for
(EGT, fuel, battery, ...) still falls through to that original heuristic, unchanged.

## Fleet-Level Health Monitoring (Phase 6)

Every phase through 5 simulates one engine. Phase 6 generalises the backend to N (three)
independently simulated UAVs — "UAV-01", "UAV-02", "UAV-03" — without changing what any
single UAV's simulation *is*: `SimulationLoop` itself gained zero new lines for this phase.
What changed is everything one layer up, that used to assume there was exactly one of it.

**`FleetEntry` and `FleetRegistry` (`app/core/fleet_registry.py`).** A `FleetEntry` is a
`SimulationLoop` plus its own `ReplayEngine` — the two pieces of *per-engine* mutable state
control.py used to reach for as `app.state.sim` and the module-level `replay_engine`
singleton. `ReplayEngine` needed no code change at all to become per-UAV: it already took
its `broadcast` callback as a parameter to `start()` rather than importing the telemetry
manager directly, so three independent instances just work. `FleetRegistry` holds one
`FleetEntry` per uav_id in a plain dict; `app.state.fleet` is the source of truth built
once at startup by `build_default_fleet()`, and `app.state.sim` is kept as an alias to
`fleet.default.sim` purely so code that has not been made uav_id-aware (chiefly the Phase 1
mock generator, which never will be — see `app/main.py::_run_mock`) keeps working exactly
as it did.

**Continuity with everything recorded before this phase.** UAV-01 is not a fresh engine —
it is deliberately the *same* engine every earlier phase's Lifecycle feature was already
tracking under `engine_id="primary"`. `fleet_registry.lifecycle_engine_id(uav_id)` maps
UAV-01 back onto `DEFAULT_ENGINE_ID` ("primary") and every other UAV onto its own id, so a
database that already had missions and accumulated wear before this phase existed sees
UAV-01 pick up exactly where "the engine" left off, rather than starting over under a new
key. The `missions` table needed an actual schema change to carry this — the first one in
the project's history to alter an already-shipped table rather than add a new one (see
"the migration" below) — and its new `uav_id` column defaults to "UAV-01" for every
pre-existing row, which is the other half of the same continuity guarantee.

**Pre-seeded starting wear, once.** Task 1's requirement — a realistic, differentiated
fleet the moment the backend starts, without requiring a mission run on each UAV first — is
`fleet_registry._SEED_WEAR_STATES` plus `seed_fleet_wear()`. UAV-02 gets moderate wear
spread across a few subsystems; UAV-03 gets `bearing_wear` pushed close to a maintenance
threshold, chosen because it shows up across several channels at once (vibration, oil
pressure, RPM stability) and so reliably produces the worst health score of the three. The
seed only fires when a UAV's `engine_lifecycle` row is genuinely fresh (`_is_fresh_lifecycle`
— zero hours, zero wear); on every later restart, whatever the UAV has actually accumulated
since is what gets loaded instead, so the seed cannot silently overwrite real history. This
was verified directly: three UAVs boot to health scores of 100 / ~89 / ~43 with no mission
run, `/fleet/rankings` places UAV-03 first, and after 25 more seconds of idle ticking the
seeded wear had visibly propagated through the residual/anomaly chain into a real NO-GO / a
real CAUTION rather than staying a static injected number.

**The migration (`app/db/session.py::_ensure_column`).** SQLite's `ALTER TABLE ... ADD
COLUMN` is metadata-only and never rewrites existing rows, which is exactly why every
earlier phase avoided needing one — a new *table* is something `Base.metadata.create_all()`
already handles for free, but a new *column* on a table that already exists is not.
`_ensure_column` is a deliberately narrow substitute for a real migration framework (there is
no Alembic in this project): it checks `PRAGMA table_info(table)` for the column and runs
the `ALTER TABLE` only if it is missing, called once from `init_db()` right after
`create_all()`. `Mission.uav_id` uses a SQL-level `server_default`, not just a Python-side
`default=`, specifically so this backfill gives every pre-existing row a real value rather
than `NULL`.

**Every per-engine endpoint gained a `uav_id` query parameter**, defaulting to
`DEFAULT_UAV_ID` ("UAV-01") so an old caller — or the frontend before its own uav_id
threading — gets exactly the engine it always got. This is genuinely every route in
`control.py`, plus `/twin/diagnosis`, `/optimize/operating-point` (only when
`use_current_engine_health` is set — the optimizer never reads live state otherwise),
`/lifecycle/summary` and `/lifecycle/maintenance-action`, and `/performance-maps/live-point`.
`/simulate/scenario` and its siblings deliberately did **not** gain one: a Test Bench run's
starting wear comes from `initial_fault_severities` in the request body, never from a live
engine, so the computation is identical regardless of which UAV happens to be selected in
the UI at the time — threading a dead parameter through it would have been noise. An
unknown `uav_id` on any route that does need one is a 404, not a `KeyError` leaking out as
a 500 (`control.py::_entry`).

**WebSockets.** `ConnectionManager.active` became `dict[str, list[WebSocket]]` keyed by
uav_id; `/ws/telemetry?uav_id=...` only ever receives that UAV's frames, and
`broadcast_json` is kept as a thin alias for "broadcast to the default UAV" so nothing
calling it needed to change. A second, separate manager (`FleetOverviewConnectionManager`)
backs the new `/ws/fleet-overview` — a single shared list, not keyed by UAV, since every
subscriber there watches the same fleet-wide ranking — pushed every ~1.5 s by
`run_fleet_overview_broadcast()`, a second background task alongside `run_simulation()`
rather than piggy-backing on its 10 Hz loop, so a slow fleet-overview subscriber can never
throttle live telemetry.

**Ranking (`app/api/fleet.py`).** `GET /fleet/overview` and `GET /fleet/rankings` share one
`_uav_snapshot()` — overall health, worst subsystem, RUL, both recommendation ladders,
active fault count, lifetime hours — read straight off each UAV's `get_latest()` frame plus
its lifecycle ledger; `rankings` just sorts that same list by `_urgency_key`, most-urgent
first. Urgency sorts by `mission_reliability`'s ladder first (NO-GO outranks CAUTION
outranks GO), then `recovery_reliability`'s, then raw health ascending as a tiebreaker — a
UAV that cannot be trusted to finish its mission outranks one that merely cannot finish it
gracefully. `build_fleet_overview()` is the one function both the REST routes and the
WebSocket broadcast loop call, so the two views can never disagree.

**Frontend: one more independent zustand store, not a rewrite of the other three.**
`lib/fleet/store.ts`'s `useFleetStore` holds `selectedUavId` — the single piece of state
every other per-engine view now scopes its requests to — plus the polled `/fleet/rankings`
roster for the new Fleet page. `lib/store.ts`, `lib/lifecycle/store.ts` and
`lib/testbench/store.ts` each read `useFleetStore.getState().selectedUavId` at request time
(a `withUav()` helper appends it as a query param) rather than importing one merged store,
matching this codebase's existing pattern of independent per-page stores. Reacting to a
switch — resetting the live buffer, re-fetching missions and mission status, reloading the
lifecycle summary — is done via `useFleetStore.subscribe(...)` registered once at each
store's module scope, not inside a component effect, so the reset behaviour is identical no
matter where the switch was triggered from (the Fleet page's roster cards, or
`MissionHeader`'s persistent selector). `useTelemetryStream` is the one exception that has
to live inside a component: it owns a `ReconnectingSocket` across renders, so `uavId` is a
plain `useEffect` dependency there, tearing down the old UAV's socket and opening the newly
selected one's.

## Early Warning System (Phase 7)

Every phase through 6 answers "is this now anomalous?" — `ResidualMonitor`'s z-score gate
(`app/twin/residual_analysis.py`) is the system's **existing hard fault-alert threshold**:
a channel is flagged when its residual has drifted more than `z_threshold` (3.0) standard
deviations from its own recent noise, and that flag is what ultimately drives the anomaly
detector, the fault classifier and `active_faults` — the field FaultAlertFeed renders.
Phase 7 adds a second, deliberately softer and earlier tier **in front of** that gate,
answering a different question: not "is this now anomalous" but "has this channel's
*behaviour* started to change, even while its current value still looks unremarkable?"
Nothing in this phase touches `ResidualMonitor`, the anomaly detector, the classifier, or
`active_faults` — every existing hard-alert code path is unmodified, and
`scripts/hardening_checks.py`'s 14/14 checks (including the twin-isolation and
recovery-after-clear checks that most directly exercise that path) reproduce the identical
numbers recorded before this phase.

```
                    ┌─────────────────────────────────────────┐
                    │  app/twin/digital_twin.py                │
                    │  residual = real[channel] - twin[channel]│
                    └──────────────────┬────────────────────────┘
                                       │  identical residual dict, every tick
                    ┌──────────────────┴──────────────────────┐
                    ▼                                          ▼
   ┌─────────────────────────────────┐      ┌─────────────────────────────────────┐
   │ ResidualMonitor (existing,       │      │ PreAlertMonitor (NEW)                 │
   │ unmodified)                      │      │ app/twin/residual_analysis.py         │
   │  EWMA mean/variance, z-score     │      │  per-channel rolling history,         │
   │  flagged = z >= z_threshold      │      │  variance-ratio test + trend-slope    │
   │  = the existing hard alert       │      │  test, each independently gated       │
   └──────────────────┬────────────────┘      │  level = none / emerging / building   │
                      │                        └──────────────────┬─────────────────────┘
                      ▼                                            │ worst-of-subsystem-channels
      classifier / active_faults / FaultAlertFeed                  ▼
                                             ┌─────────────────────────────────────┐
                                             │ RULPredictor.update_early_warnings    │
                                             │ app/ml/rul_predictor.py               │
                                             │ SAME trend fit as critical RUL,       │
                                             │ target HI=75 not HI=40, gated on      │
                                             │ pre-alert >= "emerging", SAME          │
                                             │ asymmetric-EMA rate limiting          │
                                             └──────────────────┬─────────────────────┘
                                                                 ▼
                                             ┌─────────────────────────────────────┐
                                             │ MaintenanceAdvisor                    │
                                             │  .generate_early_warning()            │
                                             │ app/ml/maintenance_advisor.py         │
                                             │ one concrete, subsystem-specific      │
                                             │ instruction + basis                   │
                                             └──────────────────┬─────────────────────┘
                                                                 ▼
                                             TelemetryFrame.early_warnings  (NEW, additive)
                                                                 ▼
                                             frontend/components/dashboard/
                                             EarlyWarningBanner.tsx  (NEW)
```

### Two independent tests, and why each catches a different kind of onset

`pre_alert_check` (a pure function over one channel's `(t, residual)` history) runs two
statistically-gated tests that look for genuinely different shapes of onset, matching how
differently this codebase's own faults actually announce themselves (see
`docs/physics-model.md`'s fault table — some faults are smooth mean shifts, others add
noise):

* **Variance-ratio** — recent-window variance vs. an established baseline-window variance
  immediately before it, both windows requiring a minimum sample count before the test is
  even evaluated. Catches a fault whose real signature is *added noise* — `bearing_wear`'s
  broadband vibration is the model's own example — before the *mean* has moved enough to
  trip a z-score gate keyed on the mean.
* **Trend-slope** — an OLS fit over the recent window, tested as a **t-statistic against
  its own standard error** (so "significant" scales with how noisy the channel already is,
  not a fixed slope in physical units), additionally required to be **sustained**: at
  least 55% of the window's individual step-to-step deltas must share the fitted slope's
  sign, which is what stops one large single-tick jump plus flat noise from reading as a
  trend. Catches a fault whose signature is a smooth mean shift — `bearing_wear`'s own
  effect on `oil_pressure_kpa` — long before the mean has moved the several sigma a
  z-score gate requires.

A channel is `emerging` when exactly one test fires, `building` when both do. A subsystem
is gated in when the *worst* of its own channels (the identical `SUBSYSTEM_CHANNELS`
mapping `AnomalyDetector` already uses) reaches at least `emerging` — the same granularity
`MaintenanceAdvisor.generate_early_warning` and `TelemetryFrame.early_warnings` both
operate at.

**A bug caught by validating this, not by inspection.** The first working version had no
explicit warm-up: three real and healthy-twin plants share an identical cold start, and the
mission's own start-up transient (RPM ramping off idle, manifold filling) briefly perturbs
the residual before the two plants settle into lockstep. With only a 10-sample minimum, the
trend test tripped on that shared transient within the first few *seconds* of a healthy
mission — a false positive with nothing to do with any fault. `PreAlertMonitor` now holds
every channel at `none` until a full baseline-plus-recent window (195 s by default) has
actually elapsed since the monitor was created, mirroring `ResidualMonitor`'s own
`warmup_s` pattern. Verified: a healthy 400-simulated-second run (crossing the climb→cruise
mission-phase boundary, itself a real transient the twin tracks identically) now produces
zero non-`none` pre-alert levels on any of the six subsystems throughout.

### Time-to-warning-threshold: the same trend fit, gated, rate-limited the same way

`RULPredictor.update_early_warnings` deliberately does not duplicate the trend-fitting
logic that already produces the critical RUL. It calls the identical `_estimate_for` — now
parameterised with an optional target threshold — against a **gentler** line,
`HI_WARNING_THRESHOLD = 75` (the critical RUL threshold stays `HI = 40`, unchanged), and
only for subsystems the pre-alert gate above has already flagged; a subsystem still at
`none` gets no estimate at all — "return null rather than guessing from noise", per the
brief, rather than fitting a regression to what is still statistical noise.

It also reuses this file's own fix for the RUL-jump bug (`docs/test-report.md`'s Fix 4):
the identical asymmetric EMA — slow to rise (`smoothing_tau_s`, 20 s), fast to fall
(`smoothing_tau_fall_s`, 3 s) — applied per-subsystem to the warning-threshold estimate.
Without it, a warning ETA seeded high the instant a trend first becomes fittable would
reproduce the exact "worse degradation reads as more time remaining" inversion that fix
closed once already for the critical RUL.

`confidence` (`low`/`medium`/`high`) is read off how much simulated-time *span* of
consistent history the fit actually rests on — not raw sample count, which is a
tick-rate-dependent proxy — so a trend three seconds past the minimum-sample floor reads
`low`, and one that has held for three or more minutes reads `high`, matching the brief's
own two examples directly.

### One concrete instruction, not a bare number

`MaintenanceAdvisor.generate_early_warning` is additive to, and independent of, the
existing `evaluate()` — the two can both produce output for the same subsystem on the same
frame (pre-alert typically fires well before a subsystem's HI has fallen far enough for
`evaluate()`'s own `HI_WATCH` cut to notice), and neither suppresses the other. Each
subsystem has its own concrete, in-flight action distinct from `SUBSYSTEM_ACTIONS`'
post-flight ground-crew language — "Reduce throttle to roughly 70% to slow thermal
buildup" for cooling, "Monitor oil pressure closely; avoid further RPM increases" for
lubrication — and escalates to suggesting an early RTB once the projected time to the
warning threshold falls to 20 minutes or under. `predicted_minutes` and `confidence` are
kept as separate structured fields rather than folded into the action text, so
`EarlyWarningBanner.tsx` can render "confidence: low" in place of a number without the
recommendation sentence itself needing two different phrasings.

### Lead time, measured — not assumed

`scripts/validate_physics.py --scenario early-warning` runs `bearing_wear` ramped over 1,
5 and 15 minutes and records, for each: the first tick pre-alert reaches at least
`emerging` on the `lubrication` subsystem, the first tick any of that subsystem's channels
crosses the *existing, unmodified* z-score gate, and the first tick
`mission_reliability.recommendation` reads `NO-GO` — i.e. what the system would have
reported with no early-warning layer at all. Real measured output
(`scripts/output/10_early_warning_lead_time.png`):

| Ramp | pre-alert | hard alert (existing gate) | NO-GO | lead vs. hard alert | lead vs. NO-GO |
|---|---|---|---|---|---|
| 1 min | 5 s | 24 s | 43 s | **19 s** | **38 s** |
| 5 min | 8 s | 29 s | 89 s | **21 s** | **81 s** |
| 15 min | 39 s | 52 s | 162 s | **13 s** | **123 s** |

Two honest things worth stating plainly rather than smoothing over:

* **These are small numbers, and that is the correct result, not a bug in the detector.**
  A fixed "~7 minutes" figure would have been fabricated — read literally, the brief's own
  worked example warns against exactly that. The actual lead time here is governed by how
  fast `bearing_wear`'s dominant channel, `oil_pressure_kpa`, already crosses the
  **existing, unmodified** z-score gate: `CHANNEL_SCALES["oil_pressure_kpa"]` is 40 kPa
  against a healthy operating range of roughly 250–450 kPa, so even a small, consistent
  mean shift clears `z_threshold = 3.0` quickly once `ResidualMonitor`'s own 6 s EWMA has
  caught up to it — traced directly: at the 15-minute ramp's hard-alert tick, injected
  severity is only 0.049 (t = 52 s of a 900 s ramp). The pre-alert layer cannot manufacture
  lead time against a gate that is already this fast for this specific fault/channel pair
  without becoming noise-driven itself, and it was deliberately not tuned to do so — see
  the two tests' significance thresholds above.
* **Lead time vs. NO-GO is the more decision-relevant number, and it scales exactly as it
  should**: 38 s → 81 s → 123 s as the ramp slows from 1 to 15 minutes, monotonically. That
  is the gap between "a specific action is now recommended" and "the mission would have
  been called NO-GO with no warning that it was coming" — the actual question an operator
  is asking — and it grows with how much runway a slower-developing fault genuinely leaves.
  Lead time against the raw hard-alert gate does *not* scale the same way here, for the
  reason above: both the pre-alert layer and the existing gate react within roughly a
  minute regardless of ramp speed, because `oil_pressure_kpa`'s existing sensitivity
  dominates over the fault's own rate of development at this severity and channel.

**Not separately measured, and worth stating as a limitation rather than a claim**: other
fault/channel pairs whose existing hard-alert channel is less sensitive than
`oil_pressure_kpa` (a wider `CHANNEL_SCALES` value, a slower-reacting subsystem) would be
expected to show a larger lead time against the hard alert specifically, since the pre-alert
layer's own reaction time is set by the same fixed windows (45 s recent / 150 s baseline)
regardless of which channel it is watching. This has not been run for every fault type —
`scripts/validate_physics.py --scenario early-warning` currently covers `bearing_wear`
only, per the brief's "at least one representative fault."

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
| `app/twin/residual_analysis.py` | EWMA statistics, z-scores, shared ML feature layout; **Phase 7** — `PreAlertMonitor`/`pre_alert_check`, the earlier variance-ratio + trend-slope tier |
| `app/ml/anomaly_detector.py` | Per-subsystem anomaly scores and health indicators |
| `app/ml/fault_classifier.py` | RandomForest inference with confidence fallback |
| `app/ml/rul_predictor.py` | HI trend fitting and extrapolation to failure; **Phase 7** — `update_early_warnings`, the same fit against a gentler threshold, gated by pre-alert |
| `app/ml/mission_reliability.py` | Weibull survival over the remaining mission |
| `app/ml/train/*` | Offline data generation and classifier training |
| `app/api/ws_telemetry.py` | `/ws/telemetry` connection manager and broadcast |
| `app/api/control.py` | `/control/*` — fault inject/clear, throttle, time-scale, phase |
| `app/api/twin_diagnostics.py` | `/twin/diagnosis` — residuals and classifier output |
| `app/api/health.py` | `/health` liveness |
| `scripts/validate_physics.py` | Headless mission + fault, and the throttle-transient scenario; writes validation plots; **Phase 7** — `--scenario early-warning`, the real lead-time measurement |
| `app/core/security.py` | Bearer-token guard for `/control/*` and the telemetry socket |
| `app/core/engine_params.py` | Every tunable constant, including all Phase 3 additions |
| `app/db/models.py` | SQLAlchemy tables: missions, telemetry_frames (JSON), fault_events |
| `app/db/session.py` | SQLite engine, WAL mode, `init_db()` on startup |
| `app/db/repository.py` | The only module that touches the database; batches frame writes |
| `app/physics/electrical_model.py` | Alternator output vs RPM, battery terminal voltage under load |
| `app/physics/sensor_fault_model.py` | Sensor faults — corrupt the reading, not the engine |
| `app/ml/efficiency_analysis.py` | BSFC and its rolling trend |
| `app/ml/maintenance_advisor.py` | Rule-based, auditable maintenance recommendations; **Phase 7** — `generate_early_warning`, one concrete action per gated-in subsystem |
| `app/ml/mission_report.py` | Post-mission debrief built from stored frames |
| `app/sim/replay_engine.py` | Streams stored missions over the live contract |
| `app/ingestion/adapter_interface.py` | `RawEngineData` + `EngineDataAdapter`; simulated impl, CAN stub |
| `app/api/twin_diagnostics.py` | Residuals, twin values, classifier verdict, ground truth |
| `app/physics/steady_state.py` | **Phase 4** — relaxes a plant to a commanded operating point at two rates |
| `app/sim/scenario_engine.py` | **Phase 4** — headless what-if runs, limit excursions, PASS/CAUTION/FAIL |
| `app/ml/operating_point_optimizer.py` | **Phase 4** — bounded search over throttle/AFR/timing under hard limits |
| `app/core/mission_presets.py` | **Phase 4** — three named presets, resolved by the optimizer and cached |
| `app/api/scenario.py` | **Phase 4** — `/simulate/scenario`, its validity envelope, and run history |
| `app/api/optimizer.py` | **Phase 4** — `/optimize/operating-point` and the objective list |
| `app/core/compute_budget.py` | **Phase 4** — one compute slot + GIL yields so heavy work does not starve the live broadcast |
| `app/core/fleet_registry.py` | **Phase 6** — `FleetEntry`/`FleetRegistry`, starting-wear seeding, `lifecycle_engine_id` |
| `app/core/uav_ids.py` | **Phase 6** — `UAV_IDS`, `DEFAULT_UAV_ID`; split out to avoid a circular import |
| `app/api/fleet.py` | **Phase 6** — `/fleet/overview`, `/fleet/rankings`, and the `/ws/fleet-overview` broadcast loop |

## Frontend component responsibilities

Phase 2 modified no frontend file at all. Phase 3 **extended** the schema with optional
fields only and added new components; the existing panels were not restyled, and they
render unchanged against the extended contract because every new field is optional.

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
| `components/dashboard/ControlDeck` | Throttle, time-scale, phase jump, fault injection, sensor faults, scenarios, record/replay |
| `components/dashboard/BatteryAlternatorTile` | Bus voltage vs alternator output (Phase 3) |
| `components/dashboard/EfficiencyTrendChart` | BSFC and COV(IMEP) over the mission (Phase 3) |
| `components/dashboard/MaintenanceAdvisoryPanel` | Actionable advisories, severity-ordered (Phase 3) |
| `components/dashboard/MissionReplayControls` | Record/stop, mission picker, replay speed (Phase 3) |
| `components/dashboard/MissionReportView` | Post-mission debrief modal (Phase 3) |
| `lib/types.ts` | `TelemetryFrame` contract (mirrored by the backend) |
| `lib/websocket.ts` | Reconnecting WebSocket client |
| `lib/store.ts` | Zustand store: 600-frame rolling buffer + control actions |
| `app/test-bench/page.tsx` | **Phase 4** — the Test Bench route; opens no WebSocket |
| `components/nav/ModeNav` | **Phase 4** — Live Dashboard ↔ Test Bench switch |
| `components/testbench/ScenarioBuilder` | **Phase 4** — conditions, throttle profile, wear, scheduled faults |
| `components/testbench/ScenarioResultsView` | **Phase 4** — static charts and the PASS/CAUTION/FAIL banner |
| `components/testbench/OptimizerPanel` | **Phase 4** — objective picker and the before/after comparison |
| `components/testbench/MissionPresetCards` | **Phase 4** — three presets with "Apply to Live Engine" |
| `components/testbench/ScenarioHistoryList` | **Phase 4** — past `scenario_runs`, reloadable into the builder |
| `lib/testbench/*` | **Phase 4** — separate types, REST client and store from the live path |
| `app/fleet/page.tsx` | **Phase 6** — the fleet route: summary header, roster grid, per-UAV trend charts |
| `components/fleet/FleetSummaryHeader` | **Phase 6** — fleet-wide GO/CAUTION/NO-GO counts and total active faults |
| `components/fleet/FleetRosterGrid` | **Phase 6** — one card per UAV, ranked; click selects that UAV and navigates |
| `components/fleet/FleetTrendMiniCharts` | **Phase 6** — per-UAV health trend, reusing the Lifecycle view's series |
| `lib/fleet/store.ts` | **Phase 6** — `selectedUavId`, the single state every other store scopes requests to |
| `components/dashboard/EarlyWarningBanner` | **Phase 7** — the earlier, softer tier ahead of `FaultAlertFeed`; renders nothing when there is no active early warning |

## Still ahead

The ML layer is scaffolded but only the classifier is a trained model. Future work would add:
a learned anomaly detector (autoencoder reconstruction error over residuals) replacing the
hand-weighted subsystem mapping in `anomaly_detector.py`; a sequence model (LSTM/TCN) for
RUL in place of trend extrapolation; and a calibrated reliability model trained on
simulated mission outcomes rather than a fixed Weibull shape.

Phase 4 adds two of its own. The stress-rate index behind the life objective is a
physically-motivated ordering normalised to nominal cruise, not a calibrated damage model —
turning its hours figure into a real overhaul interval needs run-to-failure data. And the
mean-value combustion model has no oxygen limit on the rich side, which is why the mixture
trim is confined to the AFR band the schedule already covers; modelling best-power mixture
properly would change the live simulation too, and is a Phase 2 change rather than a Phase
4 one.

Phase 6 has three of its own. The Phase 1 mock generator (`USE_MOCK=true`) stays
single-engine — it is a demo-safety fallback for when the physics model itself cannot run,
not something worth generalising a second time. `CORS_ORIGINS` still defaults to
`http://localhost:3000` only; this project's frontend dev server runs on 3005 (3000 is
occupied by an unrelated local project on the development machine this was built on), so a
real deployment — or a differently-numbered local setup — needs `CORS_ORIGINS` set
explicitly to whatever origin the frontend is actually served from, same as it always did.
And three fixed UAV ids (`app/core/uav_ids.py::UAV_IDS`) is a roster, not a fleet-management
system — adding or retiring an airframe means editing that constant and restarting, not an
admin action; see docs/deployment-roadmap.md for what a real squadron deployment would need
instead.
