"""EWMA z-scoring for fusion-derived signals.

This is the exact statistical treatment `app/twin/residual_analysis.py` gives real-vs-
twin residuals — an EWMA mean and variance per channel, reduced to a z-score — applied to
a different comparison axis: a raw sensor's disagreement with its own fused estimate, and
the oil-pressure Kalman gain's departure from its own healthy baseline. None of these
signals involve the digital twin at all, which is exactly why they are not just six more
entries in `app.physics.plant.CHANNELS`: that list, and `ResidualMonitor`'s hard-wiring to
it, is specifically the "real minus twin" comparison. Fusion innovations are "sensor
minus fused estimate," computed for the real engine alone — the twin has no sensors and
nothing to fuse (`app/twin/digital_twin.py` says so directly) — so they get their own
small monitor rather than being forced through an interface built for the other kind of
comparison.

`feature_names()` / `feature_vector()` mirror `ResidualMonitor`'s exactly (same
mean/z/std triple per channel) so `app/ml/fault_classifier.py` and
`app/ml/train/generate_training_data.py` can simply concatenate this onto the residual
feature vector — one shared layout, same as the residual monitor's own docstring insists
on, just assembled from two monitors instead of one.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

FUSION_CHANNEL_SCALES: dict[str, float] = {
    "cht_innovation_primary": 3.0,
    "cht_innovation_secondary": 3.0,
    "rpm_innovation_tachometer": 5.0,
    "rpm_innovation_vibration_derived": 15.0,
    "oil_pressure_innovation": 5.0,
    "oil_pressure_gain_deviation": 0.15,
}

FUSION_CHANNELS: tuple[str, ...] = tuple(FUSION_CHANNEL_SCALES.keys())


@dataclass
class _ChannelStats:
    mean: float = 0.0
    variance: float = 0.0
    z_score: float = 0.0


class FusionMonitor:
    """EWMA statistics over the six fusion signals in `FUSION_CHANNELS`.

    Same time-constant convention as `ResidualMonitor`: `tau_s` is in *simulated*
    seconds, so a 20x-accelerated demo does not make this take twenty times longer,
    simulated-time-wise, to notice a fusion channel misbehaving.
    """

    def __init__(
        self, tau_s: float = 6.0, variance_floor_fraction: float = 0.08
    ) -> None:
        self.tau_s = tau_s
        self.variance_floor_fraction = variance_floor_fraction
        self._stats: dict[str, _ChannelStats] = {c: _ChannelStats() for c in FUSION_CHANNELS}

    def reset(self) -> None:
        self._stats = {c: _ChannelStats() for c in FUSION_CHANNELS}

    def _floor(self, channel: str) -> float:
        return FUSION_CHANNEL_SCALES.get(channel, 1.0) * self.variance_floor_fraction

    def update(self, values: dict[str, float], dt_s: float) -> dict[str, float]:
        """Fold in one tick's fusion signals; returns this tick's z-scores."""
        a = 1.0 - math.exp(-max(1e-6, dt_s) / max(1e-6, self.tau_s))
        z_scores: dict[str, float] = {}
        for channel in FUSION_CHANNELS:
            x = values.get(channel, 0.0)
            stat = self._stats[channel]
            prev_mean = stat.mean
            stat.mean = a * x + (1.0 - a) * prev_mean
            stat.variance = a * (x - prev_mean) ** 2 + (1.0 - a) * stat.variance
            std = max(stat.variance**0.5, self._floor(channel))
            stat.z_score = abs(stat.mean) / std
            z_scores[channel] = stat.z_score
        return z_scores

    def z(self, channel: str) -> float:
        stat = self._stats.get(channel)
        return stat.z_score if stat else 0.0

    def mean(self, channel: str) -> float:
        stat = self._stats.get(channel)
        return stat.mean if stat else 0.0

    def all_z(self) -> dict[str, float]:
        return {c: s.z_score for c, s in self._stats.items()}

    # ---- ML feature layout, mirroring ResidualMonitor exactly -----------------

    def feature_names(self) -> list[str]:
        names: list[str] = []
        for channel in FUSION_CHANNELS:
            names.append(f"{channel}__norm_mean")
            names.append(f"{channel}__z")
            names.append(f"{channel}__norm_std")
        return names

    def feature_vector(self) -> list[float]:
        values: list[float] = []
        for channel in FUSION_CHANNELS:
            stat = self._stats[channel]
            scale = FUSION_CHANNEL_SCALES.get(channel, 1.0)
            values.append(stat.mean / scale)
            values.append(min(stat.z_score, 50.0))
            values.append(min((stat.variance**0.5) / scale, 50.0))
        return values
