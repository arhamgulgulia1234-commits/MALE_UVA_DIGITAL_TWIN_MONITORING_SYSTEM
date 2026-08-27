"""Train the RandomForest fault classifier and save it with joblib.

**The train/test split is by episode, not by sample.** Samples within one episode are
consecutive 100 ms snapshots of the same slowly-evolving fault, so they are near-
duplicates of each other. Splitting them randomly puts copies of the same moment in both
train and test and reports ~100% accuracy that means nothing. Holding out whole episodes
asks the honest question: given a fault it has never seen ramp, can the model name it?

Datasets generated before episode tagging are handled by reconstructing episode
boundaries from the label sequence — each episode emits its healthy block first, so a
transition from a fault label back to `healthy` marks a new episode.

Usage:
    python -m app.ml.train.train_classifier [--data training_data.npz] [--out ...]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

DEFAULT_DATA = Path(__file__).parent / "training_data.npz"
DEFAULT_OUT = Path(__file__).parent.parent / "artifacts" / "fault_classifier.joblib"


def _reconstruct_episodes(y: np.ndarray) -> np.ndarray:
    """Infer episode boundaries from the label sequence.

    Each episode writes its `healthy` samples first and its fault samples second, so a
    transition from a fault label back to `healthy` starts a new episode."""
    groups = np.zeros(len(y), dtype=np.int32)
    current = 0
    for i in range(1, len(y)):
        if y[i] == "healthy" and y[i - 1] != "healthy":
            current += 1
        groups[i] = current
    return groups


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--trees", type=int, default=300)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    if not args.data.exists():
        raise SystemExit(
            f"No training data at {args.data}. Run:\n"
            f"  python -m app.ml.train.generate_training_data"
        )

    import joblib
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import classification_report, confusion_matrix
    from sklearn.model_selection import GroupShuffleSplit

    blob = np.load(args.data, allow_pickle=True)
    x, y = blob["X"], blob["y"]

    if "episode" in blob:
        groups = blob["episode"]
    else:
        groups = _reconstruct_episodes(y)
        print("(no episode tags in dataset — reconstructed from label transitions)")

    print(f"Loaded {x.shape[0]} samples x {x.shape[1]} features, "
          f"{len(np.unique(y))} classes, {len(np.unique(groups))} episodes")

    splitter = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=args.seed)
    train_idx, test_idx = next(splitter.split(x, y, groups=groups))
    x_train, x_test = x[train_idx], x[test_idx]
    y_train, y_test = y[train_idx], y[test_idx]
    print(f"Held out {len(np.unique(groups[test_idx]))} whole episodes "
          f"({len(test_idx)} samples) for testing")

    model = RandomForestClassifier(
        n_estimators=args.trees,
        min_samples_leaf=2,
        class_weight="balanced_subsample",
        n_jobs=-1,
        random_state=args.seed,
    )
    model.fit(x_train, y_train)

    y_pred = model.predict(x_test)
    print("\n" + classification_report(y_test, y_pred, zero_division=0))

    labels = sorted(np.unique(y))
    matrix = confusion_matrix(y_test, y_pred, labels=labels)
    width = max(len(label) for label in labels)
    print("Confusion matrix (rows = truth):")
    print(" " * (width + 2) + " ".join(f"{label[:6]:>7s}" for label in labels))
    for label, row in zip(labels, matrix):
        print(f"{label:>{width}s}  " + " ".join(f"{value:7d}" for value in row))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, args.out)
    print(f"\nSaved model -> {args.out}")


if __name__ == "__main__":
    main()
