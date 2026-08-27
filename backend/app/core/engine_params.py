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
    # stoichiometric in cruise. Leaning therefore moves EGT *up* toward its peak.
    afr_target_wot: float = 12.8
    afr_target_cruise: float = 14.7
    afr_peak_egt: float = 16.0               # AFR at which EGT peaks
    afr_egt_width: float = 9.0               # width of the EGT-vs-AFR curve
    afr_egt_reference: float = 13.5          # AFR the EGT curve is normalised at
    afr_max: float = 22.0                    # lean misfire limit
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
    f_spark_eta_comb_loss: float = 0.30
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


PARAMS = EngineParams()
"""Default engine. Models accept an EngineParams so alternates can be swapped in."""
