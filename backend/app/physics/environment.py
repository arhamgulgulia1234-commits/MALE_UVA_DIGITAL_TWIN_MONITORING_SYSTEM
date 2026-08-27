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


def atmosphere(altitude_m: float) -> AtmosphereState:
    """Full ISA state at a given geopotential altitude (metres)."""
    h = max(0.0, altitude_m)

    if h <= TROPOPAUSE_M:
        temperature = T0_K - LAPSE_K_PER_M * h
        pressure = P0_PA * (temperature / T0_K) ** (G_M_PER_S2 / (LAPSE_K_PER_M * R_AIR))
    else:
        temperature = T_TROPOPAUSE_K
        p_trop = P0_PA * (T_TROPOPAUSE_K / T0_K) ** (
            G_M_PER_S2 / (LAPSE_K_PER_M * R_AIR)
        )
        pressure = p_trop * math.exp(
            -G_M_PER_S2 * (h - TROPOPAUSE_M) / (R_AIR * T_TROPOPAUSE_K)
        )

    density = pressure / (R_AIR * temperature)
    return AtmosphereState(
        altitude_m=h,
        temperature_k=temperature,
        pressure_pa=pressure,
        density_kg_per_m3=density,
    )


def density_ratio(altitude_m: float) -> float:
    """sigma = rho(h) / rho(sea level). Used for power/breathing lapse with altitude."""
    return atmosphere(altitude_m).density_kg_per_m3 / RHO0_KG_PER_M3


def ambient_pressure_kpa(altitude_m: float) -> float:
    return atmosphere(altitude_m).pressure_kpa


def ambient_temperature_k(altitude_m: float) -> float:
    return atmosphere(altitude_m).temperature_k
