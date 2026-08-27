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
