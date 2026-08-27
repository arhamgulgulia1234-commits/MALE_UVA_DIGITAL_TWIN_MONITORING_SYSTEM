# Demo Script (~3 minutes)

Live-demo walkthrough for SIH26054 judges. Run backend + frontend side by side (see the
README "Running locally"). Presenter drives the **Control Deck** while narrating.

Phase 2 note: every number on screen is now derived from a physics simulation, and the
health/diagnosis figures come from comparing that engine against a digital twin. Say so —
it is the single strongest point of the demo.

## 0:00 – 0:30 — Healthy baseline

- Let it sit in **cruise**. Point out the **GO** banner, **HealthScoreGauge** at 100, the
  **RULPanel** showing a dash, and the **EngineCutaway3D** cylinders firing in sequence.
- Narrate: *"This is a mean-value thermodynamic model of a turbocharged 2-litre
  four-cylinder aero engine running at 10 Hz. Nothing here is scripted — RPM comes from
  integrating crankshaft torque against propeller load, EGT from the combustion heat that
  doesn't become work, oil pressure from a viscosity curve. And alongside it we're flying a
  second, healthy copy of the same engine: the digital twin."*
- Key point: **RUL is null and health is exactly 100 while healthy.** The system is not
  guessing; there is no degradation trend to extrapolate.

## 0:30 – 1:00 — Show that it is really physics

The most convincing 30 seconds in the demo — pick one:

- **Throttle sweep.** Drag the throttle slider down and back up. RPM, MAP, boost, fuel
  flow and EGT all move together, with the turbo visibly lagging boost behind throttle
  (0.95 s spool constant). Health stays at 100 throughout — *"the twin throttled back too,
  so the residual stayed at zero. A raw threshold system would have alarmed here."*
- **Phase jump to climb.** Altitude climbs, air thins, and power lapses with density.

## 1:00 – 1:45 — Inject a fault live

Open the fault grid and trigger **`bearing_wear`** (severity ~0.85, the default 18 s ramp).

Narrate while it ramps:
- *"I've increased the bearing running clearance in the simulation. I haven't touched a
  single output signal — watch what the physics does with it."*
- **Oil pressure** falls (leakage term in the lubrication model).
- **Vibration RMS** climbs broadband across all cylinders in the VibrationSpectrum panel.
- **Lubrication** subsystem score diverges from the others in the HealthScoreGauge — the
  residual layer localised it.
- **FaultAlertFeed** logs it with a live severity percentage.

## 1:45 – 2:15 — Prognostics react

- **RULPanel** switches on and counts down as the health-indicator trend is extrapolated
  toward the HI = 40 failure threshold.
- **GO → CAUTION → NO-GO** on the banner, driven by
  `R(t) = exp(−(t_remaining / RUL)^2.5)` over the *remaining planned mission*.
- Narrate: *"This is the recommendation an operator acts on: not 'oil pressure is low',
  but 'you have roughly N minutes of useful life and this mission needs more than that'."*

If the classifier is trained, open <http://localhost:8000/twin/diagnosis> on a second
screen to show `predicted_fault: bearing_wear` next to the ground truth.

## 2:15 – 2:45 — Clear the fault

- Tap **clear** on the active fault. Health climbs back, the RUL panel clears, the banner
  returns to GO.
- Optionally bump **time acceleration** to 20× — *"and the diagnosis keeps up, because the
  detector's time constants are defined in simulated time, not in frames."*

## 2:45 – 3:00 — Close

*"Phase 1 was the dashboard. Phase 2 — what you're seeing — is the physics and the digital
twin underneath it: nine fault modes injected as parameter perturbations, diagnosed from
residuals against a healthy reference engine. Phase 3 replaces our hand-weighted anomaly
mapping and trend-fit RUL with trained sequence models on the same interface."*

## Tips and contingencies

- **Most visual faults:** `misfire` (one cylinder's EGT collapses, huge spread, crest
  factor spike) and `bearing_wear` (oil pressure + broadband vibration). `cooling_degradation`
  is the clearest single-signal story — CHT climbs steeply and nothing else does.
- **Slow-burn option:** `air_filter_clog` or `piston_ring_wear` if you want to talk over a
  gradual CAUTION rather than a dramatic NO-GO.
- **Rehearse the ramp.** 18 s reads well live; shorten it if you talk fast.
- **If the physics misbehaves on stage:** restart the backend with `USE_MOCK=true` to fall
  back to Phase 1's scripted generator. Identical contract, dashboard unaffected.
- **If asked about accuracy:** be straight about it — this is a mean-value model tuned for
  correct *relative* behaviour, not a calibrated engine. Peak power is ~64 hp rather than
  the 150 hp class in the problem statement, because 150 hp from 2.0 L at 2700 RPM needs a
  compressor pressure ratio around 3.0; `docs/physics-model.md` documents exactly which
  parameters to change and what it costs.
