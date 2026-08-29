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
work, spread over the charge mass, shaped by an AFR curve that peaks slightly lean of
stoichiometric at `afr_peak_egt`. Because aero practice is to run rich of peak at high
power, leaning the mixture (a clogged injector, a weak spark burning late) moves EGT *up*
toward that peak — which is the fault signature the dashboard shows.

Past the peak it comes back down: a cylinder leaned far enough — a badly clogged
injector at high severity drives one about 3 AFR lean of peak — reads *cooler* than its
neighbours, not hotter. Both halves of that curve are real, and the per-cylinder EGT
spread is what identifies the fault in either direction.
"""
from __future__ import annotations

import math
import random
from collections import deque
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
    # --- Phase 3 ---------------------------------------------------------
    injection_timing_deg: float
    combustion_instability_pct: float


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
        # Phase 4: externally commanded trims. These are *commands*, not integrator
        # state, so `reset()` deliberately leaves them alone — exactly like the throttle,
        # which also survives a reset. app/ml/operating_point_optimizer.py searches over
        # them and app/sim/simulation_loop.py exposes them through the manual-override
        # mechanism.
        self.afr_trim: float = 0.0                     # AFR units; positive = leaner
        self.injection_timing_trim_deg: float = 0.0    # crank degrees; positive = advance
        # Phase 3 state
        self.injection_timing_deg: float = params.injection_timing_nominal_deg
        self.combustion_instability_pct: float = 0.0
        self._imep_history: deque[float] = deque(maxlen=params.imep_cov_window)
        self._imep_baseline: float | None = None
        self._transient_active: bool = False
        self._prev_map_kpa: float = self.map_kpa

    def set_trims(
        self,
        afr_trim: float | None = None,
        injection_timing_trim_deg: float | None = None,
    ) -> None:
        """Command a mixture and/or injection-timing offset from the internal schedule.

        `None` leaves that trim where it is. Both are clamped to the ECU's authority
        inside `step()`, so nothing outside this class can command past the lean misfire
        limit or an unbounded timing advance."""
        if afr_trim is not None:
            self.afr_trim = float(afr_trim)
        if injection_timing_trim_deg is not None:
            self.injection_timing_trim_deg = float(injection_timing_trim_deg)

    def trims(self) -> tuple[float, float]:
        """(afr_trim, injection_timing_trim_deg) as currently commanded."""
        return self.afr_trim, self.injection_timing_trim_deg

    def _compute_imep_cov(self) -> float:
        """Coefficient of variation of IMEP over the rolling window, as a percentage."""
        n = len(self._imep_history)
        if n < 12:
            return 0.0
        mean = sum(self._imep_history) / n
        if mean <= 1e-6:
            return 0.0
        variance = sum((x - mean) ** 2 for x in self._imep_history) / n
        return 100.0 * (variance**0.5) / mean

    # ---- initialisation ------------------------------------------------------

    def reset(self, altitude_m: float, rpm: float | None = None) -> None:
        p = self.p
        atm = atmosphere(altitude_m)
        self.rpm = rpm if rpm is not None else p.rpm_idle
        self.map_kpa = atm.pressure_kpa * 0.5
        self._egt_c = [420.0] * p.n_cylinders
        self.injection_timing_deg = p.injection_timing_nominal_deg
        self.combustion_instability_pct = 0.0
        self._imep_history.clear()
        self._imep_baseline = None
        self._transient_active = False
        self._prev_map_kpa = self.map_kpa
        self.turbo.reset(altitude_m)
        self._initialised = True

    # ---- sub-model helpers ---------------------------------------------------

    def _eta_vol(self, rpm: float, map_kpa: float, altitude_m: float, fs: FaultState,
                 ambient_temperature_c: float | None = None) -> float:
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
        sigma = density_ratio(altitude_m, ambient_temperature_c)
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
        # Phase 4: commanded mixture trim on top of the schedule. Leaning saves fuel and
        # moves EGT toward its peak; enriching costs fuel but cools the head, which is
        # precisely the lever an operating-point optimizer needs when CHT is the binding
        # constraint. Clamped to the ECU's authority, so a trim can never command past
        # the lean misfire limit.
        base = _clamp(
            base + _clamp(self.afr_trim, p.afr_trim_min, p.afr_trim_max),
            p.afr_command_min,
            p.afr_command_max,
        )
        afrs = [base] * p.n_cylinders
        # A clogged injector starves one cylinder -> that cylinder runs lean.
        if fs.fuel_injector_clog > 1e-4:
            idx = fs.cylinder_for("fuel_injector_clog", p.n_cylinders)
            afrs[idx] = min(
                p.afr_max, base + p.f_injector_afr_lean_shift * fs.fuel_injector_clog
            )
        return afrs

    def _afr_eta_comb_penalty(self, afr: float) -> float:
        """Combustion-completeness penalty for a charge away from stoichiometric.

        Shared by `step()` (where it derates indicated work) and `_egt_afr_shape` (where
        it is divided back out, so the EGT curve is not shaped by it twice)."""
        return _clamp(1.0 - 0.030 * abs(afr - self.p.afr_stoich), 0.55, 1.0)

    def _egt_afr_shape(self, afr: float) -> float:
        """Multiplier that makes the *net* EGT rise follow a bell peaking at
        `afr_peak_egt`.

        The caller computes an exhaust temperature rise proportional to
        `eta_comb * m_fuel / m_charge`, and both of those already vary strongly with AFR:
        `m_fuel/m_charge` falls monotonically as 1/(AFR+1), and the combustion penalty
        falls either side of stoichiometric. A bare bell multiplied on top of those does
        *not* peak where the bell peaks — it peaks well rich of it, because the
        monotonic 1/(AFR+1) term dominates a bell this wide.

        That mattered in practice, not just on paper. Before this was corrected the model
        peaked at AFR ~14.5 while `afr_peak_egt` claimed 16.0, so `afr_command_max` —
        documented as "just lean of the EGT peak" and used as the optimizer's lean
        authority — actually sat about two AFR units *lean* of peak, in a region where
        the model reported EGT falling as the mixture was leaned further.

        So this divides both AFR-dependent terms back out and returns the bell itself.
        The net rise is then exactly `bell(AFR)` times the combustion-quality factors
        that are *not* mixture-related (spark, injection timing, misfire), which keep
        modulating EGT independently. Normalised to 1.0 at `afr_egt_reference` so the
        absolute EGT calibration through the normal operating band is unchanged."""
        p = self.p

        def bell(a: float) -> float:
            return math.exp(-(((a - p.afr_peak_egt) / p.afr_egt_width) ** 2))

        ref = p.afr_egt_reference
        charge_term = (afr + 1.0) / (ref + 1.0)
        eta_term = self._afr_eta_comb_penalty(ref) / self._afr_eta_comb_penalty(afr)
        return charge_term * eta_term * bell(afr) / bell(ref)

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
        ambient_temperature_c: float | None = None,
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

        atm = atmosphere(altitude_m, ambient_temperature_c)

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
            ambient_temperature_c=ambient_temperature_c,
        )
        feed_pressure_kpa = max(p.map_min_kpa, turbo.boost_pressure_kpa)

        throttle_open = p.throttle_idle_fraction + (1.0 - p.throttle_idle_fraction) * throttle
        map_target = max(p.map_min_kpa, feed_pressure_kpa * throttle_open)
        self.map_kpa += (map_target - self.map_kpa) * (dt / max(1e-3, p.manifold_tau_s))

        intake_temp_k = turbo.compressor_outlet_temp_k + (
            p.t_intake_base_k - atmosphere(0.0).temperature_k
        )

        # ---- 2. air and fuel flow ---------------------------------------------
        eta_vol = self._eta_vol(
            self.rpm, self.map_kpa, altitude_m, fault_state, ambient_temperature_c
        )
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

        # Phase 3: injection timing. Drift away from the nominal crank angle burns less
        # completely and pushes heat into the exhaust — a *quality* fault, distinct from
        # fuel_injector_clog, which changes the fuel *quantity* reaching one cylinder.
        timing_idx = (
            fault_state.cylinder_for("injection_timing_drift", p.n_cylinders)
            if fault_state.injection_timing_drift > 1e-4
            else -1
        )
        timing_error_deg = (
            p.f_injection_timing_drift_deg * fault_state.injection_timing_drift
        )
        # Phase 4: commanded timing trim (positive = advance). The nominal angle is MBT,
        # so *any* departure from it costs combustion efficiency; what the trim buys is a
        # CHT-versus-EGT trade. Advance keeps heat in the cylinder (hotter head, cooler
        # exhaust), retard sends it out of the valve (cooler head, hotter exhaust).
        timing_trim_deg = _clamp(
            self.injection_timing_trim_deg,
            p.injection_timing_trim_min_deg,
            p.injection_timing_trim_max_deg,
        )
        self.injection_timing_deg = (
            p.injection_timing_nominal_deg + timing_trim_deg - timing_error_deg
        )

        for i in range(p.n_cylinders):
            eta = p.eta_comb_nominal
            # Mixture that is far off stoichiometric burns less completely.
            eta *= self._afr_eta_comb_penalty(afrs[i])
            if i == spark_idx:
                eta *= 1.0 - p.f_spark_eta_comb_loss * fault_state.spark_degradation
            # Departure from MBT for *this* cylinder: the commanded trim, plus the
            # fault's local drift on whichever cylinder it targets. With no trim
            # commanded this reduces exactly to the Phase 3 behaviour.
            timing_departure_deg = abs(
                timing_trim_deg - (timing_error_deg if i == timing_idx else 0.0)
            )
            if timing_departure_deg > 1e-9:
                eta *= _clamp(
                    1.0 - p.injection_timing_sensitivity * timing_departure_deg, 0.5, 1.0
                )

            misfired = False
            if i == misfire_idx:
                prob = p.f_misfire_dropout_probability * fault_state.misfire
                if self._rng.random() < prob:
                    misfired = True
                    eta *= 0.06  # essentially no combustion this cycle
            eta_comb.append(max(0.0, eta))
            misfire_events.append(misfired)

        # ---- 4. indicated work -------------------------------------------------
        # Late/weak spark converts less of the released heat into piston work, and the
        # work it fails to extract leaves through the exhaust valve instead — which is
        # why a fouled plug reads *hotter* on that cylinder's EGT probe while making
        # less power.
        #
        # This is per cylinder, and that matters. `spark_degradation` is a
        # cylinder-localised fault (see fault_models.CYLINDER_LOCALISED_FAULTS), so
        # applying its work-extraction penalty engine-wide put the extra exhaust heat on
        # all four cylinders: at severity 1.0 the three *healthy* cylinders read +134 C
        # while the genuinely faulty one read -102 C. That is the fault signature exactly
        # inverted and smeared onto the wrong cylinders, and it also over-penalised brake
        # power by roughly 4x, because every cylinder was paying one cylinder's loss.
        eta_thermal_per_cyl = [
            p.eta_thermal_indicated
            * (
                1.0 - p.f_spark_eta_thermal_loss * fault_state.spark_degradation
                if i == spark_idx
                else 1.0
            )
            for i in range(p.n_cylinders)
        ]

        fuel_power_per_cyl = [
            m_fuel_per_cyl[i] * p.fuel_lhv_j_per_kg * eta_comb[i]
            for i in range(p.n_cylinders)
        ]
        released_power_w = sum(fuel_power_per_cyl)
        indicated_power_w = sum(
            fuel_power_per_cyl[i] * eta_thermal_per_cyl[i]
            for i in range(p.n_cylinders)
        )

        if cycles_per_s > 1e-6:
            imep_pa = indicated_power_w / (p.displacement_m3 * cycles_per_s)
        else:
            imep_pa = 0.0

        # ---- Phase 3: cycle-to-cycle combustion stability ----------------------
        # Real engines never produce identical consecutive cycles — turbulence and
        # residual-gas variation scatter IMEP by a percent or two even when healthy.
        # Anything that destabilises the flame kernel widens that scatter, and it widens
        # *before* the engine starts dropping whole cycles. That is why COV(IMEP) is a
        # genuine leading indicator rather than just another way to observe a misfire:
        # it rises during the ramp, while the misfire dropout probability is still too
        # low to have produced a visible dead cycle.
        cov_sigma = p.imep_cov_baseline
        cov_sigma += p.imep_cov_misfire_gain * fault_state.misfire
        cov_sigma += p.imep_cov_spark_gain * fault_state.spark_degradation
        cov_sigma += p.imep_cov_timing_gain * fault_state.injection_timing_drift
        cov_sigma += p.imep_cov_injector_gain * fault_state.fuel_injector_clog

        cycle_scatter = self._rng.gauss(1.0, cov_sigma)
        imep_this_cycle = imep_pa * max(0.0, cycle_scatter)

        # Detrend before measuring scatter. COV is meant to capture *cycle-to-cycle*
        # variability, but a raw rolling COV also picks up any change in the mean — so
        # opening the throttle, which legitimately doubles IMEP, would read as ~80%
        # instability and trigger a spurious "immediate maintenance" advisory on every
        # throttle movement. Dividing by a fast-tracking baseline cancels the level shift
        # while leaving the per-cycle scatter intact, which is the quantity we actually
        # want.
        if self._imep_baseline is None or self._imep_baseline <= 1e-6:
            self._imep_baseline = max(imep_this_cycle, 1e-6)
        else:
            alpha = min(1.0, dt / max(1e-6, p.imep_baseline_tau_s))
            self._imep_baseline += (imep_this_cycle - self._imep_baseline) * alpha

        # Detect the manoeuvre from manifold pressure, not from IMEP. IMEP is the noisy
        # signal we are trying to measure the noise of — a misfire makes it jump around
        # violently, which would look exactly like a throttle transient and suppress the
        # very fault we need to see. MAP is smooth, moves only when the operator actually
        # commands a change, and is unaffected by combustion scatter.
        map_rate = abs(self.map_kpa - self._prev_map_kpa) / (
            max(1e-6, self._prev_map_kpa) * max(1e-6, dt)
        )
        self._prev_map_kpa = self.map_kpa
        baseline_rate = map_rate

        # COV(IMEP) is only defined at quasi-steady operation — engine test standards
        # measure it that way for a reason. During a throttle transient the load is
        # genuinely changing cycle to cycle, so the statistic measures the manoeuvre
        # rather than combustion quality, and no amount of detrending fixes that. Hold
        # the last steady reading instead of reporting a number that means something
        # else. Without this, every throttle movement raises a spurious "immediate
        # maintenance" advisory.
        self._transient_active = baseline_rate > p.imep_transient_rate_threshold
        if self._transient_active:
            # Drop the window rather than mixing pre- and post-manoeuvre cycles, and hold
            # the last steady reading until enough new steady cycles have accumulated.
            self._imep_history.clear()
        else:
            self._imep_history.append(imep_this_cycle / self._imep_baseline)
            self.combustion_instability_pct = self._compute_imep_cov()

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
        # Advanced timing burns earlier, so more of the residual heat is still in the
        # cylinder when the exhaust valve opens: the head takes it instead of the pipe.
        head_split += p.injection_timing_head_split_per_deg * timing_trim_deg
        head_split = _clamp(head_split, 0.15, 0.85)

        residual_power_w = max(0.0, released_power_w - indicated_power_w)
        heat_to_head_w = residual_power_w * head_split

        # ---- 7. per-cylinder EGT ----------------------------------------------
        egt_target: list[float] = []
        for i in range(p.n_cylinders):
            residual_i = max(
                0.0, fuel_power_per_cyl[i] * (1.0 - eta_thermal_per_cyl[i])
            ) * p.heat_to_exhaust_fraction
            charge_flow_i = max(1e-6, m_air_per_cyl + m_fuel_per_cyl[i])
            delta_t = residual_i / (charge_flow_i * p.cp_exhaust_j_per_kg_k)
            delta_t *= self._egt_afr_shape(afrs[i])
            t_k = intake_temp_k + delta_t
            # Blow-by adds a little exhaust heat across all cylinders.
            t_k += p.f_ring_wear_egt_rise_k * fault_state.piston_ring_wear
            # Retarded injection burns late, so more heat leaves through the valve —
            # and a commanded advance does the opposite. `retard_deg` is the net retard
            # from nominal for this cylinder, so it is negative when timing is advanced.
            retard_deg = (
                timing_error_deg if i == timing_idx else 0.0
            ) - timing_trim_deg
            t_k += p.injection_timing_egt_per_deg * retard_deg
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
            injection_timing_deg=self.injection_timing_deg,
            combustion_instability_pct=self.combustion_instability_pct,
        )


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
