# Physics Model Reference (Phase 2 target)

This document is the reference the `backend/app/physics/*` modules will implement in
Phase 2. Phase 1's mock generator (`backend/app/sim/simulation_loop.py`) approximates the
*visible effects* described here (which signals move, in which direction, how fast) using
sine baselines + noise + ramps, without solving the underlying equations. Every fault
listed here already has a corresponding mock ramp in Phase 1 so the frontend/demo
behavior won't change when Phase 2 physics replaces the generator.

## Engine reference model

Target airframe class: MALE UAV aero piston engine — turbocharged, air/liquid-cooled,
4–6 cylinder boxer or inline, EFI, ~100–200 hp class (e.g. Rotax 914/916-like envelope).

| Subsystem | Phase-2 module | Governs |
|---|---|---|
| Thermodynamic core (Otto/Miller cycle, torque/power) | `engine_model.py` | RPM response, manifold pressure, torque, fuel flow baseline |
| Heat transfer (cylinder head, EGT, CHT) | `thermal_model.py` | `cylinders[].egt_c`, `cht_c`, cooling load |
| Lubrication (oil pressure/temp, film thickness) | `lubrication_model.py` | `oil_pressure_kpa`, `oil_temp_c`, bearing wear coupling |
| Vibration (crank/valvetrain dynamics, combustion impulse) | `vibration_model.py` | `cylinders[].vibration_rms`, spectral content |
| Turbocharging (compressor/turbine, boost control) | `turbo_model.py` | `boost_pressure_kpa`, spool lag, surge margin |
| Environment (altitude density, ISA temp/pressure lapse) | `environment.py` | Air density → boost/EGT/power correction with `altitude_m`, `airspeed_ms` |
| Fault injection | `fault_models.py` | Perturbs the above per the fault table below |

## Mission phase → nominal signal targets

| Phase | RPM | Manifold press. | EGT | CHT | Fuel flow | Altitude trend |
|---|---|---|---|---|---|---|
| climb | high (~5200–5500) | high | high, rising | rising toward limit | high | rising fast |
| cruise | mid (~4200–4600) | steady mid | steady mid | steady mid | steady mid | flat |
| loiter | low (~3000–3400) | low | low-mid | cooling slightly | low | flat / slow descent |
| descent | low-mid (~3200–3800), throttled | low | falling | falling | low | falling fast |

## Fault table

Each fault has a **type key** (used by `POST /control/fault`), a set of **affected
signals** (which telemetry fields move and in which direction), the **subsystem health
score** it degrades, and a qualitative **onset/RUL behavior**. Severity is 0–1; ramps are
linear over `ramp_seconds` toward the target severity, monotonic health score decrease.

| type | Description | Primary signals affected | Subsystem | RUL / reliability behavior |
|---|---|---|---|---|
| `misfire` | Intermittent cylinder combustion failure | Affected cylinder(s) EGT drops sharply + vibration RMS spikes (irregular); RPM roughness; fuel flow drifts up (unburnt fuel) | `cylinder` | Fast RUL decay at high severity; NO-GO above ~0.7 severity |
| `spark_degradation` | Weak/fouled spark, incomplete combustion | EGT rises (slow burn) on affected cylinder; vibration RMS rises moderately; fuel flow slightly up | `cylinder` | Gradual RUL decay; CAUTION by ~0.4 |
| `piston_ring_wear` | Blow-by past worn rings | Oil pressure trends down slowly; oil temp up; EGT slightly up (blow-by heat); overall vibration up slightly | `cylinder` + `lubrication` | Slow, steady RUL decay — long-horizon fault |
| `bearing_wear` | Main/rod bearing degradation | Vibration RMS rises broadband across cylinders; oil pressure drops; oil temp rises | `lubrication` | Fast RUL decay at high severity — safety-critical, NO-GO early (~0.5) |
| `oil_pump_degradation` | Reduced lubrication flow/pressure | Oil pressure falls steadily; oil temp rises; secondary vibration rise as severity increases | `lubrication` | Fast RUL decay — NO-GO by ~0.6 |
| `cooling_degradation` | Reduced cooling airflow / coolant flow | CHT rises steadily; EGT drifts up secondarily; oil temp rises slightly | `cooling` | Moderate RUL decay; CAUTION by ~0.4, NO-GO ~0.75 |
| `fuel_injector_clog` | Partial injector blockage on a cylinder | Affected cylinder EGT rises (lean); fuel flow total drops slightly; vibration rises mildly (rough running) | `fuel` | Moderate RUL decay |
| `turbo_wear` | Compressor/turbine wheel wear, seal leakage | Boost pressure fails to reach target (droops under load); manifold pressure falls; EGT rises (compensating mixture); vibration rise at high severity (imbalance) | `turbo` | Moderate RUL decay; CAUTION at ~0.5 |
| `air_filter_clog` | Progressive intake restriction | Manifold pressure falls; boost pressure falls; EGT rises (lean mixture); fuel flow falls slightly | `turbo` + `fuel` | Slow RUL decay — long-horizon, low urgency until severe |

## Health score composition (mock + future ML)

- `subsystem_scores.*` start at 95–100 (small random jitter) and fall as the relevant
  fault(s) ramp in, floor determined by severity (`score ≈ 100 − severity × 80`, clamped).
- `overall_score` is a weighted minimum-biased blend of subsystem scores (never higher
  than the worst subsystem, pulled down further by multiple concurrent faults) —
  Phase 3 will replace this with a learned/composite health index.
- `rul_minutes` becomes non-null once any subsystem score drops below ~85, then decays
  based on the *fastest-decaying* active fault's severity slope. `null` while healthy.
- `mission_reliability.score` (0–1) is a smooth function of `overall_score` and the
  worst active-fault severity; recommendation thresholds: `score > 0.75` → GO,
  `0.4–0.75` → CAUTION, `< 0.4` → NO-GO.

## Phase-2 equations (to implement)

`engine_model.py`, `thermal_model.py`, `lubrication_model.py`, `vibration_model.py`,
`turbo_model.py`, and `environment.py` will each replace their corresponding mock-signal
generator with first-principles or semi-empirical equations (e.g. Otto-cycle indicated
work, Woschni heat-transfer correlation for EGT/CHT, Reynolds-lubrication film thickness
for oil pressure, ISA atmosphere model for altitude-density correction, turbocharger
compressor/turbine maps for boost). `fault_models.py` will perturb model *parameters*
(e.g. reduced volumetric efficiency, increased bearing clearance) rather than perturbing
output signals directly, so faults propagate through the physics instead of being scripted
— this is the key Phase 1 → Phase 2 architectural change. The mock generator and the
physics engine are designed to expose the same `simulation_loop` interface so
`app/twin/digital_twin.py` can swap one for the other without touching the API layer.
