"""International Standard Atmosphere (ISA) model.

Provides ambient pressure, temperature and density as a function of geopotential
altitude, plus the density ratio sigma = rho(h) / rho(0) that every other physics module
uses for altitude-driven power and breathing lapse.

Equations (troposphere, h <= 11 000 m):
    T(h) = T0 - L * h
    p(h) = p0 * (T(h)/T0) ^ (g / (L * R))
    rho  = p / (R * T)

Above the tropopause the temperature is isothermal at 216.65 K and pressure decays
exponentially:
    p(h) = p_trop * exp(-g * (h - 11000) / (R * T_trop))
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# ISA sea-level constants
T0_K = 288.15
P0_PA = 101_325.0
LAPSE_K_PER_M = 0.0065
G_M_PER_S2 = 9.80665
R_AIR = 287.05
TROPOPAUSE_M = 11_000.0
T_TROPOPAUSE_K = T0_K - LAPSE_K_PER_M * TROPOPAUSE_M  # 216.65 K
RHO0_KG_PER_M3 = P0_PA / (R_AIR * T0_K)               # 1.225 kg/m^3


@dataclass(frozen=True)
class AtmosphereState:
    altitude_m: float
    temperature_k: float
    pressure_pa: float
    density_kg_per_m3: float

    @property
    def pressure_kpa(self) -> float:
        return self.pressure_pa / 1000.0

    @property
    def temperature_c(self) -> float:
        return self.temperature_k - 273.15


def isa_temperature_k(altitude_m: float) -> float:
    """The temperature ISA *predicts* for this altitude, before any hot-day override."""
    h = max(0.0, altitude_m)
    if h <= TROPOPAUSE_M:
        return T0_K - LAPSE_K_PER_M * h
    return T_TROPOPAUSE_K


#: Phase 4: size-one memo for `atmosphere()`.
#:
#: A Test Bench scenario holds altitude and ambient temperature constant for its whole
#: run, and every integration sub-step evaluates this function about six times (engine,
#: breathing derate, turbo, thermal). Caching the last result turns those into one tuple
#: comparison and roughly halves the cost of a headless scenario. The live simulation
#: climbs continuously, so it misses every time and pays only that comparison. This is
#: safe because `AtmosphereState` is frozen — callers cannot mutate a shared instance.
_atm_cache_key: tuple[float, float | None] | None = None
_atm_cache_value: "AtmosphereState | None" = None


def atmosphere(
    altitude_m: float, ambient_temperature_c: float | None = None
) -> AtmosphereState:
    """Full atmospheric state at a given geopotential altitude.

    Phase 3: `ambient_temperature_c` overrides the ISA temperature for that altitude —
    a hot-and-high day. Pressure still follows the ISA column (the weather does not
    change the mass of air above you), but density is recomputed at the *actual*
    temperature via rho = p/(R*T). Hotter air is thinner, so the engine breathes worse
    and the cooling system has a smaller temperature gradient to work against, on top of
    whatever the altitude was already costing.
    """
    global _atm_cache_key, _atm_cache_value
    key = (altitude_m, ambient_temperature_c)
    if _atm_cache_value is not None and _atm_cache_key == key:
        return _atm_cache_value

    h = max(0.0, altitude_m)

    if h <= TROPOPAUSE_M:
        isa_temp = T0_K - LAPSE_K_PER_M * h
        pressure = P0_PA * (isa_temp / T0_K) ** (G_M_PER_S2 / (LAPSE_K_PER_M * R_AIR))
    else:
        isa_temp = T_TROPOPAUSE_K
        p_trop = P0_PA * (T_TROPOPAUSE_K / T0_K) ** (
            G_M_PER_S2 / (LAPSE_K_PER_M * R_AIR)
        )
        pressure = p_trop * math.exp(
            -G_M_PER_S2 * (h - TROPOPAUSE_M) / (R_AIR * T_TROPOPAUSE_K)
        )

    if ambient_temperature_c is None:
        temperature = isa_temp
    else:
        temperature = max(200.0, ambient_temperature_c + 273.15)

    density = pressure / (R_AIR * temperature)
    state = AtmosphereState(
        altitude_m=h,
        temperature_k=temperature,
        pressure_pa=pressure,
        density_kg_per_m3=density,
    )
    _atm_cache_key, _atm_cache_value = key, state
    return state


def density_ratio(
    altitude_m: float, ambient_temperature_c: float | None = None
) -> float:
    """sigma = rho / rho(sea level, ISA). Drives power/breathing lapse.

    With a hot-day override this captures both effects at once: less pressure with
    altitude, and less density again for the extra temperature."""
    return (
        atmosphere(altitude_m, ambient_temperature_c).density_kg_per_m3
        / RHO0_KG_PER_M3
    )


def ambient_pressure_kpa(altitude_m: float) -> float:
    return atmosphere(altitude_m).pressure_kpa


def ambient_temperature_k(
    altitude_m: float, ambient_temperature_c: float | None = None
) -> float:
    return atmosphere(altitude_m, ambient_temperature_c).temperature_k


def isa_deviation_k(altitude_m: float, ambient_temperature_c: float | None) -> float:
    """How many Kelvin hotter than ISA the day is. Zero when no override is set."""
    if ambient_temperature_c is None:
        return 0.0
    return (ambient_temperature_c + 273.15) - isa_temperature_k(altitude_m)
