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
| `scripts/validate_physics.py` | Headless mission + fault, and the throttle-transient scenario; writes validation plots |
| `app/core/security.py` | Bearer-token guard for `/control/*` and the telemetry socket |
| `app/core/engine_params.py` | Every tunable constant, including all Phase 3 additions |
| `app/db/models.py` | SQLAlchemy tables: missions, telemetry_frames (JSON), fault_events |
| `app/db/session.py` | SQLite engine, WAL mode, `init_db()` on startup |
| `app/db/repository.py` | The only module that touches the database; batches frame writes |
| `app/physics/electrical_model.py` | Alternator output vs RPM, battery terminal voltage under load |
| `app/physics/sensor_fault_model.py` | Sensor faults — corrupt the reading, not the engine |
| `app/ml/efficiency_analysis.py` | BSFC and its rolling trend |
| `app/ml/maintenance_advisor.py` | Rule-based, auditable maintenance recommendations |
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
