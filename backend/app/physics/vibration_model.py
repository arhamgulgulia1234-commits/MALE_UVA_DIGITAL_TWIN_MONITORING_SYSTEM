"""Per-cylinder vibration synthesis and spectral feature extraction.

The vibration signal is generated at its own sample rate (default 1 kHz), independent of
the ODE sub-step, because the firing harmonic reaches ~90 Hz at redline and would alias
badly at the 50 Hz sub-step rate.

Healthy signal per cylinder:
    x(t) = A_fire * sin(2*pi*f_fire*t + phase_cyl) + broadband noise

    f_fire = (RPM / 60) * n_cylinders / revs_per_cycle      [Hz]

Fault signatures:
  * `bearing_wear`      adds rising *broadband* energy (raises the noise floor across
                        the whole spectrum, flattening the crest factor).
  * `misfire`           adds a once-per-cycle *impulse* on the affected cylinder, which
                        shows up as a high crest factor with energy at the sub-harmonic.
  * `turbo_wear`        adds a modest high-frequency imbalance tone.

`compute_features()` turns a rolling buffer into the RMS / crest-factor / dominant-
frequency readout consumed by the anomaly detector and fault classifier.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.core.engine_params import PARAMS, EngineParams
from app.physics.fault_models import FaultState


@dataclass
class VibrationFeatures:
    rms: float
    crest_factor: float
    dominant_frequency_hz: float
    dominant_energy: float
    band_energy_low: float      # below the firing harmonic
    band_energy_high: float     # above the firing harmonic


@dataclass
class VibrationOutputs:
    rms_per_cylinder: list[float]
    features_per_cylinder: list[VibrationFeatures]
    firing_frequency_hz: float


class VibrationModel:
    def __init__(self, params: EngineParams = PARAMS, seed: int = 11) -> None:
        self.p = params
        self._rng = np.random.default_rng(seed)
        self._t = 0.0
        # Rolling window of raw samples per cylinder, ~1 s at vib_sample_rate_hz.
        self._buffers: list[np.ndarray] = [
            np.zeros(int(params.vib_sample_rate_hz)) for _ in range(params.n_cylinders)
        ]

    def reset(self) -> None:
        self._t = 0.0
        for buf in self._buffers:
            buf.fill(0.0)

    def firing_frequency_hz(self, rpm: float) -> float:
        p = self.p
        return (rpm / 60.0) * p.n_cylinders / p.revs_per_cycle

    def compute_all_features(self, rpm: float) -> list[VibrationFeatures]:
        """FFT features for every cylinder's current rolling buffer.

        Called once per telemetry tick rather than per ODE sub-step — at 20x time
        acceleration a tick covers ~100 sub-steps, and running the transform on each
        would be pure waste."""
        f_fire = self.firing_frequency_hz(rpm)
        return [
            compute_features(buf, self.p.vib_sample_rate_hz, f_fire)
            for buf in self._buffers
        ]

    def generate_window(
        self,
        duration_s: float,
        rpm: float,
        fault_state: FaultState,
        misfire_active: bool = False,
        with_features: bool = False,
    ) -> VibrationOutputs:
        """Synthesise `duration_s` of vibration for every cylinder and fold it into the
        rolling buffers, then return RMS (and optionally spectral features) per
        cylinder."""
        p = self.p
        fs = p.vib_sample_rate_hz
        n = max(8, int(duration_s * fs))
        t = self._t + np.arange(n) / fs
        self._t += n / fs

        f_fire = self.firing_frequency_hz(rpm)
        rpm_norm = max(0.0, rpm / max(1.0, p.rpm_redline))
        amp_scale = 1.0 + p.vib_rpm_scaling * rpm_norm

        misfire_idx = (
            fault_state.cylinder_for("misfire", p.n_cylinders)
            if fault_state.misfire > 1e-4
            else -1
        )

        rms_list: list[float] = []
        feats: list[VibrationFeatures] = []

        for i in range(p.n_cylinders):
            phase = 2.0 * math.pi * i / p.n_cylinders

            # Healthy content: firing harmonic + second harmonic + noise floor.
            signal = (
                p.vib_firing_amplitude
                * amp_scale
                * np.sin(2.0 * np.pi * f_fire * t + phase)
            )
            signal += (
                0.35
                * p.vib_firing_amplitude
                * amp_scale
                * np.sin(4.0 * np.pi * f_fire * t + phase)
            )
            signal += self._rng.normal(0.0, p.vib_noise_floor, n)
            signal += p.vib_baseline_rms * 0.35 * amp_scale

            # Bearing wear: broadband energy rising with severity.
            if fault_state.bearing_wear > 1e-4:
                broadband = p.f_bearing_vib_broadband * fault_state.bearing_wear
                signal += self._rng.normal(0.0, broadband * 0.45, n)

            # Turbo imbalance: a high-frequency tone well above the firing harmonic.
            if fault_state.turbo_wear > 1e-4:
                f_turbo = max(120.0, f_fire * 6.0)
                signal += (
                    p.f_turbo_vib_imbalance
                    * fault_state.turbo_wear
                    * np.sin(2.0 * np.pi * f_turbo * t)
                )

            # Misfire: a sharp once-per-cycle impulse on the affected cylinder.
            if i == misfire_idx and fault_state.misfire > 1e-4:
                cycle_hz = max(0.5, (rpm / 60.0) / p.revs_per_cycle)
                period_samples = max(4, int(fs / cycle_hz))
                impulse_amp = p.f_misfire_vib_impulse * fault_state.misfire
                if misfire_active:
                    impulse_amp *= 1.6
                for start in range(0, n, period_samples):
                    width = max(2, period_samples // 24)
                    end = min(n, start + width)
                    decay = np.exp(-np.arange(end - start) / max(1.0, width / 3.0))
                    signal[start:end] += impulse_amp * decay

            # Fold into the rolling buffer.
            buf = self._buffers[i]
            if n >= buf.size:
                buf[:] = signal[-buf.size :]
            else:
                buf[:-n] = buf[n:]
                buf[-n:] = signal
            self._buffers[i] = buf

            rms_list.append(float(np.sqrt(np.mean(np.square(signal)))))
            if with_features:
                feats.append(compute_features(buf, fs, f_fire))

        return VibrationOutputs(
            rms_per_cylinder=rms_list,
            features_per_cylinder=feats,
            firing_frequency_hz=f_fire,
        )

    def buffer(self, cylinder_index: int) -> np.ndarray:
        return self._buffers[cylinder_index]


def compute_features(
    rolling_buffer: np.ndarray,
    sample_rate_hz: float = PARAMS.vib_sample_rate_hz,
    firing_frequency_hz: float | None = None,
) -> VibrationFeatures:
    """RMS, crest factor, and an rfft-based dominant-frequency / band-energy readout.

    Feeds both the frontend's VibrationSpectrum panel and the anomaly detector."""
    x = np.asarray(rolling_buffer, dtype=float)
    if x.size < 8:
        return VibrationFeatures(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    x = x - float(np.mean(x))
    rms = float(np.sqrt(np.mean(np.square(x))))
    peak = float(np.max(np.abs(x)))
    crest = peak / rms if rms > 1e-9 else 0.0

    window = np.hanning(x.size)
    # Amplitude-normalised spectrum: bins carry the amplitude of their component rather
    # than a magnitude that grows with buffer length, so band energies stay comparable to
    # the signal's own RMS and can share a fixed full-scale in the anomaly detector.
    coherent_gain = float(np.mean(window))
    spectrum = 2.0 * np.abs(np.fft.rfft(x * window)) / (x.size * max(1e-9, coherent_gain))
    freqs = np.fft.rfftfreq(x.size, d=1.0 / sample_rate_hz)

    if spectrum.size > 1:
        idx = int(np.argmax(spectrum[1:]) + 1)
        dominant_freq = float(freqs[idx])
        dominant_energy = float(spectrum[idx])
    else:
        dominant_freq = 0.0
        dominant_energy = 0.0

    if firing_frequency_hz and firing_frequency_hz > 1.0:
        split = firing_frequency_hz
    else:
        split = float(freqs[-1]) / 4.0
    low_mask = freqs <= split
    high_mask = freqs > split
    # Per-band RMS (Parseval), so these read in the same units as the signal.
    band_low = float(np.sqrt(np.sum(np.square(spectrum[low_mask])) / 2.0))
    band_high = float(np.sqrt(np.sum(np.square(spectrum[high_mask])) / 2.0))

    return VibrationFeatures(
        rms=rms,
        crest_factor=crest,
        dominant_frequency_hz=dominant_freq,
        dominant_energy=dominant_energy,
        band_energy_low=band_low,
        band_energy_high=band_high,
    )
