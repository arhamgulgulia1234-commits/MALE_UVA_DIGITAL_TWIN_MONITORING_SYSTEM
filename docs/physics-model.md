# Physics Model Reference (Phase 2 — implemented)

This document describes the physics that `backend/app/physics/*` actually implements,
with the default parameter values from `backend/app/core/engine_params.py`. It replaces
the Phase 1 placeholder, which described a scripted mock rather than a simulation.

Everything below is *tunable* — every coefficient lives in `engine_params.py`, nothing is
hardcoded in the model files. The defaults were chosen so that relative behaviour is
correct and believable (power rises with throttle, lapses with altitude, EGT rises when
lean, oil pressure falls as bearings wear), not so that absolute numbers match a specific
engine on a specific dynamometer.

## Reference engine

| Property | Default | Notes |
|---|---|---|
| Configuration | 4-cylinder, 4-stroke, turbocharged | `n_cylinders`, `revs_per_cycle = 2` |
| Displacement | 2.0 L (`2.0e-3` m³) | `displacement_m3` |
| Redline / idle | 2700 / 700 RPM | `rpm_redline`, `rpm_idle` |
| Load | Fixed-pitch propeller, direct drive | `prop_load_k = 2.80e-3` N·m·s² |
| Stoichiometric AFR | 14.7 | `afr_stoich` |
| Fuel LHV | 44 MJ/kg | `fuel_lhv_j_per_kg` |
| Peak boost | 1.95 pressure ratio, 156 kPa wastegate cap | `boost_pressure_ratio_max` |

**Achieved performance at these defaults:** roughly **64 hp (48 kW) at 2600 RPM** in
climb, falling to ~24 hp in descent. This is short of the "~150 hp class" the problem
statement suggests, and deliberately so: 150 hp from 2.0 L at only 2700 RPM implies a
brake mean effective pressure near 2.5 MPa, which is turbo-diesel territory rather than a
spark-ignition aero engine, and would need a compressor pressure ratio around 3.0. If you
want the higher figure, raise `boost_pressure_ratio_max` and `turbo_wastegate_limit_kpa`
together and re-tune `prop_load_k` so the engine still balances at redline — but note that
boost above ~160 kPa absolute pushes the reported `boost_pressure_kpa` outside the healthy
band the frontend colours against.

## Mission profile

Phases command only **throttle, target altitude and airspeed** (`app/sim/mission_profiles.py`).
Every engine signal emerges from the physics in response — climb is hot because the
throttle is open, not because a table says so.

| Phase | Duration | Throttle | Target alt | Climb rate | Airspeed |
|---|---|---|---|---|---|
| climb | 240 s | 0.95 | 2400 m | +6.5 m/s | 38 m/s |
| cruise | 240 s | 0.78 | 2400 m | 0 | 48 m/s |
| loiter | 300 s | 0.66 | 2200 m | −1.0 m/s | 33 m/s |
| descent | 150 s | 0.56 | 900 m | −7.0 m/s | 42 m/s |

Resulting steady-state signals (healthy):

| Phase | RPM | MAP kPa | Boost kPa | EGT °C | CHT °C | Oil °C | Oil kPa | Fuel L/h | hp |
|---|---|---|---|---|---|---|---|---|---|
| climb | 2593 | 142 | 149 | 741 | 165 | 95 | 385 | 17 | 64 |
| cruise | 2366 | 98 | 122 | 728 | 165 | 99 | 334 | 12 | 41 |
| loiter | 2024 | 76 | 109 | 717 | 153 | 94 | 303 | 8 | 29 |
| descent | 2000 | 67 | 111 | 718 | 146 | 90 | 317 | 7 | 24 |

## 1. Environment — `environment.py`

International Standard Atmosphere. Troposphere (h ≤ 11 000 m):

```
T(h)   = T0 - L*h                       T0 = 288.15 K,  L = 0.0065 K/m
p(h)   = p0 * (T(h)/T0)^(g/(L*R))       p0 = 101325 Pa, R = 287.05 J/(kg·K)
rho    = p / (R*T)
sigma  = rho(h) / rho(0)                <- density_ratio(), used everywhere
```

Above the tropopause temperature is constant at 216.65 K and pressure decays
exponentially. `density_ratio()` is the single altitude-lapse term consumed by the
breathing, turbo and cooling models.

## 2. Engine core — `engine_model.py`

Mean-value model. State: crankshaft speed and manifold absolute pressure.

**Volumetric efficiency** — a smooth peak at mid-RPM, corrected for manifold pressure,
air density and intake restriction:

```
eta_vol = eta_vol_peak * exp(-((RPM - 2000)/1500)^2)
        * (1 + 0.12*(MAP/100 - 1))
        * (1 + 0.25*(sigma - 1))
        * (1 - 0.42 * air_filter_clog)
```

**Air and fuel flow** (N = rev/s, `revs_per_cycle = 2` for a 4-stroke):

```
m_air_dot  = eta_vol * (MAP * V_d * N) / (revs_per_cycle * R * T_intake)
m_fuel_dot = m_air_dot / AFR_target        (computed per cylinder)
```

`AFR_target` interpolates from 14.7 in cruise to 12.8 at wide-open throttle. Running rich
of peak at high power is standard aero practice for charge cooling, and it is what makes
*leaning* (a clogged injector, a late-burning spark) drive EGT **up** toward its peak.

**Combustion efficiency**, per cylinder:

```
eta_comb = 0.95 * clamp(1 - 0.030*|AFR - 14.7|, 0.55, 1)
         * (1 - 0.30 * spark_degradation)        [affected cylinder]
         * 0.06 on cycles dropped by misfire     [affected cylinder]
```

**Indicated work and torque** (4-stroke, cycles/s = N/2):

```
P_indicated = sum_cyl(m_fuel_dot * LHV * eta_comb) * eta_thermal
eta_thermal = 0.46 * (1 - 0.26 * spark_degradation)
IMEP        = P_indicated / (V_d * cycles_per_second)
T_indicated = IMEP * V_d / (4*pi)
T_friction  = 12.0 + 0.055*omega + 4.2e-4*omega^2                   [N·m]
T_brake     = T_indicated - T_friction - 46.0 * piston_ring_wear
```

**Speed dynamics** against a fixed-pitch propeller. The windmilling term is what stops
the engine stalling dead when the throttle is closed in a descent — in forward flight the
airstream drives the prop:

```
T_load        = prop_load_k * omega^2 - prop_windmill_k * airspeed^2
J * domega/dt = T_brake - T_load                    J = 0.62 kg·m²
```

An ECU **idle governor** opens an idle-air bypass whenever RPM falls below the idle
target, bounded by `idle_governor_max_throttle = 0.42`.

**Exhaust gas temperature.** Combustion heat that does not become indicated work is split
between the exhaust and the cylinder head. The split itself depends on mixture — a rich
charge carries more heat out of the exhaust and less into the head, which is precisely why
rich operation protects the engine at high power:

```
head_split = 0.50 * (AFR / 14.7)^3.6                  clamped to [0.15, 0.85]
Q_residual = P_released - P_indicated
dT         = (Q_residual_cyl * exhaust_split) / (m_charge_cyl * cp_exhaust)
EGT        = T_intake + dT * egt_afr_shape(AFR) + trim_cyl + 34*piston_ring_wear
```

`egt_afr_shape` is a Gaussian peaking at AFR 16.0 (width 9.0), normalised to 1.0 at
AFR 13.5, so leaning from 12.8 toward 16 raises EGT by roughly 10 % — realistic, rather
than the factor-of-two a narrow curve would give. Per-cylinder `trim` is *deterministic*
(±9 °C by cylinder index), so the digital twin reproduces it exactly and the healthy
EGT-spread residual is genuinely zero. A 1.6 s thermocouple lag is applied so a misfiring
cylinder reads as a steady drop rather than noise.

## 3. Thermal — `thermal_model.py`

Two first-order ODEs, integrated on the same sub-step as the engine.

```
m_head*cp_head * dT_cht/dt = Q_to_head - h_eff*(T_cht - T_ambient)

h_eff = 150 * (airspeed/45)^0.55 * sigma^0.5 * flap_factor
        * (1 - 0.62 * cooling_degradation)
flap_factor = 0.35 + 0.65 * throttle
```

The `flap_factor` models cooling flaps scheduled off power demand: they close as the
engine is throttled back, which is what stops CHT collapsing during a low-power descent.

```
m_oil*cp_oil * dT_oil/dt = Q_friction + Q_blowby + Q_from_head - Q_cooler

Q_friction  = friction_power * 0.45
Q_blowby    = 4200 * piston_ring_wear                    [W]
Q_from_head = 21 * (T_cht - T_oil)
Q_cooler    = 75 * (airspeed/45)^0.55 * (T_oil - T_ambient)
```

Head thermal mass 26 kJ/K, oil 42 kJ/K — CHT settles in a few minutes, oil more slowly,
which is what the warm-up transient in the validation plots shows.

## 4. Lubrication — `lubrication_model.py`

```
mu(T)  = A * exp(B / (T + C))          A = 1.054e-3, B = 450, C = 95   [Pa·s, T in °C]
mu    *= (1 - 0.30 * piston_ring_wear)       blow-by dilutes and thins the oil
P_oil  = k1 * RPM * mu(T_oil) - k2 * wear_factor
         k1 = 13.2 * (1 - 0.68 * oil_pump_degradation)
         k2 = 260 kPa,  wear_factor = bearing_wear
```

clamped to [15, 520] kPa (relief valve) with a 0.25 s hydraulic lag.

The two lubrication faults are deliberately separable from oil pressure alone: pump
degradation scales the *slope* with RPM, while bearing wear is an approximately constant
offset. That difference is what lets the classifier tell them apart.

## 5. Vibration — `vibration_model.py`

Synthesised at its own 1 kHz sample rate, independent of the 20 ms ODE sub-step, because
the firing harmonic reaches ~90 Hz at redline and would alias at 50 Hz.

```
f_fire = (RPM/60) * n_cylinders / revs_per_cycle
x(t)   = A_fire*sin(2*pi*f_fire*t + phase_cyl) + 0.35*A_fire*sin(4*pi*f_fire*t + phase)
       + N(0, noise_floor) + baseline
```

Fault content:
* `bearing_wear` — added broadband noise, `0.62 * severity` (raises the floor everywhere)
* `misfire` — a decaying impulse once per cycle on the affected cylinder, amplitude `0.95 * severity`
* `turbo_wear` — a tone at `6 * f_fire`, amplitude `0.18 * severity`

`compute_features()` returns RMS, crest factor, dominant frequency and per-band RMS from
an amplitude-normalised `rfft` (normalised so band energies stay comparable to the
signal's own RMS regardless of buffer length). Crest factor is the misfire tell — impulsive
content raises peak-to-RMS — while bearing wear raises RMS with a *flat* crest factor.

## 6. Turbocharger — `turbo_model.py`

```
PR_target   = 1 + (PR_max_eff - 1) * throttle * rpm_norm
PR_max_eff  = 1 + (1.95 - 1) * (1 - 0.55 * turbo_wear)
P_target    = min(p_inlet * PR_target, 156 kPa)
dP/dt       = (P_target - P_boost) / tau_eff
tau_eff     = 0.95 * (1 + 2.6 * turbo_wear)
T_out       = T_amb * (1 + (PR^((g-1)/g) - 1) / eta_compressor)     eta_c = 0.72
```

`p_inlet` is ambient **minus the air-filter restriction**. The filter sits upstream of the
compressor, so a clog lowers what the turbo has to work with and boost droops — modelling
it downstream (as Phase 1 effectively did) makes an air-filter clog invisible in the boost
signal, which is wrong.

## 7. Fault models — `fault_models.py`

`FaultState` holds a 0–1 severity per fault type plus which cylinder the localised faults
target. `inject(type, target_severity, ramp_seconds)` and `clear(type, ramp_seconds)`
linearly ramp toward the target; `step(dt)` advances every in-flight ramp.

Crucially these severities perturb **model parameters**, not output signals. Nothing in
Phase 2 writes "EGT = 780" — the visible signatures emerge from the equations above.

### Fault table

| type | Parameter perturbed | Emergent signature | Subsystem |
|---|---|---|---|
| `misfire` | `eta_comb → 0.06` on random cycles, one cylinder | that cylinder's EGT collapses, huge EGT spread, crest factor spikes, RPM roughness | cylinder |
| `spark_degradation` | `eta_comb −30 %`, `eta_thermal −26 %`, one cylinder | that cylinder's EGT **rises** (late burn), EGT spread, torque loss | cylinder |
| `piston_ring_wear` | `T_brake −46 N·m`, oil heat +4.2 kW, oil viscosity −30 % | oil pressure falls, oil temp rises, mild EGT rise, power loss | lubrication + cylinder |
| `bearing_wear` | lubrication `wear_factor`, broadband vibration | oil pressure falls hard, vibration RMS rises broadband | lubrication |
| `oil_pump_degradation` | pump gain `k1 −68 %` | oil pressure falls proportionally to RPM, oil temp rises | lubrication |
| `cooling_degradation` | `h_eff −62 %` | CHT climbs steeply, oil temp follows | cooling |
| `fuel_injector_clog` | AFR `+5.5` on one cylinder | that cylinder runs lean, its EGT rises, mean AFR rises, EGT spread | fuel |
| `turbo_wear` | `PR_max −55 %`, `tau ×3.6` | boost and MAP droop, sluggish spool, HF vibration tone | turbo |
| `air_filter_clog` | `eta_vol −42 %`, inlet −34 kPa | boost, MAP, RPM and power all fall together | turbo |

## 8. Integration

Fixed-step Euler at **20 ms** sub-steps (`INTERNAL_DT_S`), sub-stepped to cover
`100 ms × time_scale` of simulated time per telemetry tick — so 20× acceleration runs 100
sub-steps per tick. Every time constant in the model (manifold 0.12 s, turbo 0.95 s, oil
0.25 s, EGT probe 1.6 s, thermal masses in the hundreds of seconds) is comfortably longer
than 20 ms, so explicit Euler is stable here and cheaper than RK4.

## 8b. Phase 3 additions

### Electrical — `electrical_model.py`

```
V_alt  = V_regulated * tanh((RPM - rpm_cutin) / rpm_knee)     0 below cut-in
         V_regulated = 14.2 * (1 - 0.30 * battery_alternator_degradation)
V_batt += (V_target - V_batt) * dt/tau,    tau = 2.5 s
         V_target = V_alt - I_load * R_internal        (charging)
         R_internal = 0.022 * (1 + 6.0 * battery_alternator_degradation)
```

Cut-in 600 RPM, knee 900 RPM, so output saturates by roughly 1500 RPM. The fault attacks
both halves — less regulated output *and* higher internal resistance — which is what makes
it separable from a plain low-RPM condition: at idle both look like low voltage, but only
the fault keeps voltage low once RPM recovers.

### Injection timing — `engine_model.py`

Nominal 22 deg BTDC. `injection_timing_drift` retards it by up to 11 deg at severity 1:

```
eta_comb *= clamp(1 - 0.011 * |timing_error|, 0.5, 1)      affected cylinder
EGT      += 7.5 * |timing_error|                           burns late -> hotter exhaust
```

This is deliberately distinct from `fuel_injector_clog`, which changes the fuel *quantity*
(and therefore AFR) rather than the *timing*. Both raise EGT on one cylinder; only the
clog moves AFR, which is how they are told apart.

### Combustion stability — COV(IMEP)

Each cycle's IMEP is scattered multiplicatively:

```
sigma      = 0.018 + 0.34*misfire + 0.16*spark + 0.12*timing_drift + 0.10*injector_clog
IMEP_cycle = IMEP * N(1, sigma)
COV        = 100 * std(IMEP_cycle / baseline) / mean(...)   over a 90-sample window
```

Healthy sits near 1.7%; above ~6% the engine is rough; above ~12% misfire is imminent.

Two corrections matter here, and both were found by running the transient test:

* **Detrending.** The window is divided by a fast-tracking baseline (tau 0.3 s), so a
  legitimate change in load does not register as scatter.
* **Transient gating.** COV is only defined at quasi-steady operation — engine test
  standards measure it that way. During a manoeuvre the load genuinely changes cycle to
  cycle and the statistic measures the manoeuvre, not combustion. The metric is therefore
  held whenever `|d(MAP)/dt| / MAP > 0.30 /s`. **The transient is detected from manifold
  pressure, not from IMEP** — IMEP is the noisy signal being measured, and a misfire makes
  it jump around exactly like a throttle step, which would suppress the very fault the
  metric exists to catch. Without the gate, every throttle movement raised a spurious
  "immediate maintenance" advisory (measured: 80% COV on a 20%->100% step).

### Ambient temperature — `environment.py`, `thermal_model.py`

`atmosphere(altitude, ambient_temperature_c)` keeps the ISA pressure column (weather does
not change the mass of air above you) but recomputes density at the actual temperature via
`rho = p/(R*T)`. A hot day therefore thins the air beyond the altitude effect. Cooling is
hit twice: the driving gradient `(T_cht - T_ambient)` shrinks on its own, and the cooling
air itself carries less heat per unit volume:

```
h_eff *= max(0.45, 1 - 0.0055 * max(0, T_ambient - T_ISA(altitude)))
```

Scenarios: `standard` (ISA), `hot_weather` (48 degC), `cold_soak` (-25 degC).

### Sensor faults — `sensor_fault_model.py`

Architecturally distinct from everything above. These corrupt the **reported value**, not
the engine, and are applied *after* the physics and *after* the twin has made its
prediction:

| type | Corruption | Engine truth |
|---|---|---|
| `egt_sensor_drift` | +165 degC offset on one probe at severity 1 | unaffected |
| `oil_pressure_sensor_noise` | +55 kPa sigma noise, 10% dropouts to 25% of value | unaffected |
| `rpm_sensor_stuck` | reading freezes at its last value above severity 0.5 | unaffected |

The residual against the twin still shows an anomaly — that is the point. What separates
them from real faults is correlation structure, handled in `app/ml/fault_classifier.py`.

## 8c. Validation

`python -m scripts.validate_physics --scenario throttle-transient` steps the throttle
20% -> 100% -> 20% and measures the time to 63% of each excursion. Measured:

| Signal | tau | Expected from |
|---|---|---|
| manifold pressure | 0.50 s | manifold filling, 0.12 s |
| RPM | 0.60 s | crank inertia 0.62 kg m^2 against prop load |
| boost pressure | 1.10 s | turbo spool, 0.95 s |
| EGT (mean) | 3.30 s | probe lag 1.6 s + gas dynamics |
| oil temperature | 10.70 s | 42 kJ/K |
| CHT | 13.90 s | 26 kJ/K |

The *ordering* is the result worth checking: pressures fastest, then rotational inertia,
then exhaust gas, then metal and oil. Post-transient RPM peak-to-peak is 16 RPM, so the
explicit Euler integrator is stable under a step excitation.

## 9. Known simplifications

* No knock/detonation model, no valve timing, no per-cylinder charge imbalance from
  intake runner geometry.
* Vibration is synthesised from known fault content rather than derived from crank/
  valvetrain dynamics — the spectrum is plausible, not solved.
* The propeller is a fixed-pitch `k*omega²` absorber with a lumped windmilling term; no
  advance-ratio or blade-element model.
* Combustion is a mean-value energy balance, not a crank-angle-resolved burn.
* Cycle-to-cycle IMEP scatter is injected stochastically rather than emerging from
  turbulence and residual-gas modelling, so COV(IMEP) is calibrated in magnitude but not
  derived from first principles.
* The electrical model is a lumped RC analogy; there is no state-of-charge model, no
  temperature dependence of pack resistance, and no load scheduling.
