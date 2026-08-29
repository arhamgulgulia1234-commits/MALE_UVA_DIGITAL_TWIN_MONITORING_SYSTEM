"""Three named operating presets, each resolved by the operating-point optimizer.

A preset is not a hardcoded throttle number. It is a *question* — "what should this
engine run for maximum endurance?" — answered by `optimize_operating_point()` at standard
reference conditions, with the answer cached. That means the presets stay consistent with
the physics: retune a coefficient in `engine_params.py` and the presets move with it,
because they were never written down anywhere.

Reference conditions are sea level at 20 degC, which is what a preset *card* means: a
book figure quoted at a standard datum, the way any engine's published settings are. For
a specific mission, the Optimizer panel answers at that mission's actual altitude and
temperature instead.

On "max endurance" versus "max range"
-------------------------------------
They are genuinely different objectives: endurance minimises fuel burned per *hour*, range
minimises fuel burned per *mile*. Separating them properly needs an airframe drag polar —
endurance flies at minimum power required, range at the best lift-to-drag speed — and this
project models an engine, not an airframe. What the optimizer can answer honestly is "make
the required cruise power for the least fuel", which is the engine's contribution to both.
The preset therefore uses the `max_range` objective and this note exists so nobody mistakes
it for a full endurance analysis. See docs/deployment-roadmap.md.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.engine_params import PARAMS
from app.ml.operating_point_optimizer import (
    OperatingPointResult,
    optimize_operating_point,
)

logger = logging.getLogger(__name__)

REFERENCE_ALTITUDE_M = 0.0
REFERENCE_AMBIENT_C = 20.0
"""Sea level, 20 degC — the datum the preset cards quote."""


@dataclass(frozen=True)
class PresetDefinition:
    name: str
    label: str
    objective: str
    description: str
    trade_off: str


PRESET_DEFINITIONS: dict[str, PresetDefinition] = {
    "max_endurance": PresetDefinition(
        name="max_endurance",
        label="Max Endurance",
        objective="max_range",
        description=(
            "Hold cruise power for the least fuel. Mixture is trimmed toward the "
            "efficiency peak and throttle set as low as the power requirement allows."
        ),
        trade_off="Longest time on station. Least power in hand if you need it.",
    ),
    "balanced": PresetDefinition(
        name="balanced",
        label="Balanced",
        objective="balanced",
        description=(
            "A weighted compromise between fuel burn, available power and the rate the "
            "engine is wearing out."
        ),
        trade_off="No single figure is best; nothing is badly compromised either.",
    ),
    "max_power": PresetDefinition(
        name="max_power",
        label="Max Power",
        objective="max_power",
        description=(
            "The most brake power the engine can make without exceeding a cylinder head, "
            "exhaust, oil temperature or oil pressure limit."
        ),
        trade_off="Power when it is needed, at a markedly higher fuel burn and wear rate.",
    ),
}

PRESET_NAMES: tuple[str, ...] = tuple(PRESET_DEFINITIONS)

#: Resolved presets, computed on first request. Each one is a few seconds of optimizer
#: search, and the reference conditions never change, so there is nothing to gain from
#: recomputing them — and a preset that answered differently on the second click would be
#: worse than one that took a moment on the first.
_cache: dict[str, OperatingPointResult] = {}


def clear_cache() -> None:
    """Drop the cached results. Only useful if engine parameters are retuned at runtime."""
    _cache.clear()


def resolve_preset(name: str) -> OperatingPointResult:
    """The optimizer's answer for one preset at reference conditions."""
    definition = PRESET_DEFINITIONS.get(name)
    if definition is None:
        raise ValueError(
            f"unknown preset '{name}'; expected one of {', '.join(PRESET_NAMES)}"
        )
    cached = _cache.get(name)
    if cached is not None:
        return cached
    logger.info("Resolving mission preset '%s' (%s)", name, definition.objective)
    result = optimize_operating_point(
        REFERENCE_ALTITUDE_M, REFERENCE_AMBIENT_C, definition.objective
    )
    _cache[name] = result
    return result


def preset_card(name: str) -> dict:
    """One preset in the shape the Test Bench cards render.

    The setpoint is separated from the presentation deliberately: `setpoint` is what gets
    POSTed to /control/apply-preset, while everything else exists to let an operator decide
    whether they want to.
    """
    definition = PRESET_DEFINITIONS[name]
    result = resolve_preset(name)
    rec = result.recommended
    return {
        "name": definition.name,
        "label": definition.label,
        "objective": definition.objective,
        "description": definition.description,
        "trade_off": definition.trade_off,
        "reference_conditions": {
            "altitude_m": REFERENCE_ALTITUDE_M,
            "ambient_temperature_c": REFERENCE_AMBIENT_C,
        },
        "setpoint": {
            "throttle": round(rec.throttle_pct / 100.0, 4),
            "throttle_pct": round(rec.throttle_pct, 1),
            "afr_trim": round(rec.afr_trim, 2),
            "injection_timing_trim_deg": round(rec.injection_timing_trim_deg, 2),
        },
        "predicted": rec.to_dict()["predicted"],
        "deltas": result.comparison.to_dict(),
        "safety": [c.to_dict() for c in rec.safety],
        "feasible": result.feasible,
        "safety_notes": result.safety_notes,
        "rationale": result.rationale,
    }


def all_preset_cards() -> list[dict]:
    """Every preset, in the order they are shown. Resolves any that are not cached yet."""
    return [preset_card(name) for name in PRESET_NAMES]


def preset_setpoint(name: str) -> dict:
    """Just the commandable numbers for one preset."""
    result = resolve_preset(name)
    rec = result.recommended
    return {
        "throttle": rec.throttle_pct / 100.0,
        "afr_trim": rec.afr_trim,
        "injection_timing_trim_deg": rec.injection_timing_trim_deg,
    }


def reference_summary() -> dict:
    return {
        "altitude_m": REFERENCE_ALTITUDE_M,
        "ambient_temperature_c": REFERENCE_AMBIENT_C,
        "nominal_cruise_throttle_pct": PARAMS.nominal_cruise_throttle * 100.0,
        "presets": list(PRESET_NAMES),
    }
