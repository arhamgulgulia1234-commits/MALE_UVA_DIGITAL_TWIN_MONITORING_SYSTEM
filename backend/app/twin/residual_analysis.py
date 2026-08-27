"""Statistical monitoring of twin residuals.

For each residual channel we keep an exponentially-weighted moving average of the value
and of its variance:

    mu_t     = a * x_t + (1 - a) * mu_{t-1}
    var_t    = a * (x_t - mu_{t-1})^2 + (1 - a) * var_{t-1}
    z_t      = |mu_t| / max(sqrt(var_t), floor)

A channel is *flagged* when z crosses `z_threshold` — that is, when its residual has
drifted by more than a configurable multiple of its own recent noise. This is the actual
anomaly signal: it adapts to how noisy each channel normally is rather than relying on
fixed thresholds against raw telemetry values.

The variance floor comes from `CHANNEL_SCALES` in app/physics/plant.py so a quiet channel
whose variance has collapsed toward zero cannot produce an infinite z-score.

`feature_vector()` is the single shared definition of the ML feature layout — both
app/ml/train/generate_training_data.py (offline) and app/ml/fault_classifier.py (at
runtime) call it, so training and inference can never drift apart.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.physics.plant import CHANNEL_SCALES, CHANNELS


@dataclass
class ChannelStats:
    mean: float = 0.0
    variance: float = 0.0
    z_score: float = 0.0
    flagged: bool = False


@dataclass
class ResidualReport:
    stats: dict[str, ChannelStats]
    warmed_up: bool

    def z(self, channel: str) -> float:
        stat = self.stats.get(channel)
        return stat.z_score if stat else 0.0

    def mean(self, channel: str) -> float:
        stat = self.stats.get(channel)
        return stat.mean if stat else 0.0

    def flagged_channels(self) -> list[str]:
        return [name for name, s in self.stats.items() if s.flagged]


class ResidualMonitor:
    """EWMA statistics over the twin residuals.

    The smoothing constant is expressed as a time constant in *simulated* seconds and
    converted per update via `alpha = 1 - exp(-dt/tau)`. That matters: the operator can
    run the simulation at 20x for a demo, and a filter whose memory was measured in ticks
    would then appear to take twenty times longer in simulated time to notice a fault.
    Anchoring it to simulated time keeps the diagnosis responsive at every time scale.
    """

    def __init__(
        self,
        tau_s: float = 6.0,
        z_threshold: float = 3.0,
        warmup_s: float = 20.0,
        variance_floor_fraction: float = 0.08,
    ) -> None:
        self.tau_s = tau_s
        self.z_threshold = z_threshold
        self.warmup_s = warmup_s
        self.variance_floor_fraction = variance_floor_fraction
        self._stats: dict[str, ChannelStats] = {c: ChannelStats() for c in CHANNELS}
        self._elapsed_s = 0.0

    def reset(self) -> None:
        self._stats = {c: ChannelStats() for c in CHANNELS}
        self._elapsed_s = 0.0

    def _floor(self, channel: str) -> float:
        return CHANNEL_SCALES.get(channel, 1.0) * self.variance_floor_fraction

    def update(self, residuals: dict[str, float], dt_s: float = 0.1) -> ResidualReport:
        self._elapsed_s += dt_s
        a = 1.0 - math.exp(-max(1e-6, dt_s) / max(1e-6, self.tau_s))
        warmed = self._elapsed_s >= self.warmup_s
        for channel in CHANNELS:
            x = residuals.get(channel, 0.0)
            stat = self._stats[channel]
            prev_mean = stat.mean
            stat.mean = a * x + (1.0 - a) * prev_mean
            stat.variance = a * (x - prev_mean) ** 2 + (1.0 - a) * stat.variance
            std = max(stat.variance**0.5, self._floor(channel))
            stat.z_score = abs(stat.mean) / std
            stat.flagged = warmed and stat.z_score >= self.z_threshold
        return ResidualReport(stats=dict(self._stats), warmed_up=warmed)

    # ---- ML feature layout ---------------------------------------------------

    def feature_names(self) -> list[str]:
        names: list[str] = []
        for channel in CHANNELS:
            names.append(f"{channel}__norm_mean")
            names.append(f"{channel}__z")
        return names

    def feature_vector(self) -> list[float]:
        """Normalised residual mean and z-score per channel.

        Normalising the mean by the channel scale keeps every feature O(1) so a
        RandomForest is not dominated by the RPM channel's raw magnitude."""
        values: list[float] = []
        for channel in CHANNELS:
            stat = self._stats[channel]
            scale = CHANNEL_SCALES.get(channel, 1.0)
            values.append(stat.mean / scale)
            values.append(min(stat.z_score, 50.0))
        return values
