"""Part D item 12 — repeated group-wise CV so every episode is tested at least once, plus
the physical-fault vs sensor-fault confusion the project's key claim rests on."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import GroupKFold

from app.physics.sensor_fault_model import SENSOR_FAULT_TYPES

DATA = Path(__file__).parent.parent / "app/ml/train/training_data.npz"

blob = np.load(DATA, allow_pickle=True)
X, y, groups = blob["X"], blob["y"], blob["episode"]
print(f"{X.shape[0]} samples x {X.shape[1]} features, {len(np.unique(y))} classes, "
      f"{len(np.unique(groups))} episodes")

N_SPLITS = 6
gkf = GroupKFold(n_splits=N_SPLITS)
y_true_all, y_pred_all = [], []
fold_acc = []
for k, (tr, te) in enumerate(gkf.split(X, y, groups)):
    m = RandomForestClassifier(n_estimators=300, min_samples_leaf=2,
                               class_weight="balanced_subsample", n_jobs=-1,
                               random_state=0)
    m.fit(X[tr], y[tr])
    p = m.predict(X[te])
    acc = float((p == y[te]).mean())
    fold_acc.append(acc)
    y_true_all.append(y[te]); y_pred_all.append(p)
    print(f"  fold {k+1}/{N_SPLITS}: {len(np.unique(groups[te])):3d} held-out episodes, "
          f"{len(te):5d} samples, accuracy {acc:.4f}", flush=True)

yt = np.concatenate(y_true_all); yp = np.concatenate(y_pred_all)
print(f"\nGroupKFold accuracy: mean {np.mean(fold_acc):.4f}  "
      f"min {min(fold_acc):.4f}  max {max(fold_acc):.4f}")
print(f"Pooled accuracy over all {len(yt)} out-of-fold samples: {(yt==yp).mean():.4f}\n")

print(classification_report(yt, yp, zero_division=0, digits=3))

labels = sorted(np.unique(y))
cm = confusion_matrix(yt, yp, labels=labels)
w = max(len(l) for l in labels)
print("Confusion matrix, pooled out-of-fold (rows = truth):")
print(" " * (w + 2) + " ".join(f"{l[:6]:>7s}" for l in labels))
for l, row in zip(labels, cm):
    print(f"{l:>{w}s}  " + " ".join(f"{v:7d}" for v in row))

# ---- the claim that matters: physical vs sensor -----------------------------
SENSOR = set(SENSOR_FAULT_TYPES)
def cat(v):
    if v == "healthy": return "healthy"
    return "sensor" if v in SENSOR else "physical"

ct, cp = np.array([cat(v) for v in yt]), np.array([cat(v) for v in yp])
cats = ["healthy", "physical", "sensor"]
print("\n\nPhysical-vs-sensor confusion (the distinction the digital twin exists to make):")
print(f"{'truth \\\\ predicted':>22}" + "".join(f"{c:>12}" for c in cats) + f"{'row total':>12}")
for a in cats:
    row = [int(((ct == a) & (cp == b)).sum()) for b in cats]
    print(f"{a:>22}" + "".join(f"{v:>12d}" for v in row) + f"{sum(row):>12d}")

p2s = int(((ct == "physical") & (cp == "sensor")).sum()); npz_ = int((ct == "physical").sum())
s2p = int(((ct == "sensor") & (cp == "physical")).sum()); ns = int((ct == "sensor").sum())
print(f"\n  physical fault called a SENSOR fault : {p2s}/{npz_} = {100*p2s/max(1,npz_):.3f}%")
print(f"  sensor fault called a PHYSICAL fault : {s2p}/{ns} = {100*s2p/max(1,ns):.3f}%")

# ---- worst confusions --------------------------------------------------------
print("\nLargest off-diagonal confusions:")
pairs = []
for i, a in enumerate(labels):
    for j, b in enumerate(labels):
        if i != j and cm[i][j] > 0:
            pairs.append((cm[i][j], a, b, cm[i].sum()))
for n, a, b, tot in sorted(pairs, reverse=True)[:8]:
    print(f"  {a} -> {b}: {n}/{tot} ({100*n/tot:.1f}% of that class's samples)")
