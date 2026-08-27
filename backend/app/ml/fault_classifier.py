"""Fault classification from twin-residual features.

A scikit-learn RandomForest maps a residual feature vector (see
app/twin/residual_analysis.ResidualMonitor.feature_vector) to a predicted fault type.
The model is trained offline against simulated runs — see app/ml/train/ — and loaded
from disk with joblib at runtime.

If the model file is missing, or the top-class probability is below `min_confidence`, the
classifier reports `unknown/monitoring` rather than guessing. That matters for a PHM
system: a confidently wrong fault label is worse than an honest "something is off but I
cannot name it yet", which the anomaly detector has already flagged anyway.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent / "artifacts" / "fault_classifier.joblib"
UNKNOWN_LABEL = "unknown/monitoring"
HEALTHY_LABEL = "healthy"


@dataclass
class Diagnosis:
    predicted_fault: str
    confidence: float
    probabilities: dict[str, float]
    model_available: bool


class FaultClassifier:
    def __init__(
        self,
        model_path: Path = MODEL_PATH,
        min_confidence: float = 0.55,
    ) -> None:
        self.model_path = model_path
        self.min_confidence = min_confidence
        self._model: Any | None = None
        self._classes: list[str] = []
        self._load()

    def _load(self) -> None:
        if not self.model_path.exists():
            logger.warning(
                "Fault classifier model not found at %s — running in "
                "anomaly-detection-only mode. Train it with "
                "`python -m app.ml.train.train_classifier`.",
                self.model_path,
            )
            return
        try:
            import joblib

            self._model = joblib.load(self.model_path)
            self._classes = list(self._model.classes_)
            logger.info(
                "Loaded fault classifier (%d classes) from %s",
                len(self._classes),
                self.model_path,
            )
        except Exception:
            logger.exception("Failed to load fault classifier from %s", self.model_path)
            self._model = None

    @property
    def available(self) -> bool:
        return self._model is not None

    def predict(self, features: list[float]) -> Diagnosis:
        if self._model is None:
            return Diagnosis(UNKNOWN_LABEL, 0.0, {}, model_available=False)

        try:
            proba = self._model.predict_proba([features])[0]
        except Exception:
            logger.exception("Fault classifier inference failed")
            return Diagnosis(UNKNOWN_LABEL, 0.0, {}, model_available=True)

        probabilities = {
            str(cls): float(p) for cls, p in zip(self._classes, proba)
        }
        best_label = max(probabilities, key=lambda k: probabilities[k])
        best_p = probabilities[best_label]

        if best_p < self.min_confidence and best_label != HEALTHY_LABEL:
            return Diagnosis(UNKNOWN_LABEL, best_p, probabilities, model_available=True)

        return Diagnosis(best_label, best_p, probabilities, model_available=True)
