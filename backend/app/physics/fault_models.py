"""Fault state and ramping.

Phase 2 injects faults as *parameter perturbations*: this object carries a 0-1 severity
per fault type and is handed to every physics model's step(), which decides how that
severity distorts its own equations. Nothing here writes telemetry values directly — the
visible signatures emerge from the physics.

`inject()` and `clear()` linearly ramp a severity toward a target over `ramp_seconds`;
`step(dt)` advances all in-flight ramps and must be called once per simulation sub-step.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

FAULT_TYPES: tuple[str, ...] = (
    "misfire",
    "spark_degradation",
    "piston_ring_wear",
    "bearing_wear",
    "oil_pump_degradation",
    "cooling_degradation",
    "fuel_injector_clog",
    "turbo_wear",
    "air_filter_clog",
)

#: Faults that manifest on one specific cylinder rather than the whole engine.
CYLINDER_LOCALISED_FAULTS: tuple[str, ...] = (
    "misfire",
    "spark_degradation",
    "fuel_injector_clog",
)


@dataclass
class _Ramp:
    start_value: float
    target_value: float
    duration_s: float
    elapsed_s: float = 0.0

    def advance(self, dt: float) -> tuple[float, bool]:
        """Return (current value, finished)."""
        if self.duration_s <= 0.0:
            return self.target_value, True
        self.elapsed_s += dt
        frac = min(1.0, self.elapsed_s / self.duration_s)
        value = self.start_value + (self.target_value - self.start_value) * frac
        return value, frac >= 1.0


@dataclass
class FaultState:
    """0-1 severity per fault type, plus which cylinder the localised faults target."""

    misfire: float = 0.0
    spark_degradation: float = 0.0
    piston_ring_wear: float = 0.0
    bearing_wear: float = 0.0
    oil_pump_degradation: float = 0.0
    cooling_degradation: float = 0.0
    fuel_injector_clog: float = 0.0
    turbo_wear: float = 0.0
    air_filter_clog: float = 0.0

    #: fault type -> 0-indexed cylinder it affects
    target_cylinder: dict[str, int] = field(default_factory=dict)
    #: fault type -> wall-clock time the fault was first injected
    started_at: dict[str, float] = field(default_factory=dict)

    _ramps: dict[str, _Ramp] = field(default_factory=dict, repr=False)
    _rng: random.Random = field(default_factory=lambda: random.Random(1234), repr=False)

    # ---- query ---------------------------------------------------------------

    def severity(self, fault_type: str) -> float:
        return float(getattr(self, fault_type))

    def active(self) -> dict[str, float]:
        """Fault types with non-negligible severity, mapped to that severity."""
        return {
            ft: self.severity(ft) for ft in FAULT_TYPES if self.severity(ft) > 1e-4
        }

    def any_active(self) -> bool:
        return bool(self.active())

    def cylinder_for(self, fault_type: str, n_cylinders: int) -> int:
        """Which cylinder a localised fault targets (stable once chosen)."""
        if fault_type not in self.target_cylinder:
            self.target_cylinder[fault_type] = self._rng.randrange(n_cylinders)
        return self.target_cylinder[fault_type] % n_cylinders

    # ---- mutation ------------------------------------------------------------

    def inject(
        self,
        fault_type: str,
        target_severity: float,
        ramp_seconds: float,
        *,
        now: float | None = None,
        n_cylinders: int = 4,
    ) -> None:
        if fault_type not in FAULT_TYPES:
            raise ValueError(f"unknown fault type: {fault_type}")
        target = max(0.0, min(1.0, target_severity))
        self._ramps[fault_type] = _Ramp(
            start_value=self.severity(fault_type),
            target_value=target,
            duration_s=max(0.0, ramp_seconds),
        )
        if fault_type in CYLINDER_LOCALISED_FAULTS:
            self.cylinder_for(fault_type, n_cylinders)
        if fault_type not in self.started_at and now is not None:
            self.started_at[fault_type] = now

    def clear(self, fault_type: str, ramp_seconds: float = 8.0) -> None:
        """Ramp a fault back down to healthy over `ramp_seconds`."""
        if fault_type not in FAULT_TYPES:
            raise ValueError(f"unknown fault type: {fault_type}")
        self._ramps[fault_type] = _Ramp(
            start_value=self.severity(fault_type),
            target_value=0.0,
            duration_s=max(0.0, ramp_seconds),
        )

    def clear_all(self, ramp_seconds: float = 8.0) -> None:
        for ft in FAULT_TYPES:
            if self.severity(ft) > 1e-4:
                self.clear(ft, ramp_seconds)

    def step(self, dt: float) -> None:
        """Advance every in-flight ramp by dt seconds of simulated time."""
        finished: list[str] = []
        for fault_type, ramp in self._ramps.items():
            value, done = ramp.advance(dt)
            setattr(self, fault_type, value)
            if done:
                finished.append(fault_type)
        for fault_type in finished:
            del self._ramps[fault_type]
            if self.severity(fault_type) <= 1e-4:
                setattr(self, fault_type, 0.0)
                self.started_at.pop(fault_type, None)
                self.target_cylinder.pop(fault_type, None)

    def snapshot(self) -> dict[str, float]:
        return {ft: self.severity(ft) for ft in FAULT_TYPES}

    @classmethod
    def healthy(cls) -> "FaultState":
        """A permanently-zero fault state — used for the digital twin reference model."""
        return cls()
