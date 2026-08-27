"""Mean-value engine model — the thermodynamic core of the digital twin.

State: crankshaft speed (RPM) and manifold absolute pressure (MAP). Each `step()`
integrates one sub-step of the following chain:

    eta_vol   = eta_vol(RPM, MAP) * altitude and air-filter derates
    m_air_dot = eta_vol * (MAP * V_d * N) / (revs_per_cycle * R * T_intake)
    m_fuel_dot = m_air_dot / AFR_target          (per cylinder)
    eta_comb  = eta_comb(misfire, spark, AFR)    (per cylinder)
    IMEP      = eta_comb * eta_thermal * (m_fuel_dot * LHV) / (V_d * cycles_per_second)
    T_ind     = IMEP * V_d / (4*pi)              (4-stroke)
    T_fric    = c0 + c1*omega + c2*omega^2
    T_brake   = T_ind - T_fric - ring_wear_loss
    J * domega/dt = T_brake - T_load(omega)      (fixed-pitch propeller load)

Exhaust gas temperature comes from the combustion heat that does *not* become indicated
work, spread over the charge mass, shaped by an AFR curve that peaks lean of
stoichiometric. Because aero practice is to run rich of peak at high power, leaning the
mixture (a clogged injector, a weak spark burning late) moves EGT *up* toward that peak —
which is the fault signature the dashboard shows.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from app.core.engine_params import PARAMS, EngineParams
from app.physics.environment import atmosphere, density_ratio
from app.physics.fault_models import FaultState
from app.physics.turbo_model import TurboModel, TurboOutputs


@dataclass
class EngineOutputs:
    rpm: float
    manifold_pressure_kpa: float
    boost_pressure_kpa: float
    egt_c: list[float]                # per cylinder, probe-filtered
    air_mass_flow_kg_s: float
    fuel_mass_flow_kg_s: float
    fuel_flow_lph: float
    afr_mean: float
    eta_vol: float
    eta_comb_mean: float
    torque_indicated_nm: float
    torque_friction_nm: float
    torque_brake_nm: float
    power_brake_kw: float
    intake_temp_k: float
    heat_to_head_w: float             # -> thermal_model (CHT)
    friction_power_w: float           # -> thermal_model (oil)
    misfire_events: list[bool]        # per cylinder, this sub-step


class EngineModel:
    """One physical engine. Two instances exist at runtime: the 'real' engine (subject to
    an evolving FaultState) and the digital twin's healthy reference (FaultState all
    zero) — see app/twin/digital_twin.py."""

    def __init__(self, params: EngineParams = PARAMS, seed: int = 7) -> None:
        self.p = params
        self.turbo = TurboModel(params)
        self.rpm: float = params.rpm_idle
        self.map_kpa: float = atmosphere(0.0).pressure_kpa * params.throttle_idle_fraction
        self._egt_c: list[float] = [300.0] * params.n_cylinders
        self._rng = random.Random(seed)
        self._initialised = False

    # ---- initialisation ------------------------------------------------------

    def reset(self, altitude_m: float, rpm: float | None = None) -> None:
        p = self.p
        atm = atmosphere(altitude_m)
        self.rpm = rpm if rpm is not None else p.rpm_idle
        self.map_kpa = atm.pressure_kpa * 0.5
        self._egt_c = [420.0] * p.n_cylinders
        self.turbo.reset(altitude_m)
        self._initialised = True

    # ---- sub-model helpers ---------------------------------------------------

    def _eta_vol(self, rpm: float, map_kpa: float, altitude_m: float, fs: FaultState) -> float:
        p = self.p
        # Breathing curve: smooth peak at eta_vol_rpm_peak, falling off either side.
        speed_term = math.exp(
            -(((rpm - p.eta_vol_rpm_peak) / p.eta_vol_rpm_width) ** 2)
        )
        eta = p.eta_vol_peak * speed_term
        # Mild dependence on manifold pressure (better filling when boosted).
        eta *= 1.0 + p.eta_vol_map_sensitivity * (
            map_kpa / p.eta_vol_map_ref_kpa - 1.0
        )
        # Thin air degrades breathing slightly beyond the density term already in MAP.
        sigma = density_ratio(altitude_m)
        eta *= 1.0 + 0.25 * (sigma - 1.0)
        # A clogged air filter is a direct restriction on volumetric efficiency.
        eta *= 1.0 - p.f_air_filter_eta_vol_loss * fs.air_filter_clog
        return max(0.05, eta)

    def _afr_targets(self, throttle: float, fs: FaultState) -> list[float]:
        """Commanded AFR per cylinder. Rich of peak at high power, leaner in cruise."""
        p = self.p
        base = p.afr_target_cruise + (p.afr_target_wot - p.afr_target_cruise) * _clamp(
            throttle, 0.0, 1.0
        )
        afrs = [base] * p.n_cylinders
        # A clogged injector starves one cylinder -> that cylinder runs lean.
        if fs.fuel_injector_clog > 1e-4:
            idx = fs.cylinder_for("fuel_injector_clog", p.n_cylinders)
            afrs[idx] = min(
                p.afr_max, base + p.f_injector_afr_lean_shift * fs.fuel_injector_clog
            )
        return afrs

    def _egt_afr_shape(self, afr: float) -> float:
        """EGT-vs-AFR curve.

        Peaks at `afr_peak_egt` (lean of stoichiometric) and is normalised to 1.0 at
        `afr_egt_reference`, so the multiplier stays near unity through the normal
        operating range and leaning the mixture lifts EGT by a realistic ~10%, not by a
        factor of two."""
        p = self.p

        def bell(a: float) -> float:
            return math.exp(-(((a - p.afr_peak_egt) / p.afr_egt_width) ** 2))

        return bell(afr) / bell(p.afr_egt_reference)

    def _cylinder_trim_c(self, index: int) -> float:
        """Deterministic cylinder-to-cylinder EGT spread from build tolerance.

        Deterministic (not random) so the digital twin reproduces exactly the same trim —
        the healthy EGT-spread residual is then genuinely zero, and any spread that does
        appear is a real fault rather than sampling noise."""
        p = self.p
        if p.n_cylinders <= 1:
            return 0.0
        phase = 2.0 * math.pi * index / p.n_cylinders
        return p.egt_cylinder_trim_c * math.sin(phase + 0.7)

    # ---- main integration step ----------------------------------------------

    def step(
        self,
        dt: float,
        throttle: float,
        altitude_m: float,
        fault_state: FaultState,
        airspeed_ms: float = 0.0,
    ) -> EngineOutputs:
        p = self.p
        if not self._initialised:
            self.reset(altitude_m)

        throttle = _clamp(throttle, 0.0, 1.0)

        # ECU idle governor: bypass air holds the engine at its idle target when the
        # commanded throttle alone would drag it below and stall it.
        if self.rpm < p.rpm_idle:
            deficit = (p.rpm_idle - self.rpm) / max(1.0, p.rpm_idle)
            bypass = _clamp(
                deficit * p.idle_governor_gain, 0.0, p.idle_governor_max_throttle
            )
            throttle = max(throttle, bypass)

        atm = atmosphere(altitude_m)

        # ---- 1. induction path: air filter -> turbo -> throttle -> manifold ----
        # A clogged filter drops compressor *inlet* pressure, worse at high flow.
        rpm_norm = _clamp(self.rpm / max(1.0, p.rpm_redline), 0.0, 1.2)
        filter_drop_kpa = p.f_air_filter_intake_drop_kpa * fault_state.air_filter_clog * rpm_norm
        inlet_pressure_kpa = max(10.0, atm.pressure_kpa - filter_drop_kpa)

        turbo: TurboOutputs = self.turbo.step(
            dt,
            self.rpm,
            throttle,
            altitude_m,
            fault_state,
            inlet_pressure_kpa=inlet_pressure_kpa,
        )
        feed_pressure_kpa = max(p.map_min_kpa, turbo.boost_pressure_kpa)

        throttle_open = p.throttle_idle_fraction + (1.0 - p.throttle_idle_fraction) * throttle
        map_target = max(p.map_min_kpa, feed_pressure_kpa * throttle_open)
        self.map_kpa += (map_target - self.map_kpa) * (dt / max(1e-3, p.manifold_tau_s))

        intake_temp_k = turbo.compressor_outlet_temp_k + (
            p.t_intake_base_k - atmosphere(0.0).temperature_k
        )

        # ---- 2. air and fuel flow ---------------------------------------------
        eta_vol = self._eta_vol(self.rpm, self.map_kpa, altitude_m, fault_state)
        rev_per_s = self.rpm / 60.0
        cycles_per_s = rev_per_s / p.revs_per_cycle

        m_air_dot = (
            eta_vol
            * (self.map_kpa * 1000.0 * p.displacement_m3 * rev_per_s)
            / (p.revs_per_cycle * p.r_air_j_per_kg_k * intake_temp_k)
        )
        m_air_dot = max(0.0, m_air_dot)
        m_air_per_cyl = m_air_dot / p.n_cylinders

        afrs = self._afr_targets(throttle, fault_state)
        m_fuel_per_cyl = [m_air_per_cyl / max(6.0, afr) for afr in afrs]

        # ---- 3. per-cylinder combustion quality --------------------------------
        eta_comb: list[float] = []
        misfire_events: list[bool] = []
        misfire_idx = (
            fault_state.cylinder_for("misfire", p.n_cylinders)
            if fault_state.misfire > 1e-4
            else -1
        )
        spark_idx = (
            fault_state.cylinder_for("spark_degradation", p.n_cylinders)
            if fault_state.spark_degradation > 1e-4
            else -1
        )

        for i in range(p.n_cylinders):
            eta = p.eta_comb_nominal
            # Mixture that is far off stoichiometric burns less completely.
            afr_penalty = 1.0 - 0.030 * abs(afrs[i] - p.afr_stoich)
            eta *= _clamp(afr_penalty, 0.55, 1.0)
            if i == spark_idx:
                eta *= 1.0 - p.f_spark_eta_comb_loss * fault_state.spark_degradation

            misfired = False
            if i == misfire_idx:
                prob = p.f_misfire_dropout_probability * fault_state.misfire
                if self._rng.random() < prob:
                    misfired = True
                    eta *= 0.06  # essentially no combustion this cycle
            eta_comb.append(max(0.0, eta))
            misfire_events.append(misfired)

        # ---- 4. indicated work -------------------------------------------------
        # Late/weak spark converts less of the released heat into piston work.
        eta_thermal = p.eta_thermal_indicated * (
            1.0 - p.f_spark_eta_thermal_loss * fault_state.spark_degradation
        )

        fuel_power_per_cyl = [
            m_fuel_per_cyl[i] * p.fuel_lhv_j_per_kg * eta_comb[i]
            for i in range(p.n_cylinders)
        ]
        released_power_w = sum(fuel_power_per_cyl)
        indicated_power_w = released_power_w * eta_thermal

        if cycles_per_s > 1e-6:
            imep_pa = indicated_power_w / (p.displacement_m3 * cycles_per_s)
        else:
            imep_pa = 0.0
        torque_indicated = imep_pa * p.displacement_m3 / (4.0 * math.pi)

        # ---- 5. friction and load ----------------------------------------------
        omega = self.rpm * 2.0 * math.pi / 60.0
        torque_friction = (
            p.friction_c0_nm + p.friction_c1_nm_s * omega + p.friction_c2_nm_s2 * omega**2
        )
        ring_loss = p.f_ring_wear_torque_loss_nm * fault_state.piston_ring_wear
        torque_brake = torque_indicated - torque_friction - ring_loss

        # Net propeller torque demand: absorption rises with omega^2, but the airstream
        # drives the prop back, so a throttled-back engine in forward flight keeps
        # turning instead of stopping.
        torque_load = p.prop_load_k * omega**2 - p.prop_windmill_k * airspeed_ms**2

        domega_dt = (torque_brake - torque_load) / p.crank_inertia_kg_m2
        omega = max(0.0, omega + domega_dt * dt)
        self.rpm = _clamp(omega * 60.0 / (2.0 * math.pi), 0.0, p.rpm_redline * 1.12)

        power_brake_w = max(0.0, torque_brake) * omega
        friction_power_w = torque_friction * omega

        # ---- 6. heat split: work / exhaust / head ------------------------------
        # A rich charge carries more heat out of the exhaust and puts less into the head.
        # This is the physical reason aero engines run rich of peak at high power, and it
        # is what keeps climb CHT survivable in this model.
        afr_charge = sum(afrs) / len(afrs)
        head_split = p.heat_to_head_fraction * (
            afr_charge / p.afr_target_cruise
        ) ** p.heat_to_head_afr_exponent
        head_split = _clamp(head_split, 0.15, 0.85)

        residual_power_w = max(0.0, released_power_w - indicated_power_w)
        heat_to_head_w = residual_power_w * head_split

        # ---- 7. per-cylinder EGT ----------------------------------------------
        egt_target: list[float] = []
        for i in range(p.n_cylinders):
            residual_i = max(
                0.0, fuel_power_per_cyl[i] * (1.0 - eta_thermal)
            ) * p.heat_to_exhaust_fraction
            charge_flow_i = max(1e-6, m_air_per_cyl + m_fuel_per_cyl[i])
            delta_t = residual_i / (charge_flow_i * p.cp_exhaust_j_per_kg_k)
            delta_t *= self._egt_afr_shape(afrs[i])
            t_k = intake_temp_k + delta_t
            # Blow-by adds a little exhaust heat across all cylinders.
            t_k += p.f_ring_wear_egt_rise_k * fault_state.piston_ring_wear
            t_k += self._cylinder_trim_c(i)
            egt_target.append(t_k - 273.15)

        # Thermocouple inertia: a misfiring cylinder reads as a steady *drop*, not noise.
        alpha = dt / max(1e-3, p.egt_probe_tau_s)
        for i in range(p.n_cylinders):
            self._egt_c[i] += (egt_target[i] - self._egt_c[i]) * min(1.0, alpha)

        m_fuel_dot = sum(m_fuel_per_cyl)
        fuel_flow_lph = m_fuel_dot / p.fuel_density_kg_per_l * 3600.0
        afr_mean = (m_air_dot / m_fuel_dot) if m_fuel_dot > 1e-9 else p.afr_stoich

        return EngineOutputs(
            rpm=self.rpm,
            manifold_pressure_kpa=self.map_kpa,
            boost_pressure_kpa=turbo.boost_pressure_kpa,
            egt_c=list(self._egt_c),
            air_mass_flow_kg_s=m_air_dot,
            fuel_mass_flow_kg_s=m_fuel_dot,
            fuel_flow_lph=fuel_flow_lph,
            afr_mean=afr_mean,
            eta_vol=eta_vol,
            eta_comb_mean=sum(eta_comb) / len(eta_comb),
            torque_indicated_nm=torque_indicated,
            torque_friction_nm=torque_friction,
            torque_brake_nm=torque_brake,
            power_brake_kw=power_brake_w / 1000.0,
            intake_temp_k=intake_temp_k,
            heat_to_head_w=heat_to_head_w,
            friction_power_w=friction_power_w,
            misfire_events=misfire_events,
        )


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
