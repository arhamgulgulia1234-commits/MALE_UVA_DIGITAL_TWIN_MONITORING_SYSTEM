"""EnginePlant — one complete physical engine: thermodynamic core + thermal + lubrication
+ vibration, stepped together.

Two instances exist at runtime and they are the same class:
  * the **real** engine, carrying an evolving FaultState (app/sim/simulation_loop.py)
  * the **healthy reference** twin, whose FaultState is permanently zero
    (app/twin/digital_twin.py)

Running both through identical code is the point — any difference between their outputs
is attributable to the faults alone, not to modelling differences between a "real" and a
"reference" implementation.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.core.engine_params import PARAMS, EngineParams
from app.physics.engine_model import EngineModel, EngineOutputs
from app.physics.fault_models import FaultState
from app.physics.lubrication_model import LubricationModel, LubricationOutputs
from app.physics.thermal_model import ThermalModel, ThermalOutputs
from app.physics.vibration_model import VibrationFeatures, VibrationModel


@dataclass
class PlantState:
    """Everything the rest of the system reads off a plant, in one flat object."""

    rpm: float = 0.0
    manifold_pressure_kpa: float = 0.0
    boost_pressure_kpa: float = 0.0
    egt_c: list[float] = field(default_factory=list)
    cht_c: float = 0.0
    oil_temp_c: float = 0.0
    oil_pressure_kpa: float = 0.0
    fuel_flow_lph: float = 0.0
    afr_mean: float = 14.7
    torque_brake_nm: float = 0.0
    power_brake_kw: float = 0.0
    eta_vol: float = 0.0
    eta_comb_mean: float = 0.0
    vibration_rms: list[float] = field(default_factory=list)
    vibration_features: list[VibrationFeatures] = field(default_factory=list)

    # ---- derived scalars used as residual channels -------------------------

    @property
    def egt_mean_c(self) -> float:
        return sum(self.egt_c) / len(self.egt_c) if self.egt_c else 0.0

    @property
    def egt_spread_c(self) -> float:
        """Hottest minus coldest cylinder — the classic per-cylinder fault tell."""
        return (max(self.egt_c) - min(self.egt_c)) if self.egt_c else 0.0

    @property
    def vibration_rms_mean(self) -> float:
        return (
            sum(self.vibration_rms) / len(self.vibration_rms)
            if self.vibration_rms
            else 0.0
        )

    @property
    def vibration_rms_max(self) -> float:
        return max(self.vibration_rms) if self.vibration_rms else 0.0

    @property
    def crest_factor_max(self) -> float:
        return (
            max(f.crest_factor for f in self.vibration_features)
            if self.vibration_features
            else 0.0
        )

    @property
    def vib_band_high(self) -> float:
        if not self.vibration_features:
            return 0.0
        return sum(f.band_energy_high for f in self.vibration_features) / len(
            self.vibration_features
        )

    def channels(self) -> dict[str, float]:
        """The flat channel dict residuals are computed over."""
        return {
            "rpm": self.rpm,
            "manifold_pressure_kpa": self.manifold_pressure_kpa,
            "boost_pressure_kpa": self.boost_pressure_kpa,
            "egt_mean_c": self.egt_mean_c,
            "egt_spread_c": self.egt_spread_c,
            "cht_c": self.cht_c,
            "oil_temp_c": self.oil_temp_c,
            "oil_pressure_kpa": self.oil_pressure_kpa,
            "fuel_flow_lph": self.fuel_flow_lph,
            "afr_mean": self.afr_mean,
            "torque_brake_nm": self.torque_brake_nm,
            "vibration_rms_mean": self.vibration_rms_mean,
            "vibration_rms_max": self.vibration_rms_max,
            "crest_factor_max": self.crest_factor_max,
            "vib_band_high": self.vib_band_high,
        }


#: Typical magnitude of each channel, used to floor the residual normalisation so a
#: near-zero rolling standard deviation cannot blow the z-score up to infinity.
CHANNEL_SCALES: dict[str, float] = {
    "rpm": 200.0,
    "manifold_pressure_kpa": 20.0,
    "boost_pressure_kpa": 20.0,
    "egt_mean_c": 40.0,
    "egt_spread_c": 15.0,
    "cht_c": 20.0,
    "oil_temp_c": 10.0,
    "oil_pressure_kpa": 40.0,
    "fuel_flow_lph": 3.0,
    "afr_mean": 1.0,
    "torque_brake_nm": 30.0,
    "vibration_rms_mean": 0.05,
    "vibration_rms_max": 0.05,
    "crest_factor_max": 0.5,
    "vib_band_high": 0.05,
}

CHANNELS: tuple[str, ...] = tuple(CHANNEL_SCALES.keys())


class EnginePlant:
    def __init__(
        self,
        params: EngineParams = PARAMS,
        seed: int = 7,
        sensor_noise: bool = True,
    ) -> None:
        self.p = params
        self.engine = EngineModel(params, seed=seed)
        self.thermal = ThermalModel(params)
        self.lubrication = LubricationModel(params)
        self.vibration = VibrationModel(params, seed=seed + 1)
        self.state = PlantState()
        #: The real engine is *measured* through noisy instruments; the digital twin is
        #: computed, so it has no sensors and no sensor noise.
        self.sensor_noise = sensor_noise
        self._noise_rng = random.Random(seed + 977)

    def reset(self, altitude_m: float) -> None:
        self.engine.reset(altitude_m)
        self.thermal.reset()
        self.lubrication.reset()
        self.vibration.reset()

    def substep(
        self,
        dt: float,
        throttle: float,
        altitude_m: float,
        airspeed_ms: float,
        fault_state: FaultState,
    ) -> EngineOutputs:
        """Advance every physics model by one integration sub-step."""
        eng: EngineOutputs = self.engine.step(
            dt, throttle, altitude_m, fault_state, airspeed_ms=airspeed_ms
        )
        therm: ThermalOutputs = self.thermal.step(
            dt,
            heat_to_head_w=eng.heat_to_head_w,
            friction_power_w=eng.friction_power_w,
            altitude_m=altitude_m,
            airspeed_ms=airspeed_ms,
            fault_state=fault_state,
            cooling_flap_command=throttle,
        )
        lub: LubricationOutputs = self.lubrication.step(
            dt, rpm=eng.rpm, oil_temp_c=therm.oil_temp_c, fault_state=fault_state
        )
        vib = self.vibration.generate_window(
            dt,
            rpm=eng.rpm,
            fault_state=fault_state,
            misfire_active=any(eng.misfire_events),
            with_features=False,
        )

        self.state.rpm = eng.rpm
        self.state.manifold_pressure_kpa = eng.manifold_pressure_kpa
        self.state.boost_pressure_kpa = eng.boost_pressure_kpa
        self.state.egt_c = eng.egt_c
        self.state.fuel_flow_lph = eng.fuel_flow_lph
        self.state.afr_mean = eng.afr_mean
        self.state.torque_brake_nm = eng.torque_brake_nm
        self.state.power_brake_kw = eng.power_brake_kw
        self.state.eta_vol = eng.eta_vol
        self.state.eta_comb_mean = eng.eta_comb_mean
        self.state.cht_c = therm.cht_c
        self.state.oil_temp_c = therm.oil_temp_c
        self.state.oil_pressure_kpa = lub.oil_pressure_kpa
        self.state.vibration_rms = vib.rms_per_cylinder
        return eng

    def finalise_tick(self) -> PlantState:
        """Run the once-per-tick work (spectral features, sensor noise) and return the
        state.

        Noise is added here, to the *reported* values only — the integrator state inside
        each physics model stays clean, so noise cannot accumulate into the dynamics."""
        self.state.vibration_features = self.vibration.compute_all_features(self.state.rpm)
        if self.sensor_noise:
            self._apply_sensor_noise()
        return self.state

    def _apply_sensor_noise(self) -> None:
        p = self.p
        gauss = self._noise_rng.gauss
        s = self.state
        s.rpm = max(0.0, s.rpm + gauss(0.0, p.noise_rpm))
        s.manifold_pressure_kpa += gauss(0.0, p.noise_map_kpa)
        s.boost_pressure_kpa += gauss(0.0, p.noise_boost_kpa)
        s.cht_c += gauss(0.0, p.noise_cht_c)
        s.oil_temp_c += gauss(0.0, p.noise_oil_temp_c)
        s.oil_pressure_kpa = max(0.0, s.oil_pressure_kpa + gauss(0.0, p.noise_oil_pressure_kpa))
        s.fuel_flow_lph = max(0.0, s.fuel_flow_lph + gauss(0.0, p.noise_fuel_flow_lph))
        s.egt_c = [e + gauss(0.0, p.noise_egt_c) for e in s.egt_c]
        s.vibration_rms = [
            max(0.0, v + gauss(0.0, p.noise_vibration_rms)) for v in s.vibration_rms
        ]
