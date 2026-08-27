"""Generate labelled training data for the fault classifier by running the simulation
headless with randomised fault injections.

Each episode: start a fresh simulation, fly it healthy long enough for the residual
monitor to warm up (collecting `healthy` samples), then inject one randomly chosen fault
at a random severity and ramp, and collect samples labelled with that fault type.

Feature vectors come from `ResidualMonitor.feature_vector()` — the same call the runtime
classifier makes — so training and inference can never drift out of sync.

Samples whose severity sits in an ambiguous band (barely-there faults that genuinely look
healthy) are dropped rather than mislabelled, which otherwise teaches the model that
healthy residuals belong to a fault class.

Usage:
    python -m app.ml.train.generate_training_data [--episodes 12] [--out data.npz]
"""
from __future__ import annotations

import argparse
import logging
import random
from pathlib import Path

import numpy as np

from app.physics.fault_models import FAULT_TYPES
from app.physics.sensor_fault_model import SENSOR_FAULT_TYPES
from app.sim.mission_profiles import PHASE_ORDER
from app.sim.simulation_loop import SimulationLoop

logging.getLogger("app.ml.fault_classifier").setLevel(logging.ERROR)

HEALTHY_LABEL = "healthy"
DEFAULT_OUT = Path(__file__).parent / "training_data.npz"

#: Below this severity a fault is indistinguishable from healthy — drop, don't mislabel.
AMBIGUOUS_BELOW = 0.25
#: Samples taken while healthy, after the residual monitor has warmed up.
WARMUP_TICKS = 220
HEALTHY_SAMPLE_TICKS = 90
FAULT_SETTLE_TICKS = 40
FAULT_SAMPLE_TICKS = 150


def run_episode(
    fault_type: str,
    rng: random.Random,
    time_scale: float = 5.0,
    is_sensor_fault: bool = False,
) -> tuple[list[list[float]], list[str]]:
    """Fly one episode: healthy for a while, then one fault ramped in.

    `is_sensor_fault` routes the injection through SensorFaultState instead of
    FaultState. Both produce residuals against the twin, but only the physical ones
    actually degrade the engine — teaching the classifier that difference is the entire
    point of including them here as their own labelled classes."""
    sim = SimulationLoop()
    sim.set_time_scale(time_scale)

    # Randomise the operating point so the classifier learns fault signatures rather
    # than "what cruise looks like".
    sim.jump_phase(rng.choice(PHASE_ORDER))
    if rng.random() < 0.5:
        sim.set_throttle(rng.uniform(0.35, 1.0))
    # Vary the weather too, so a hot day is not mistaken for a cooling fault.
    if rng.random() < 0.25:
        sim.set_ambient_temperature(rng.uniform(-20.0, 48.0))

    features: list[list[float]] = []
    labels: list[str] = []

    for _ in range(WARMUP_TICKS):
        sim.tick(0.1)

    for _ in range(HEALTHY_SAMPLE_TICKS):
        sim.tick(0.1)
        features.append(sim.residuals.feature_vector())
        labels.append(HEALTHY_LABEL)

    severity = rng.uniform(0.35, 1.0)
    ramp = rng.uniform(5.0, 45.0)
    if is_sensor_fault:
        sim.inject_sensor_fault(fault_type, severity, ramp)
        severity_of = sim.sensor_faults.severity
    else:
        sim.inject_fault(fault_type, severity, ramp)
        severity_of = sim.faults.severity

    for _ in range(FAULT_SETTLE_TICKS):
        sim.tick(0.1)

    for _ in range(FAULT_SAMPLE_TICKS):
        sim.tick(0.1)
        if severity_of(fault_type) < AMBIGUOUS_BELOW:
            continue
        features.append(sim.residuals.feature_vector())
        labels.append(fault_type)

    return features, labels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episodes", type=int, default=12,
                        help="episodes per fault type (default: 12)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=20260828)
    parser.add_argument("--time-scale", type=float, default=5.0)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    all_features: list[list[float]] = []
    all_labels: list[str] = []

    all_episodes: list[int] = []

    # Physical faults and sensor faults are both labelled classes. The classifier has to
    # learn to separate "the engine is broken" from "the instrument is broken", and it
    # cannot do that if it has only ever seen the former.
    catalogue = [(ft, False) for ft in FAULT_TYPES] + [
        (ft, True) for ft in SENSOR_FAULT_TYPES
    ]

    total = args.episodes * len(catalogue)
    done = 0
    for fault_type, is_sensor in catalogue:
        for _ in range(args.episodes):
            feats, labs = run_episode(fault_type, rng, args.time_scale, is_sensor)
            all_features.extend(feats)
            all_labels.extend(labs)
            # Tag every sample with its episode so training can hold out whole episodes.
            # Samples within one episode are near-duplicates of each other; splitting
            # them randomly leaks the test set into training and reports ~100% accuracy.
            all_episodes.extend([done] * len(feats))
            done += 1
            kind = "sensor" if is_sensor else "physical"
            print(f"  [{done:3d}/{total}] {fault_type:30s} {kind:8s} "
                  f"samples={len(feats):4d} total={len(all_labels)}", flush=True)

    x = np.asarray(all_features, dtype=np.float32)
    y = np.asarray(all_labels)
    episodes = np.asarray(all_episodes, dtype=np.int32)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.out, X=x, y=y, episode=episodes)

    print(f"\nWrote {x.shape[0]} samples x {x.shape[1]} features -> {args.out}")
    labels_unique, counts = np.unique(y, return_counts=True)
    for label, count in zip(labels_unique, counts):
        print(f"  {label:22s} {count}")


if __name__ == "__main__":
    main()
