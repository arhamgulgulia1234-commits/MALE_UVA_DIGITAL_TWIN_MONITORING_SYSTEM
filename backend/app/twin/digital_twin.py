"""The digital twin: a healthy reference engine flown alongside the real one.

Every tick the twin's EnginePlant is stepped with the *same* commands as the real engine
— identical throttle, altitude and airspeed — but with a FaultState that is permanently
zero. It therefore traces out "what this engine would be doing right now if nothing were
wrong with it".

The residual is the difference:

    residual[channel] = real[channel] - twin[channel]

This is what makes the diagnosis altitude- and throttle-invariant. A raw threshold on oil
pressure fires spuriously every time the engine throttles back for loiter; the *residual*
against a twin that also throttled back stays at zero until something is genuinely wrong.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.engine_params import PARAMS, EngineParams
from app.physics.fault_models import FaultState
from app.physics.plant import CHANNELS, EnginePlant, PlantState


@dataclass
class TwinComparison:
    real: PlantState
    twin: PlantState
    residuals: dict[str, float]


class DigitalTwin:
    """Owns the healthy reference plant and computes residuals against the real one."""

    def __init__(self, params: EngineParams = PARAMS, seed: int = 101) -> None:
        self.p = params
        # A different RNG seed from the real plant, so the twin's stochastic content
        # (vibration noise) is independent rather than a copy — residuals should not
        # cancel noise artificially.
        self.plant = EnginePlant(params, seed=seed, sensor_noise=False)
        self.healthy = FaultState.healthy()

    def reset(self, altitude_m: float) -> None:
        self.plant.reset(altitude_m)

    def substep(
        self,
        dt: float,
        throttle: float,
        altitude_m: float,
        airspeed_ms: float,
    ) -> None:
        """Step the reference engine with the same commands, always fault-free."""
        self.plant.substep(dt, throttle, altitude_m, airspeed_ms, self.healthy)

    def compare(self, real_state: PlantState) -> TwinComparison:
        twin_state = self.plant.finalise_tick()
        real_channels = real_state.channels()
        twin_channels = twin_state.channels()
        residuals = {
            name: real_channels[name] - twin_channels[name] for name in CHANNELS
        }
        return TwinComparison(real=real_state, twin=twin_state, residuals=residuals)
