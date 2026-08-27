"""Post-mission debrief.

Turns a recorded mission into the summary a squadron actually wants after landing: what
was flown, what went wrong, when, how the engine ended up, and what it means in plain
language. The numeric sections are for the maintenance log; the `debrief` paragraph is
written to be read aloud in a briefing.

Everything here is computed from the stored frames, so a report can be regenerated for any
past mission without re-flying it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SUBSYSTEMS = ("cylinder", "lubrication", "cooling", "fuel", "turbo")


def _fmt_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _mean(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def build_mission_report(
    mission: dict[str, Any],
    frames: list[dict[str, Any]],
    fault_events: list[dict[str, Any]],
) -> dict[str, Any]:
    if not frames:
        return {
            "mission_id": mission.get("id"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "error": "no telemetry frames were recorded for this mission",
        }

    first, last = frames[0], frames[-1]
    duration_s = float(last.get("timestamp", 0)) - float(first.get("timestamp", 0))

    # ---- phases flown, in order, with time in each -------------------------
    phase_seconds: dict[str, float] = {}
    phase_order: list[str] = []
    prev_ts = float(first.get("timestamp", 0))
    for frame in frames:
        phase = frame.get("mission_phase", "unknown")
        ts = float(frame.get("timestamp", prev_ts))
        phase_seconds[phase] = phase_seconds.get(phase, 0.0) + max(0.0, ts - prev_ts)
        if not phase_order or phase_order[-1] != phase:
            phase_order.append(phase)
        prev_ts = ts

    # ---- health trend -------------------------------------------------------
    health_series = [f.get("health", {}).get("overall_score") for f in frames]
    health_clean = [h for h in health_series if h is not None]
    start_health = health_clean[0] if health_clean else None
    end_health = health_clean[-1] if health_clean else None
    min_health = min(health_clean) if health_clean else None

    if start_health is not None and end_health is not None:
        delta = end_health - start_health
        # A mission that started and finished healthy but collapsed in the middle is NOT
        # "stable" — that is exactly the mission a maintainer most needs told about.
        # Comparing only the endpoints hides the entire event.
        dipped = min_health is not None and min_health < min(start_health, end_health) - 15
        if delta < -5:
            health_trend = "degraded"
        elif dipped:
            health_trend = "recovered"
        elif delta > 5:
            health_trend = "recovered"
        else:
            health_trend = "stable"
    else:
        health_trend = "unknown"

    final_subsystems = last.get("health", {}).get("subsystem_scores", {})
    worst_subsystem = (
        min(final_subsystems, key=lambda k: final_subsystems[k])
        if final_subsystems
        else None
    )

    # ---- efficiency ---------------------------------------------------------
    avg_bsfc = _mean([f.get("bsfc_g_per_kwh") for f in frames])
    avg_fuel_lph = _mean([f.get("fuel_flow_lph") for f in frames])
    fuel_used_l = (avg_fuel_lph * duration_s / 3600.0) if avg_fuel_lph else None

    # ---- reliability --------------------------------------------------------
    recommendations = [
        f.get("mission_reliability", {}).get("recommendation") for f in frames
    ]
    time_not_go = sum(1 for r in recommendations if r and r != "GO") / max(1, len(frames))

    # ---- fault events -------------------------------------------------------
    events: list[dict[str, Any]] = []
    base_ts = float(first.get("timestamp", 0))
    for event in fault_events:
        started = float(event.get("started_at") or base_ts)
        cleared = event.get("cleared_at")
        events.append(
            {
                "fault_type": event.get("fault_type"),
                "severity": event.get("severity"),
                "is_sensor_fault": event.get("is_sensor_fault", False),
                "predicted_source": event.get("predicted_source"),
                "t_plus_s": round(started - base_ts, 1),
                "t_plus": _fmt_duration(started - base_ts),
                "cleared_t_plus": (
                    _fmt_duration(float(cleared) - base_ts) if cleared else None
                ),
                "resolved": cleared is not None,
                "classifier_explanation": event.get("classifier_explanation"),
            }
        )

    report: dict[str, Any] = {
        "mission_id": mission.get("id"),
        "profile_name": mission.get("mission_profile_name"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_s": round(duration_s, 1),
        "duration": _fmt_duration(duration_s),
        "frame_count": len(frames),
        "phases_flown": phase_order,
        "time_per_phase": {k: round(v, 1) for k, v in phase_seconds.items()},
        "health": {
            "start": start_health,
            "end": end_health,
            "minimum": min_health,
            "trend": health_trend,
            "final_subsystem_scores": final_subsystems,
            "worst_subsystem": worst_subsystem,
        },
        "final_rul_minutes": last.get("rul_minutes"),
        "final_recommendation": last.get("mission_reliability", {}).get("recommendation"),
        "fraction_of_mission_not_go": round(time_not_go, 3),
        "efficiency": {
            "average_bsfc_g_per_kwh": round(avg_bsfc, 1) if avg_bsfc else None,
            "average_fuel_flow_lph": round(avg_fuel_lph, 2) if avg_fuel_lph else None,
            "estimated_fuel_used_l": round(fuel_used_l, 2) if fuel_used_l else None,
            "final_trend": last.get("efficiency_trend"),
        },
        "fault_events": events,
        "final_advisories": last.get("maintenance_advisories", []),
    }

    report["debrief"] = _write_debrief(report)
    return report


def _write_debrief(r: dict[str, Any]) -> str:
    """Plain-language summary. Written to be read aloud at a debrief, not parsed."""
    parts: list[str] = []

    phases = ", ".join(r["phases_flown"]) if r["phases_flown"] else "no phases recorded"
    parts.append(
        f"Mission {r['mission_id']} ran for {r['duration']} across {phases}."
    )

    health = r["health"]
    if health["end"] is not None:
        if health["trend"] == "stable" and (health["minimum"] or 100) > 90:
            parts.append(
                f"The engine stayed healthy throughout, ending at "
                f"{health['end']:.0f}/100 with no sustained degradation."
            )
        elif health["trend"] == "degraded":
            parts.append(
                f"Engine health fell from {health['start']:.0f} to {health['end']:.0f} "
                f"out of 100, bottoming out at {health['minimum']:.0f}. The worst-"
                f"affected subsystem was {health['worst_subsystem']} "
                f"({health['final_subsystem_scores'].get(health['worst_subsystem'], 0):.0f}/100)."
            )
        elif health["trend"] == "recovered":
            parts.append(
                f"Health dipped to {health['minimum']:.0f}/100 during the mission before "
                f"recovering to {health['end']:.0f}, consistent with a fault that was "
                f"cleared in flight. The excursion still warrants inspection — the engine "
                f"spent part of the sortie outside its healthy envelope even though it "
                f"finished nominal."
            )

    events = r["fault_events"]
    if not events:
        parts.append("No fault events were recorded.")
    else:
        real = [e for e in events if not e.get("is_sensor_fault")]
        sensor = [e for e in events if e.get("is_sensor_fault")]
        described = ", ".join(
            f"{e['fault_type']} at T+{e['t_plus']}"
            + ("" if e["resolved"] else " (unresolved)")
            for e in events[:4]
        )
        parts.append(f"{len(events)} fault event(s) were logged: {described}.")
        if sensor and not real:
            parts.append(
                "All logged events were instrumentation faults — the engine itself was "
                "not degraded, and no teardown is warranted on this evidence."
            )
        elif sensor:
            parts.append(
                f"{len(sensor)} of these were instrumentation rather than engine faults; "
                f"verify those transducers before acting on the affected channels."
            )

    if r["final_recommendation"] and r["final_recommendation"] != "GO":
        parts.append(
            f"The mission ended in a {r['final_recommendation']} state"
            + (
                f", with an estimated {r['final_rul_minutes']:.0f} minutes of useful life "
                f"remaining on the limiting subsystem."
                if r["final_rul_minutes"] is not None
                else "."
            )
        )
        if r["fraction_of_mission_not_go"] > 0.25:
            parts.append(
                f"Reliability was below GO for "
                f"{r['fraction_of_mission_not_go'] * 100:.0f}% of the flight, which "
                f"should be reviewed against the mission's dispatch criteria."
            )
    else:
        parts.append("Mission reliability remained in the GO band at completion.")

    eff = r["efficiency"]
    if eff["average_bsfc_g_per_kwh"]:
        line = (
            f"Average brake specific fuel consumption was "
            f"{eff['average_bsfc_g_per_kwh']:.0f} g/kWh"
        )
        if eff["estimated_fuel_used_l"]:
            line += f" over roughly {eff['estimated_fuel_used_l']:.1f} L of fuel"
        if eff["final_trend"] == "degrading":
            line += ", and the trend was worsening by the end of the mission"
        parts.append(line + ".")

    advisories = r.get("final_advisories") or []
    if advisories:
        top = advisories[0]
        parts.append(
            f"Outstanding maintenance action ({top.get('urgency')}): "
            f"{top.get('recommendation')}"
        )

    return " ".join(parts)
