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

**Physical vs sensor disambiguation, two layers.** The structural insight underneath both:
a real physical fault propagates and a sensor fault does not. Bearing wear drops oil
pressure *and* raises vibration *and* raises oil temperature, because those channels are
physically coupled through the same mechanism; a drifting EGT probe moves the EGT channel
and leaves every physically linked channel exactly where the twin predicted.

For CHT, RPM and oil pressure — the three channels `app/fusion/` builds a Kalman fusion
for — that insight now has a direct, quantitative answer instead of an inferred one:
`_classify_fused_channels` reads each channel's fusion innovations straight from
`app/fusion/fusion_monitor.py`. A sensor fault on a fused channel shows up as a large,
sustained innovation isolated to *one* of its two sources while the other tracks the
fused estimate normally; a genuine physical fault shows innovations that are broadly
consistent with the physics instead (real bearing wear degrades the oil-pressure model's
own prediction confidence — its Kalman gain shifting toward the sensor — which is a
different shape of evidence than one sensor lying while the model and the other sensor
agree). This has to run independently of, and before, the correlation check below: fusion
is specifically built to keep the *fused* channel's residual against the twin looking
normal even while one of its two sources is lying, which means waiting for that channel
to become the "loudest moved" residual — the correlation heuristic's own trigger — would
miss precisely the case this layer exists to catch.

Every other channel (EGT, fuel, battery, ...) has no fusion built for it, so
`_classify_correlation` keeps doing exactly what it always has: count how many of a
channel's physical neighbours moved with it. One channel shouting alone is
instrumentation; a whole coupled group moving together is mechanical.
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
    #: Phase 5: which specific probe/instrument the fusion disambiguation named, e.g.
    #: "cht_sensor_secondary" or "rpm_tachometer" — set only when `predicted_source` is
    #: "sensor_fault" *and* the fault landed on one of the three fused channels, where a
    #: specific instrument can actually be named rather than a general subsystem.
    suspect_sensor: str | None = None

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
        #: Cached `feature_importances_`. The attribute is a *property* on a
        #: RandomForest: reading it walks all 100 trees through a joblib dispatch, and at
        #: 10 Hz that costs more than the prediction itself. The forest is fitted and
        #: immutable, so the value cannot change — read it once.
        self._importances: list[float] | None = None
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
            self._importances = None
            # The forest was fitted with n_jobs=-1 and joblib persists that setting, so
            # every single-sample prediction fanned 300 trees across every core through
            # the joblib dispatcher: 26 ms per call for work that takes about one. That is
            # pure scheduling overhead at inference time — we predict one sample, not a
            # batch — and it was being paid ten times a second by the live loop. Forcing
            # single-threaded inference is numerically identical (parallelism only changes
            # who sums the trees) and roughly twenty times faster.
            try:
                self._model.n_jobs = 1
            except Exception:  # pragma: no cover - defensive, some estimators are frozen
                pass
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
            self._importances = None

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
        if self._importances is None:
            try:
                self._importances = [float(v) for v in self._model.feature_importances_]
            except Exception:
                return []
        importances = self._importances

        scored: list[tuple[float, str, float]] = []
        for i, name in enumerate(self._feature_names):
            if i >= len(features) or i >= len(importances):
                break
            magnitude = abs(features[i])
            scored.append((importances[i] * magnitude, name, features[i]))

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
        self,
        residual_z: dict[str, float],
        fusion_z: dict[str, float] | None = None,
    ) -> tuple[str, str, str | None]:
        """Decide whether an anomaly came from the machine or the instrument.

        Checks the three fused channels first (they can hide a sensor fault from their
        own twin-residual entirely — see the module docstring — so their own innovation
        signals have to be consulted directly, not discovered via the correlation
        check below), then falls back to the general correlation heuristic for
        everything else.

        Returns (source, rationale, suspect_sensor). `suspect_sensor` is set only when a
        fused channel named a specific instrument."""
        fused = self._classify_fused_channels(fusion_z or {}, residual_z)
        if fused is not None:
            return fused

        source, rationale = self._classify_correlation(residual_z)
        return source, rationale, None

    def _classify_fused_channels(
        self, fusion_z: dict[str, float], residual_z: dict[str, float]
    ) -> tuple[str, str, str | None] | None:
        """Fusion-innovation disambiguation for CHT, RPM and oil pressure.

        Returns `None` when none of the three fused channels shows anything worth
        calling — the caller then falls through to the correlation heuristic for
        whatever channel actually moved."""
        cht = self._classify_cht(fusion_z)
        if cht is not None:
            return cht
        rpm = self._classify_rpm(fusion_z, residual_z)
        if rpm is not None:
            return rpm
        oil = self._classify_oil_pressure(fusion_z)
        if oil is not None:
            return oil
        return None

    @staticmethod
    def _classify_cht(fusion_z: dict[str, float]) -> tuple[str, str, str | None] | None:
        primary = fusion_z.get("cht_innovation_primary", 0.0)
        secondary = fusion_z.get("cht_innovation_secondary", 0.0)
        primary_moved = primary >= CHANNEL_MOVED_Z
        secondary_moved = secondary >= CHANNEL_MOVED_Z

        if primary_moved and not secondary_moved:
            return (
                "sensor_fault",
                f"CHT primary probe disagrees with the fused estimate (z={primary:.1f}) "
                f"while the secondary probe tracks it normally (z={secondary:.1f}) — a "
                f"real cylinder-head temperature change would move both",
                "cht_sensor_primary",
            )
        if secondary_moved and not primary_moved:
            return (
                "sensor_fault",
                f"CHT secondary probe disagrees with the fused estimate (z={secondary:.1f}) "
                f"while the primary probe tracks it normally (z={primary:.1f}) — a real "
                f"cylinder-head temperature change would move both",
                "cht_sensor_secondary",
            )
        if primary_moved and secondary_moved:
            return (
                "physical_fault",
                f"both CHT probes disagree with the fused estimate together "
                f"(primary z={primary:.1f}, secondary z={secondary:.1f}) — consistent "
                f"with the true cylinder head temperature actually moving",
                None,
            )
        return None

    @staticmethod
    def _classify_rpm(
        fusion_z: dict[str, float], residual_z: dict[str, float]
    ) -> tuple[str, str, str | None] | None:
        tach = fusion_z.get("rpm_innovation_tachometer", 0.0)
        vib = fusion_z.get("rpm_innovation_vibration_derived", 0.0)
        tach_moved = tach >= CHANNEL_MOVED_Z
        vib_moved = vib >= CHANNEL_MOVED_Z
        if not tach_moved and not vib_moved:
            return None

        # Independent corroboration named in the task itself: real vibration RMS,
        # untouched by this fusion pair (it only ever reads a dominant *frequency*, not
        # RMS amplitude), is elevated only when something mechanical is actually
        # happening — a wrong estimate from a bad tachometer would not raise it.
        vib_rms_elevated = (
            residual_z.get("vibration_rms_mean", 0.0) >= CHANNEL_MOVED_Z
            or residual_z.get("vibration_rms_max", 0.0) >= CHANNEL_MOVED_Z
        )

        if tach_moved and not vib_moved:
            return (
                "sensor_fault",
                f"tachometer RPM disagrees with the fused estimate (z={tach:.1f}) while "
                f"the vibration-derived estimate tracks it normally (z={vib:.1f}) — "
                f"consistent with a stuck or corrupted RPM sensor",
                "rpm_tachometer",
            )
        if vib_moved and not tach_moved:
            if vib_rms_elevated:
                return (
                    "physical_fault",
                    f"vibration-derived RPM disagrees with the tachometer (z={vib:.1f}) "
                    f"and cylinder vibration RMS is independently elevated — consistent "
                    f"with a real mechanical issue affecting the vibration spectrum, not "
                    f"a wrong estimate",
                    None,
                )
            return (
                "uncertain",
                f"vibration-derived RPM disagrees with the tachometer (z={vib:.1f}) but "
                f"vibration RMS is not independently elevated — inconclusive",
                None,
            )
        # both moved together
        return (
            "physical_fault",
            f"tachometer and vibration-derived RPM disagree with the fused estimate "
            f"together (tach z={tach:.1f}, vibration z={vib:.1f}) — consistent with the "
            f"true crankshaft speed actually moving",
            None,
        )

    @staticmethod
    def _classify_oil_pressure(
        fusion_z: dict[str, float]
    ) -> tuple[str, str, str | None] | None:
        innovation = fusion_z.get("oil_pressure_innovation", 0.0)
        if innovation < CHANNEL_MOVED_Z:
            return None
        gain_shift = fusion_z.get("oil_pressure_gain_deviation", 0.0)
        if gain_shift >= CHANNEL_MOVED_Z:
            return (
                "physical_fault",
                f"oil pressure disagrees with the zero-wear model prediction "
                f"(z={innovation:.1f}) *and* the Kalman gain has shifted toward "
                f"trusting the sensor (z={gain_shift:.1f}) — the model's own prediction "
                f"confidence degrading is consistent with real lubrication wear",
                None,
            )
        return (
            "sensor_fault",
            f"oil pressure disagrees with the zero-wear model prediction "
            f"(z={innovation:.1f}) while the model's prediction confidence has not "
            f"moved (gain z={gain_shift:.1f}) — consistent with a lying sensor rather "
            f"than real degradation",
            "oil_pressure_sensor",
        )

    def _classify_correlation(
        self, residual_z: dict[str, float]
    ) -> tuple[str, str]:
        """The original Phase 3 heuristic: do physically-linked channels move together?

        Still the only disambiguation available for every channel Part B did not build a
        fusion pair for (EGT, fuel, battery, ...)."""
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
        fusion_z: dict[str, float] | None = None,
    ) -> Diagnosis:
        source, rationale, suspect_sensor = (
            self.classify_source(residual_z, fusion_z)
            if residual_z
            else ("uncertain", "", None)
        )

        if self._model is None:
            return Diagnosis(
                UNKNOWN_LABEL,
                0.0,
                {},
                model_available=False,
                predicted_source=source,
                source_rationale=rationale,
                suspect_sensor=suspect_sensor,
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
                suspect_sensor=suspect_sensor,
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
                suspect_sensor=suspect_sensor,
            )

        # If the model itself named a sensor fault, that outranks the fused-channel and
        # correlation heuristics — both are a fallback for when the model is unsure.
        if best_label.endswith("_sensor_drift") or best_label.endswith(
            "_sensor_noise"
        ) or best_label.endswith("_sensor_stuck") or "sensor" in best_label:
            source = "sensor_fault"
            rationale = f"classifier identified {best_label} directly"
            # The new per-probe classes name their instrument in the label itself
            # (`cht_sensor_primary_drift` / `cht_sensor_secondary_drift`) — surface that
            # the same way the fusion-based heuristic does, rather than leaving
            # `suspect_sensor` at whatever (possibly unrelated) channel the heuristic
            # had guessed before the model's own, more specific answer arrived.
            if best_label.startswith("cht_sensor_"):
                suspect_sensor = best_label.removesuffix("_drift")
            elif best_label == "rpm_sensor_stuck":
                suspect_sensor = "rpm_tachometer"
            elif best_label == "oil_pressure_sensor_noise":
                suspect_sensor = "oil_pressure_sensor"

        return Diagnosis(
            best_label,
            best_p,
            probabilities,
            model_available=True,
            predicted_source=source,
            explanation=explanation,
            source_rationale=rationale,
            suspect_sensor=suspect_sensor,
        )
