"""Tunable engine constants for the Phase 2 physics model.

Every coefficient used by app/physics/* lives here — nothing is hardcoded inline in the
model files, so the whole engine can be re-tuned from one place.

Defaults describe a small turbocharged 4-cylinder aero piston engine in the ~150 hp
class (2.0 L, 2700 RPM redline, direct-drive propeller load). They are chosen for
*correct equation structure and believable relative behavior* — power rises with
throttle, lapses with altitude, EGT rises when lean, oil pressure falls as bearings wear
— not for lab-accurate absolute numbers. Tune the values; the equations should hold.

Note on power: a 2.0 L engine making ~150 hp at only 2700 RPM implies a brake mean
effective pressure near 2.5 MPa, which is high for a spark-ignition engine (that is turbo
diesel territory). The defaults below land somewhat under that figure. Raise
`eta_thermal_indicated` or `boost_pressure_ratio_max` to push peak power up if you want
to hit 150 hp exactly.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EngineParams:
    # ---- geometry -------------------------------------------------------------
    n_cylinders: int = 4
    displacement_m3: float = 2.0e-3          # 2.0 L total swept volume
    revs_per_cycle: int = 2                  # 4-stroke: one power stroke per 2 revs
    rpm_redline: float = 2700.0
    rpm_idle: float = 700.0
    crank_inertia_kg_m2: float = 0.62        # crank + flywheel + prop, lumped

    # ---- gas properties -------------------------------------------------------
    r_air_j_per_kg_k: float = 287.05
    gamma_air: float = 1.40
    cp_exhaust_j_per_kg_k: float = 1150.0

    # ---- fuel / combustion ----------------------------------------------------
    afr_stoich: float = 14.7
    fuel_lhv_j_per_kg: float = 44.0e6
    fuel_density_kg_per_l: float = 0.72
    # Aero practice: run rich of peak at high power for charge cooling, near
    # stoichiometric in cruise. Leaning from there moves EGT *up* toward its peak at
    # `afr_peak_egt`, and back down again beyond it.
    afr_target_wot: float = 12.8
    afr_target_cruise: float = 14.7
    #: AFR at which EGT peaks. `EngineModel._egt_afr_shape` is constructed so this is
    #: the *net* peak of the modelled exhaust temperature rise, not merely the centre of
    #: an internal bell that other AFR-dependent terms then shift away from.
    #: Slightly lean of stoichiometric, matching aero-engine practice.
    afr_peak_egt: float = 15.0
    #: Width of the EGT-vs-AFR curve. Set so the rise from a rich-of-peak climb mixture
    #: (~AFR 12.9) to peak is ~8%, i.e. roughly 55 K on a 700 K exhaust rise, which is
    #: the ~100 F that published mixture sweeps show.
    afr_egt_width: float = 7.3
    afr_egt_reference: float = 13.5          # AFR the EGT curve is normalised at
    afr_max: float = 22.0                    # lean misfire limit
    afr_min_rich: float = 10.5               # rich limit; below this it will not burn
    eta_comb_nominal: float = 0.95
    eta_thermal_indicated: float = 0.46      # indicated work / fuel energy
    egt_cylinder_trim_c: float = 9.0         # deterministic cylinder-to-cylinder spread

    # ---- volumetric efficiency ------------------------------------------------
    eta_vol_peak: float = 0.92
    eta_vol_rpm_peak: float = 2000.0         # RPM at which breathing peaks
    eta_vol_rpm_width: float = 1500.0        # falloff width either side of peak
    eta_vol_map_ref_kpa: float = 100.0       # MAP at which the curve is calibrated
    eta_vol_map_sensitivity: float = 0.12    # gain of eta_vol with MAP/ref

    # ---- friction / load ------------------------------------------------------
    # T_friction(rpm) = c0 + c1*omega + c2*omega^2   [N*m]
    friction_c0_nm: float = 12.0
    friction_c1_nm_s: float = 0.055
    friction_c2_nm_s2: float = 4.2e-4
    # Fixed-pitch propeller: absorbs k_prop*omega^2, but in forward flight the airstream
    # drives it back (windmilling), which is what stops an aero engine from stalling dead
    # when the throttle is closed in a descent.
    prop_load_k: float = 2.80e-3
    prop_windmill_k: float = 0.021             # N*m per (m/s)^2 of airspeed
    # ECU idle governor: opens an idle-air bypass as RPM falls below idle target.
    idle_governor_gain: float = 3.0
    idle_governor_max_throttle: float = 0.42

    # ---- intake / manifold ----------------------------------------------------
    t_intake_base_k: float = 313.15          # pre-compressor intake air temp offset
    manifold_tau_s: float = 0.12             # manifold filling time constant
    throttle_idle_fraction: float = 0.10     # MAP fraction at closed throttle
    map_min_kpa: float = 18.0
    egt_probe_tau_s: float = 1.6             # thermocouple thermal inertia

    # ---- turbocharger ---------------------------------------------------------
    boost_pressure_ratio_max: float = 1.95   # compressor PR at full spool
    turbo_tau_s: float = 0.95                # spool time constant
    turbo_wastegate_limit_kpa: float = 156.0 # absolute manifold-feed pressure cap
    compressor_isentropic_eff: float = 0.72

    # ---- thermal: cylinder head ----------------------------------------------
    head_thermal_mass_j_per_k: float = 26_000.0
    cooling_effectiveness_w_per_k: float = 150.0   # nominal head->air conductance
    cooling_airspeed_ref_ms: float = 45.0          # airspeed the above is quoted at
    cooling_airspeed_exponent: float = 0.55        # forced-convection scaling
    heat_to_head_fraction: float = 0.50            # of non-work combustion heat
    heat_to_exhaust_fraction: float = 0.50
    # Rich mixtures dump more heat out of the exhaust and less into the head — this
    # is precisely why aero engines run rich of peak at high power.
    heat_to_head_afr_exponent: float = 3.6
    # Cooling flaps close as the engine is throttled back (scheduled off power
    # demand), so CHT does not collapse in a low-power descent.
    cooling_flap_min_fraction: float = 0.35

    # ---- thermal: oil ---------------------------------------------------------
    oil_thermal_mass_j_per_k: float = 42_000.0
    oil_friction_pickup_fraction: float = 0.45     # of friction power into the oil
    oil_cooler_w_per_k: float = 75.0
    oil_head_coupling_w_per_k: float = 21.0        # head -> oil conduction
    oil_temp_init_c: float = 88.0
    cht_init_c: float = 145.0

    # ---- lubrication: P_oil = k1*rpm*mu(T) - k2*wear ---------------------------
    oil_pressure_k1: float = 13.2           # pump gain  [kPa / (rpm * Pa*s)]
    oil_pressure_k2_kpa: float = 260.0       # leakage loss at wear_factor = 1
    oil_pressure_max_kpa: float = 520.0      # relief valve setting
    oil_pressure_min_kpa: float = 15.0
    # Vogel viscosity: mu(T) = A * exp(B / (T_c + C))   [Pa*s, T in degC]
    vogel_a_pa_s: float = 1.054e-3
    vogel_b_c: float = 450.0
    vogel_c_c: float = 95.0

    # ---- vibration ------------------------------------------------------------
    vib_sample_rate_hz: float = 1000.0
    vib_baseline_rms: float = 0.11           # g, healthy broadband floor
    vib_firing_amplitude: float = 0.055      # g, at the firing harmonic
    vib_noise_floor: float = 0.022           # g, white noise sigma
    vib_rpm_scaling: float = 0.85            # how strongly amplitude tracks RPM

    # ---- sensor noise (1-sigma) -----------------------------------------------
    # Applied to the *reported* values only, never to the integrator state, and never to
    # the digital twin (a model has no sensors). Real instrumentation is noisy; without
    # this the residuals are noise-free and every diagnosis becomes trivially separable.
    noise_rpm: float = 3.0
    noise_map_kpa: float = 0.40
    noise_boost_kpa: float = 0.45
    noise_egt_c: float = 2.6
    noise_cht_c: float = 0.75
    noise_oil_temp_c: float = 0.35
    noise_oil_pressure_kpa: float = 3.2
    noise_fuel_flow_lph: float = 0.09
    noise_vibration_rms: float = 0.004

    # ---- fault sensitivity coefficients ---------------------------------------
    # (each scales how hard a 0-1 severity pushes the affected physics)
    f_air_filter_eta_vol_loss: float = 0.42      # eta_vol multiplicative loss
    f_air_filter_intake_drop_kpa: float = 34.0   # upstream restriction at redline
    f_injector_afr_lean_shift: float = 5.5       # AFR units leaner at severity 1
    #: How much of the charge a weak spark leaves genuinely *unburned*. Deliberately
    #: small: `spark_degradation` models a plug that still lights the mixture but lights
    #: it late and slowly. Whole dropped cycles are the separate `misfire` fault.
    #:
    #: The balance between this and `f_spark_eta_thermal_loss` sets the *sign* of the EGT
    #: signature, because exhaust heat is `eta_comb * (1 - eta_thermal)`. At the previous
    #: 0.30 the unburned-fuel term won and a fouled plug read ~100 C *cooler* on its own
    #: cylinder — the opposite of the mag-check signature every pilot is taught, and the
    #: opposite of what the line below claims.
    f_spark_eta_comb_loss: float = 0.12
    f_spark_eta_thermal_loss: float = 0.26       # late burn -> less work, hotter EGT
    f_misfire_dropout_probability: float = 0.55  # cycles skipped at severity 1
    f_misfire_vib_impulse: float = 0.95          # g, impulse amplitude
    f_ring_wear_torque_loss_nm: float = 46.0     # blow-by torque loss at severity 1
    f_ring_wear_oil_heat_w: float = 4200.0       # extra heat into oil
    # Blow-by dilutes the oil with combustion products, thinning it and costing
    # gallery pressure — the lubrication half of a ring-wear signature.
    f_ring_wear_oil_visc_loss: float = 0.30      # fractional viscosity loss
    f_ring_wear_egt_rise_k: float = 34.0
    f_bearing_wear_factor: float = 1.0           # -> lubrication wear_factor
    f_bearing_vib_broadband: float = 0.62        # g added at severity 1
    f_oil_pump_k1_loss: float = 0.68             # fractional pump gain loss
    f_cooling_effectiveness_loss: float = 0.62   # fractional conductance loss
    f_turbo_tau_growth: float = 2.6              # tau multiplier at severity 1
    f_turbo_pr_loss: float = 0.55                # fractional PR_max loss
    f_turbo_vib_imbalance: float = 0.18

    # ==== Phase 3 additions ====================================================

    # ---- electrical (electrical_model.py) -------------------------------------
    battery_nominal_v: float = 12.6          # resting terminal voltage, charged pack
    alternator_regulated_v: float = 14.2     # regulator setpoint
    alternator_cutin_rpm: float = 600.0      # below this the alternator makes nothing
    alternator_knee_rpm: float = 900.0       # tanh knee -> saturated by ~1500 RPM
    battery_internal_ohm: float = 0.022
    battery_tau_s: float = 2.5               # terminal-voltage lag (capacitance analogy)
    electrical_load_a: float = 28.0          # avionics + payload draw
    f_alternator_output_loss: float = 0.30   # regulated output lost at severity 1
    f_battery_resistance_growth: float = 6.0 # internal resistance multiplier at severity 1

    # ---- injection timing (engine_model.py) -----------------------------------
    injection_timing_nominal_deg: float = 22.0   # crank degrees before TDC
    #: Combustion efficiency falls off either side of optimum timing.
    injection_timing_sensitivity: float = 0.011  # eta_comb loss per degree of error
    injection_timing_egt_per_deg: float = 7.5    # EGT rise per degree retarded
    f_injection_timing_drift_deg: float = 11.0   # degrees of drift at severity 1

    # ---- combustion stability (engine_model.py) -------------------------------
    #: Baseline cycle-to-cycle IMEP scatter of a healthy engine. Real engines sit around
    #: 1-3% COV; above ~10% the engine is audibly rough and misfire is imminent.
    imep_cov_baseline: float = 0.018
    #: Extra scatter contributed by each destabilising fault, at severity 1.
    imep_cov_misfire_gain: float = 0.34
    imep_cov_spark_gain: float = 0.16
    imep_cov_timing_gain: float = 0.12
    imep_cov_injector_gain: float = 0.10
    #: Rolling-window length (samples) for the COV statistic.
    imep_cov_window: int = 90
    #: Baseline tracker for detrending. Fast enough to follow a throttle change,
    #: slow enough to leave cycle-to-cycle scatter in the signal.
    imep_baseline_tau_s: float = 0.30
    #: Fractional rate of change of the IMEP baseline (per second) above which
    #: the engine is manoeuvring rather than holding steady, and COV is held.
    imep_transient_rate_threshold: float = 0.30

    # ---- ambient temperature (environment.py / thermal_model.py) ---------------
    #: Density and cooling both degrade when the air is hotter than ISA predicts.
    hot_weather_ambient_c: float = 48.0
    #: Cooling conductance lost per Kelvin above the ISA temperature for that altitude.
    cooling_hot_weather_loss_per_k: float = 0.0055

    # ---- sensor faults (sensor_fault_model.py) --------------------------------
    sensor_egt_drift_max_c: float = 165.0        # probe offset at severity 1
    sensor_oil_pressure_noise_kpa: float = 55.0  # excess sigma at severity 1
    sensor_oil_pressure_dropout_prob: float = 0.10
    sensor_rpm_stuck_threshold: float = 0.5      # above this the reading freezes

    # ==== Phase 4 additions ====================================================
    # Everything below is read by the Test Bench (app/sim/scenario_engine.py) and the
    # operating-point optimizer (app/ml/operating_point_optimizer.py). Nothing here is
    # touched by the Phase 1-3 live path, so adding it cannot change live behaviour.

    # ---- certified operating limits -------------------------------------------
    #: These are HARD bounds. The optimizer will never return a setpoint the engine model
    #: itself predicts would breach one, and the scenario summary reports every excursion.
    #: They deliberately match frontend/lib/types.ts HEALTHY_BANDS so the dashboard and
    #: the optimizer agree on what "out of limits" means.
    #: Each limit comes in two bands: `*_caution_*` is the edge of the normal operating
    #: envelope (and matches HEALTHY_BANDS in frontend/lib/types.ts, so a tile that has
    #: gone amber and a scenario that reports a caution excursion agree), while `*_limit_*`
    #: is the red line the optimizer treats as a hard constraint and the scenario summary
    #: treats as a FAIL.
    cht_limit_c: float = 230.0
    cht_caution_c: float = 210.0
    egt_limit_c: float = 850.0
    egt_caution_c: float = 800.0
    oil_temp_limit_c: float = 130.0
    oil_temp_caution_c: float = 115.0
    #: Oil below this never boils off the water and fuel it absorbs. There is no red line
    #: for cold oil — it is a "you are not operating this engine well" finding, not a
    #: "stop flying" one — so this is tracked as a caution band only.
    oil_temp_min_operating_c: float = 40.0
    oil_pressure_min_operating_kpa: float = 200.0
    oil_pressure_caution_kpa: float = 250.0

    # ---- externally commanded mixture / timing trims ---------------------------
    #: Phase 2 fixed AFR and injection timing to internal schedules. Phase 4 exposes an
    #: operator/optimizer *trim* on top of each, which is what the operating-point search
    #: actually varies. Bounds are the authority the ECU would enforce on a real engine.
    afr_trim_min: float = -1.5           # richer than the scheduled target
    #: Leaner. The scheduled cruise mixture is ~AFR 13.2 at 78% throttle, so +3.0 reaches
    #: ~16.2 — deliberately past `afr_peak_egt`, because lean-of-peak is a legitimate
    #: range-optimising operating point and the model must be able to represent it. EGT
    #: falls again on that side of the curve; CHT does not (see the note on
    #: `heat_to_head_afr_exponent`).
    afr_trim_max: float = 3.0
    #: Absolute clamp on the *resulting* commanded AFR, applied after the trim.
    #:
    #: This exists because of a real limitation of a mean-value model, and pretending
    #: otherwise would hand an optimizer a free lunch. Power here is fuel-limited:
    #: `eta_comb` falls off linearly either side of stoichiometric, so enriching past the
    #: schedule keeps adding fuel energy faster than it loses efficiency and the model
    #: predicts ever more power. A real engine is *air*-limited below stoichiometric —
    #: fuel with no oxygen to burn is simply pumped out of the exhaust — which is why best
    #: power sits near 12.5:1 and richer than that loses power. Modelling that properly
    #: means changing combustion for the live simulation too. Confining the mixture to the
    #: band the schedule already covers keeps the optimizer inside the region the model
    #: was calibrated for, which is the same discipline the scenario validity envelope
    #: applies to altitude and temperature.
    afr_command_min: float = 12.4        # just rich of best power
    #: ~1.5 AFR lean of `afr_peak_egt` — far enough to reach a usable lean-of-peak
    #: cruise, and short of the lean misfire limit at `afr_max`.
    afr_command_max: float = 16.5
    injection_timing_trim_min_deg: float = -8.0   # retard (later burn, hotter exhaust)
    injection_timing_trim_max_deg: float = 6.0    # advance (earlier burn, hotter head)
    #: Advancing the spark keeps more of the released heat in the cylinder and sends less
    #: out of the valve, so the head runs hotter and the exhaust cooler. This coefficient
    #: is what turns the timing trim into a genuine CHT-versus-EGT trade lever.
    injection_timing_head_split_per_deg: float = 0.0065

    # ---- thermal + mechanical stress / life model ------------------------------
    #: Life consumption is modelled as a *rate* relative to nominal cruise, normalised so
    #: the baseline cruise point reads ~1.0. Thermal terms use an Arrhenius-style doubling
    #: law (every `doubling_k` Kelvin above the reference doubles the wear rate), which is
    #: the standard first-order treatment for oxidation and creep-driven degradation.
    #: The reference point is the nominal cruise setting this airframe actually flies —
    #: 0.78 throttle at 2400 m on a standard day — so a stress rate of 1.0 means "wearing
    #: out at the rate the maintenance schedule already assumes", and the numbers below
    #: are simply that operating point's steady-state values.
    cht_stress_reference_c: float = 150.0
    cht_stress_doubling_k: float = 25.0
    oil_temp_stress_reference_c: float = 90.0
    oil_temp_stress_doubling_k: float = 15.0
    vibration_stress_reference_g: float = 0.10
    friction_stress_reference_w: float = 12_300.0
    oil_pressure_stress_reference_kpa: float = 366.0
    stress_weight_cht: float = 0.40
    stress_weight_oil_temp: float = 0.20
    stress_weight_vibration: float = 0.18
    stress_weight_friction: float = 0.10
    stress_weight_lubrication: float = 0.12
    #: Nominal time between overhauls at a stress rate of 1.0. Turns the dimensionless
    #: stress rate into an hours figure a maintainer can actually act on.
    tbo_hours_nominal: float = 1500.0

    # ---- optimizer search bounds and policy ------------------------------------
    opt_throttle_min: float = 0.25
    opt_throttle_max: float = 1.00
    #: "max_range" means *hold cruise power for less fuel*, not "fly slower". The search
    #: is therefore constrained to at least this fraction of the power the nominal
    #: setpoint makes at the same altitude and temperature.
    cruise_power_hold_fraction: float = 0.98
    #: "max_engine_life" is allowed to give up power, but not to stop being a useful
    #: cruise setting.
    life_min_power_fraction: float = 0.80
    balanced_min_power_fraction: float = 0.92
    #: How "balanced" splits its attention. All three terms are normalised against the
    #: baseline setpoint, so each reads 1.0 there and the weights mean what they say.
    balanced_weight_range: float = 0.35
    balanced_weight_power: float = 0.35
    balanced_weight_life: float = 0.30
    #: Nominal (baseline) cruise setpoint the optimizer compares everything against.
    nominal_cruise_throttle: float = 0.78
    #: A worn engine has a lower continuous power rating than a fresh one. This is the
    #: mechanism that makes "max_power" on a degraded engine recommend *less* power than
    #: nominal rather than blindly maximising output.
    health_power_derate_per_severity: float = 0.45
    #: Existing wear also pulls the thermal and lubrication limits in. A head that has
    #: already been through a life's worth of thermal cycling has less margin left in it
    #: than a fresh one, so the limit the optimizer respects moves with condition.
    health_cht_derate_k: float = 22.0
    health_egt_derate_k: float = 18.0
    health_oil_temp_derate_k: float = 8.0
    health_oil_pressure_margin_kpa: float = 25.0
    #: Severities below this are treated as a pristine engine.
    health_severity_floor: float = 0.02

    # ---- scenario validity envelope --------------------------------------------
    #: Outside these the ISA model, the breathing curve and the cooling correlation are
    #: no longer being used within the range they were built for. Requests that fall
    #: outside are rejected with an explanation rather than silently extrapolated.
    scenario_altitude_min_m: float = 0.0
    #: The modelled service ceiling of *this* powerplant, not of the ISA model. Above it
    #: the mean-value engine makes less than ~14 kW, which will not sustain a MALE UAV,
    #: and the thermal model — which has no oil thermostat and only a throttle-scheduled
    #: cooling flap — settles the oil below freezing. Both are honest consequences of the
    #: model's scope, and both are reasons to refuse the request rather than answer it.
    scenario_altitude_max_m: float = 8_000.0
    scenario_ambient_min_c: float = -55.0
    scenario_ambient_max_c: float = 60.0
    scenario_isa_deviation_min_k: float = -40.0
    scenario_isa_deviation_max_k: float = 50.0
    scenario_duration_min_minutes: float = 0.5
    scenario_duration_max_minutes: float = 240.0
    #: Overall health index the scenario summary treats as "still safe".
    scenario_health_safe_threshold: float = 70.0

    # ==== Phase 5 additions: sensor fusion (app/fusion/) ========================

    # ---- CHT dual-probe fusion (cht_fusion.py) ---------------------------------
    #: Two independently-mounted CHT probes, each with its own noise floor — a
    #: real second probe would not be an identical duplicate of the first.
    noise_cht_c_primary: float = 0.75        # matches the pre-fusion noise_cht_c
    noise_cht_c_secondary: float = 1.15
    #: Growing offset at severity 1 for a drifting CHT probe — same mechanism as
    #: `sensor_egt_drift_max_c`, scaled to CHT's narrower operating band.
    sensor_cht_drift_max_c: float = 55.0
    #: Kalman process variance (degC^2 per tick) for the CHT random walk. Small: CHT's
    #: thermal mass means it cannot move far between 100 ms-equivalent ticks, so the
    #: fused estimate should mostly trust its own running state between updates and let
    #: two independent sensors argue it into place.
    cht_fusion_process_variance_c2: float = 0.05

    # ---- RPM cross-modality fusion (rpm_fusion.py) -----------------------------
    #: Kalman process variance (rpm^2 per tick) for the fused RPM random walk. Wider than
    #: CHT's — RPM genuinely can move several rpm between ticks during a throttle
    #: transient, and the filter should not fight a real change.
    rpm_fusion_process_variance_rpm2: float = 36.0
    #: Floor on the vibration-derived RPM estimate's measurement variance. The FFT bin
    #: width alone (sample_rate / N) sets a hard floor on how precisely a dominant
    #: frequency can be read, converted to RPM — this adds picking noise on top of that
    #: so a wide, low-energy dominant bin does not look artificially exact.
    noise_rpm_vibration_derived_floor: float = 12.0
    #: Extra measurement-sigma (rpm) per rpm/s of fused-RPM rate of change. A coarse,
    #: once-per-tick FFT snapshot lags during a fast transient (a mission-phase change,
    #: a throttle step); widening its variance while the fused estimate is moving fast
    #: de-weights that lag rather than letting it fight the tachometer.
    rpm_vibration_transient_widening_per_rpm_s: float = 6.0

    # ---- Oil pressure model+sensor fusion (oil_pressure_fusion.py) -------------
    #: Kalman process variance (kPa^2 per tick) for the lubrication-equation PREDICT
    #: step. Narrow when no lubrication fault is suspected — the zero-wear-calibrated
    #: model is then a good predictor and the fused estimate should lean on it, smoothing
    #: sensor noise rather than chasing it.
    oil_pressure_model_process_variance_healthy_kpa2: float = 4.0
    #: Widened Q once a lubrication fault is suspected active — the zero-wear model is
    #: now a materially worse predictor, so the filter should trust the raw sensor over
    #: its own prediction. Two orders of magnitude wider is what actually shifts the
    #: Kalman gain from "mostly model" to "mostly sensor" (see the gain formula).
    oil_pressure_model_process_variance_faulted_kpa2: float = 450.0
    #: Severity above which bearing_wear / oil_pump_degradation counts as "a lubrication
    #: fault is active" for the purpose of widening Q. Small — the model should stop
    #: being trusted the moment real degradation starts, not once it is already severe.
    oil_pressure_fault_suspect_threshold: float = 0.05
    #: Time constant for the gain's own slow "healthy baseline" tracker — the same
    #: detrending idea `imep_baseline_tau_s` already uses elsewhere in this file, applied
    #: here so "the gain deviated from its own recent normal" is a zero-centred signal
    #: the classifier's EWMA z-scoring can consume the same way it consumes a residual.
    oil_pressure_gain_baseline_tau_s: float = 120.0


PARAMS = EngineParams()
"""Default engine. Models accept an EngineParams so alternates can be swapped in."""
