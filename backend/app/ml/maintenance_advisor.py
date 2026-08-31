"""Turns health numbers into actions a ground crew can take.

Deliberately rule-based. A health score of 34 does not tell a technician what to do; a
line saying "lubrication degraded, RUL 18 min — land within 15 minutes and inspect main
bearings before next sortie" does. The rules are transparent and auditable, which for a
maintenance recommendation is worth more than a model nobody can question. Every advisory
carries its `basis` — the specific signals that drove it — so the crew can check the
reasoning rather than trusting it blindly.

Urgency ladder:
    immediate      — RUL short, or a subsystem already critical. Land / abort.
    schedule_soon  — degrading but with time in hand. Book maintenance before next sortie.
    monitor        — early indication only. Watch it.

---

## Early warning: a specific action for a fluctuation, not a fault

`generate_early_warning` is a distinct, additive tier ahead of `evaluate()` above — it is
called once per subsystem currently gated `"emerging"`/`"building"` by
`app.twin.residual_analysis.PreAlertMonitor`, and produces exactly one instruction, not a
ranked list. `evaluate()`'s own `Advisory` objects are unchanged by anything in this
section: an early warning and a regular advisory can both be present on the same frame for
the same subsystem (the pre-alert layer typically fires before HI has moved far enough for
`evaluate()`'s own `HI_WATCH` cut to notice it), and neither suppresses the other.

The action text is deliberately concrete and subsystem-specific — "reduce throttle",
"avoid further RPM increases" — rather than a generic "monitor closely", because the whole
point of this tier is that there is still time to act on it.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Human-readable names and the maintenance action implied by each subsystem.
SUBSYSTEM_ACTIONS: dict[str, str] = {
    "cylinder": "inspect cylinders, plugs and injectors; run a compression check",
    "lubrication": "inspect main and rod bearings, oil pump and oil condition",
    "cooling": "check cooling ducts, baffles and coolant flow for obstruction",
    "fuel": "inspect fuel injectors, filter and delivery pressure",
    "turbo": "inspect turbocharger, wastegate linkage and intake air filter",
    "electrical": "test alternator output, regulator and battery internal resistance",
}

HI_CRITICAL = 40.0
HI_DEGRADED = 70.0
HI_WATCH = 88.0

RUL_IMMEDIATE_MIN = 30.0
RUL_SOON_MIN = 120.0


@dataclass
class Advisory:
    subsystem: str
    urgency: str  # "monitor" | "schedule_soon" | "immediate"
    recommendation: str
    basis: list[str]

    def to_dict(self) -> dict:
        return {
            "subsystem": self.subsystem,
            "urgency": self.urgency,
            "recommendation": self.recommendation,
            "basis": self.basis,
        }


_URGENCY_RANK = {"monitor": 0, "schedule_soon": 1, "immediate": 2}

#: Concrete, in-flight actions for a subsystem showing an early fluctuation — distinct
#: from `SUBSYSTEM_ACTIONS` above, which is post-flight ground-crew inspection language.
#: These are things an operator can do *right now*, in the air, while there is still time.
EARLY_WARNING_ACTIONS: dict[str, str] = {
    "cylinder": "Ease off throttle and avoid rapid transients to reduce combustion loading.",
    "lubrication": "Monitor oil pressure closely; avoid further RPM increases.",
    "cooling": "Reduce throttle to roughly 70% to slow thermal buildup.",
    "fuel": "Avoid aggressive throttle transients; watch EGT spread for a developing lean condition.",
    "turbo": "Ease off high-boost demand; avoid sustained full-throttle climbs.",
    "electrical": "Shed non-essential electrical load and monitor bus voltage.",
}

#: Below this many minutes-to-warning-threshold, the recommendation escalates to
#: suggesting an early return rather than just the subsystem-specific mitigation.
EARLY_WARNING_RTB_MIN = 20.0


@dataclass
class EarlyWarning:
    """A NEW, earlier tier in front of `Advisory`/the hard fault-alert threshold — see the
    module docstring. `predicted_minutes` is `None` whenever the underlying trend fit does
    not have enough consistent samples yet (pre-alert's own gate can trip on fewer samples
    than `RULPredictor.min_samples` requires) — `confidence` communicates that state
    honestly rather than a stale or fabricated number filling the gap."""

    subsystem: str
    pre_alert_state: str  # "emerging" | "building"
    predicted_minutes: float | None
    confidence: str  # "low" | "medium" | "high"
    recommended_action: str
    basis: list[str]

    def to_dict(self) -> dict:
        return {
            "subsystem": self.subsystem,
            "pre_alert_state": self.pre_alert_state,
            "predicted_minutes": self.predicted_minutes,
            "confidence": self.confidence,
            "recommended_action": self.recommended_action,
            "basis": self.basis,
        }


class MaintenanceAdvisor:
    def __init__(self, max_advisories: int = 4) -> None:
        self.max_advisories = max_advisories

    def evaluate(
        self,
        health_indicators: dict[str, float],
        rul_minutes: float | None,
        rul_subsystem: str | None,
        active_faults: dict[str, float],
        efficiency_trend: str = "stable",
        combustion_instability_pct: float | None = None,
        predicted_source: str | None = None,
        battery_voltage_v: float | None = None,
    ) -> list[Advisory]:
        advisories: list[Advisory] = []

        # ---- 1. per-subsystem health --------------------------------------
        for subsystem, hi in sorted(health_indicators.items(), key=lambda kv: kv[1]):
            if hi >= HI_WATCH:
                continue

            basis = [f"{subsystem} health index {hi:.0f}/100"]
            action = SUBSYSTEM_ACTIONS.get(subsystem, f"inspect {subsystem}")

            # RUL only escalates the subsystem it actually belongs to.
            subsystem_rul = rul_minutes if rul_subsystem == subsystem else None
            if subsystem_rul is not None:
                basis.append(f"estimated RUL {subsystem_rul:.0f} min")

            if hi < HI_CRITICAL or (
                subsystem_rul is not None and subsystem_rul < RUL_IMMEDIATE_MIN
            ):
                urgency = "immediate"
                recommendation = (
                    f"{subsystem.capitalize()} critical — abort or land as soon as "
                    f"practicable, then {action}."
                )
            elif hi < HI_DEGRADED or (
                subsystem_rul is not None and subsystem_rul < RUL_SOON_MIN
            ):
                urgency = "schedule_soon"
                recommendation = (
                    f"{subsystem.capitalize()} degraded — complete the sortie with "
                    f"margin, then {action} before the next flight."
                )
            else:
                urgency = "monitor"
                recommendation = (
                    f"{subsystem.capitalize()} showing early deviation — monitor; "
                    f"{action} at the next scheduled service."
                )

            advisories.append(Advisory(subsystem, urgency, recommendation, basis))

        # ---- 2. sensor-fault caveat ---------------------------------------
        # If the classifier believes the anomaly is instrumentation, say so — sending a
        # crew to pull a healthy engine apart is its own kind of failure.
        if predicted_source == "sensor_fault":
            advisories.insert(
                0,
                Advisory(
                    subsystem="instrumentation",
                    urgency="schedule_soon",
                    recommendation=(
                        "Anomaly is isolated to a single channel with no correlated "
                        "movement in physically linked signals — likely a sensor or "
                        "wiring fault, not engine damage. Verify the transducer and "
                        "harness before any engine teardown."
                    ),
                    basis=["residual confined to one channel", "no correlated residuals"],
                ),
            )

        # ---- 3. leading indicators ----------------------------------------
        # These fire while the health indices may still look acceptable — that is the
        # entire value of tracking them.
        if combustion_instability_pct is not None and combustion_instability_pct > 6.0:
            urgency = "immediate" if combustion_instability_pct > 12.0 else "schedule_soon"
            advisories.append(
                Advisory(
                    subsystem="cylinder",
                    urgency=urgency,
                    recommendation=(
                        "Cycle-to-cycle combustion variability is elevated, which "
                        "precedes hard misfire — inspect ignition and injection before "
                        "the fault fully develops."
                    ),
                    basis=[f"COV(IMEP) {combustion_instability_pct:.1f}%"],
                )
            )

        if efficiency_trend == "degrading":
            advisories.append(
                Advisory(
                    subsystem="fuel",
                    urgency="monitor",
                    recommendation=(
                        "Brake specific fuel consumption is trending worse over this "
                        "mission — the engine is doing the same work for more fuel. "
                        "Review injector condition, boost delivery and injection timing."
                    ),
                    basis=["BSFC trend degrading"],
                )
            )

        if battery_voltage_v is not None and battery_voltage_v < 12.0:
            advisories.append(
                Advisory(
                    subsystem="electrical",
                    urgency="immediate" if battery_voltage_v < 11.2 else "schedule_soon",
                    recommendation=(
                        "Bus voltage is below the healthy band with the engine running — "
                        "the alternator is not sustaining the load. "
                        + SUBSYSTEM_ACTIONS["electrical"].capitalize() + "."
                    ),
                    basis=[f"battery voltage {battery_voltage_v:.1f} V"],
                )
            )

        # ---- 4. rank and de-duplicate --------------------------------------
        # Most urgent first; keep only the worst advisory per subsystem so the panel
        # stays readable rather than repeating the same subsystem three ways.
        advisories.sort(key=lambda a: -_URGENCY_RANK[a.urgency])
        seen: set[str] = set()
        deduped: list[Advisory] = []
        for advisory in advisories:
            if advisory.subsystem in seen:
                continue
            seen.add(advisory.subsystem)
            deduped.append(advisory)

        return deduped[: self.max_advisories]

    def generate_early_warning(
        self,
        subsystem: str,
        pre_alert_state: str,
        predicted_minutes_to_warning_threshold: float | None,
        confidence: str,
    ) -> EarlyWarning:
        """One concrete instruction for a subsystem currently gated into pre-alert.

        `predicted_minutes_to_warning_threshold` and `confidence` are not folded into the
        text of `recommended_action` — the frontend renders those two fields separately
        (a distinct "~N min" / "confidence: low" readout) so the instruction itself stays
        a single, short, actionable sentence regardless of whether a numeric ETA exists
        yet.
        """
        base_action = EARLY_WARNING_ACTIONS.get(
            subsystem, f"Monitor {subsystem} closely; avoid aggressive power changes."
        )

        basis = [f"{subsystem} pre-alert: {pre_alert_state}", f"confidence: {confidence}"]
        if predicted_minutes_to_warning_threshold is not None:
            basis.append(
                f"trend-projected {predicted_minutes_to_warning_threshold:.0f} min to "
                "warning threshold"
            )
        else:
            basis.append("trend not yet fittable — too few consistent samples")

        if (
            predicted_minutes_to_warning_threshold is not None
            and predicted_minutes_to_warning_threshold <= EARLY_WARNING_RTB_MIN
        ):
            recommended_action = (
                f"{base_action} Consider early RTB within the next "
                f"{predicted_minutes_to_warning_threshold:.0f} minutes."
            )
        else:
            recommended_action = base_action

        return EarlyWarning(
            subsystem=subsystem,
            pre_alert_state=pre_alert_state,
            predicted_minutes=predicted_minutes_to_warning_threshold,
            confidence=confidence,
            recommended_action=recommended_action,
            basis=basis,
        )
