"""Fault classification, explainability, and physical-vs-sensor disambiguation.

A RandomForest maps a residual feature vector (see
`app/twin/residual_analysis.ResidualMonitor.feature_vector`) to a fault label. The model
is trained offline — see `app/ml/train/` — and loaded with joblib at runtime.

Three things this module does beyond predicting a label:

**Confidence gating.** If the model is missing, or the top class is below
`min_confidence`, it reports `unknown/monitoring` rather than guessing. For a PHM system a
confidently wrong fault label is worse than an honest "something is off but I cannot name
it" — the anomaly detector has already flagged *that* much.

**Explainability.** Alongside the label it returns the residual features that actually
drove the call, combining the forest's global `feature_importances_` with the magnitude
each feature actually took on this sample. A globally-important feature sitting at zero
did not contribute to *this* prediction, and reporting it as the reason would be
misleading.

**Physical vs sensor disambiguation.** This is the structural insight: a real physical
fault propagates. Bearing wear drops oil pressure *and* raises vibration *and* raises oil
temperature, because those channels are physically coupled through the same mechanism. A
sensor fault does not propagate — a drifting EGT probe moves the EGT channel and leaves
every physically linked channel exactly where the twin predicted. So rather than asking
the classifier to learn the difference from labels alone, we check the *correlation
structure* directly: count how many of a channel's physical neighbours moved with it. One
channel shouting alone is instrumentation; a whole coupled group moving together is
mechanical.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent / "artifacts" / "fault_classifier.joblib"
UNKNOWN_LABEL = "unknown/monitoring"
HEALTHY_LABEL = "healthy"

#: Channels that move together when a *physical* mechanism degrades. Used to decide
#: whether an anomaly propagated (mechanical) or stayed isolated (instrumentation).
PHYSICAL_CHANNEL_GROUPS: dict[str, list[str]] = {
    "oil_pressure_kpa": ["oil_temp_c", "vibration_rms_mean", "vibration_rms_max"],
    "oil_temp_c": ["oil_pressure_kpa", "cht_c"],
    # NB: egt_mean_c and egt_spread_c are deliberately NOT listed as each other's
    # neighbours. They are derived from the same thermocouple readings, so a single
    # corrupted probe moves both — they are mathematically coupled, not physically
    # coupled, and treating one as corroboration for the other would let a lone bad
    # sensor masquerade as a real combustion fault.
    "egt_mean_c": ["cht_c", "afr_mean", "fuel_flow_lph", "torque_brake_nm"],
    "egt_spread_c": ["crest_factor_max", "vibration_rms_max", "cht_c"],
    "cht_c": ["egt_mean_c", "oil_temp_c"],
    "rpm": ["torque_brake_nm", "manifold_pressure_kpa", "boost_pressure_kpa", "fuel_flow_lph"],
    "boost_pressure_kpa": ["manifold_pressure_kpa", "rpm", "egt_mean_c"],
    "manifold_pressure_kpa": ["boost_pressure_kpa", "rpm"],
    "vibration_rms_max": ["vibration_rms_mean", "crest_factor_max", "oil_pressure_kpa"],
    "vibration_rms_mean": ["vibration_rms_max", "oil_pressure_kpa"],
    "fuel_flow_lph": ["afr_mean", "egt_mean_c", "rpm"],
    "afr_mean": ["fuel_flow_lph", "egt_mean_c"],
    "battery_voltage_v": ["alternator_output_v", "rpm"],
    "alternator_output_v": ["battery_voltage_v", "rpm"],
}

#: z above which a channel counts as "moved".
CHANNEL_MOVED_Z = 3.0
#: A lone channel moving this far with no neighbours is a strong sensor-fault signal.
ISOLATED_STRONG_Z = 6.0


@dataclass
class ExplanationFeature:
    feature: str
    importance: float
    residual_value: float

    def to_dict(self) -> dict:
        return {
            "feature": self.feature,
            "importance": round(self.importance, 4),
            "residual_value": round(self.residual_value, 4),
        }


@dataclass
class Diagnosis:
    predicted_fault: str
    confidence: float
    probabilities: dict[str, float]
    model_available: bool
    #: "physical_fault" | "sensor_fault" | "uncertain"
    predicted_source: str = "uncertain"
    explanation: list[ExplanationFeature] = field(default_factory=list)
    #: Human-readable justification for the source call.
    source_rationale: str = ""

    def explanation_dicts(self) -> list[dict]:
        return [e.to_dict() for e in self.explanation]


class FaultClassifier:
    def __init__(
        self,
        model_path: Path = MODEL_PATH,
        min_confidence: float = 0.55,
        top_k: int = 3,
    ) -> None:
        self.model_path = model_path
        self.min_confidence = min_confidence
        self.top_k = top_k
        self._model: Any | None = None
        self._classes: list[str] = []
        self._feature_names: list[str] = []
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

    def set_feature_names(self, names: list[str]) -> None:
        """Register the live feature layout and validate the loaded model against it.

        A model trained before the feature vector changed will raise on every single
        prediction — ten times a second, with a full traceback each time. Detecting the
        mismatch once at startup and disabling the model is far better behaviour: the
        system falls back to anomaly-detection-only and says why, instead of drowning the
        log and burning CPU on exceptions."""
        self._feature_names = names
        if self._model is None:
            return
        expected = getattr(self._model, "n_features_in_", None)
        if expected is not None and expected != len(names):
            logger.warning(
                "Fault classifier at %s expects %d features but the current residual "
                "layout produces %d — the model is stale. Disabling it and running in "
                "anomaly-detection-only mode. Retrain with "
                "`python -m app.ml.train.generate_training_data && "
                "python -m app.ml.train.train_classifier`.",
                self.model_path,
                expected,
                len(names),
            )
            self._model = None
            self._classes = []

    # ---- explainability ---------------------------------------------------

    def _explain(self, features: list[float]) -> list[ExplanationFeature]:
        """Top contributing residual features for this specific prediction.

        Global importance alone is not an explanation of a single call — a feature the
        forest relies on heavily but which is sitting at zero right now contributed
        nothing here. Weighting global importance by the magnitude this sample actually
        presented gives a per-prediction attribution without the cost of a full
        permutation study on every tick."""
        if self._model is None or not self._feature_names:
            return []
        try:
            importances = self._model.feature_importances_
        except Exception:
            return []

        scored: list[tuple[float, str, float]] = []
        for i, name in enumerate(self._feature_names):
            if i >= len(features) or i >= len(importances):
                break
            magnitude = abs(features[i])
            scored.append((float(importances[i]) * magnitude, name, features[i]))

        scored.sort(key=lambda t: -t[0])
        total = sum(s for s, _, _ in scored) or 1.0
        return [
            ExplanationFeature(
                feature=name, importance=score / total, residual_value=value
            )
            for score, name, value in scored[: self.top_k]
            if score > 0.0
        ]

    # ---- physical vs sensor ------------------------------------------------

    def classify_source(
        self, residual_z: dict[str, float]
    ) -> tuple[str, str]:
        """Decide whether an anomaly came from the machine or the instrument.

        Returns (source, rationale)."""
        moved = {
            ch: z for ch, z in residual_z.items() if z >= CHANNEL_MOVED_Z
        }
        if not moved:
            return "uncertain", "no channel has crossed the detection threshold"

        # The loudest channel is the anomaly's epicentre.
        epicentre = max(moved, key=lambda ch: moved[ch])
        epicentre_z = moved[epicentre]

        neighbours = PHYSICAL_CHANNEL_GROUPS.get(epicentre, [])
        if not neighbours:
            return "uncertain", f"no physical coupling defined for {epicentre}"

        corroborating = [ch for ch in neighbours if residual_z.get(ch, 0.0) >= CHANNEL_MOVED_Z]
        corroboration = len(corroborating) / len(neighbours)

        if corroboration >= 0.5:
            return (
                "physical_fault",
                f"{epicentre} moved together with {len(corroborating)} of "
                f"{len(neighbours)} physically linked channels "
                f"({', '.join(corroborating)}) — consistent with a real mechanism",
            )

        if corroboration == 0.0 and epicentre_z >= ISOLATED_STRONG_Z:
            return (
                "sensor_fault",
                f"{epicentre} deviated strongly (z={epicentre_z:.1f}) while every "
                f"physically linked channel ({', '.join(neighbours)}) stayed nominal — "
                f"a real mechanism could not do that",
            )

        if corroboration < 0.5:
            return (
                "uncertain",
                f"{epicentre} moved but only {len(corroborating)} of {len(neighbours)} "
                f"linked channels followed — ambiguous",
            )

        return "uncertain", "insufficient evidence"

    # ---- prediction --------------------------------------------------------

    def predict(
        self,
        features: list[float],
        residual_z: dict[str, float] | None = None,
    ) -> Diagnosis:
        source, rationale = (
            self.classify_source(residual_z) if residual_z else ("uncertain", "")
        )

        if self._model is None:
            return Diagnosis(
                UNKNOWN_LABEL,
                0.0,
                {},
                model_available=False,
                predicted_source=source,
                source_rationale=rationale,
            )

        try:
            proba = self._model.predict_proba([features])[0]
        except Exception:
            logger.exception("Fault classifier inference failed")
            return Diagnosis(
                UNKNOWN_LABEL,
                0.0,
                {},
                model_available=True,
                predicted_source=source,
                source_rationale=rationale,
            )

        probabilities = {str(cls): float(p) for cls, p in zip(self._classes, proba)}
        best_label = max(probabilities, key=lambda k: probabilities[k])
        best_p = probabilities[best_label]

        explanation = self._explain(features)

        if best_p < self.min_confidence and best_label != HEALTHY_LABEL:
            return Diagnosis(
                UNKNOWN_LABEL,
                best_p,
                probabilities,
                model_available=True,
                predicted_source=source,
                explanation=explanation,
                source_rationale=rationale,
            )

        # If the model itself named a sensor fault, that outranks the correlation
        # heuristic — the heuristic is a fallback for when the model is unsure.
        if best_label.endswith("_sensor_drift") or "sensor" in best_label:
            source = "sensor_fault"
            rationale = f"classifier identified {best_label} directly"

        return Diagnosis(
            best_label,
            best_p,
            probabilities,
            model_available=True,
            predicted_source=source,
            explanation=explanation,
            source_rationale=rationale,
        )
