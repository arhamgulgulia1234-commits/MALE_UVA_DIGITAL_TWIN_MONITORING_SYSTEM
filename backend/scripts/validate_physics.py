"""Headless physics validation — no WebSocket, no frontend.

Flies a simulated 10-minute mission, injects one fault at the 3-minute mark, and writes
matplotlib plots of every signal that matters to backend/scripts/output/ so the physics
and fault signatures can be eyeballed before anyone looks at the dashboard.

Plots produced:
    01_engine_core.png     RPM, MAP / boost, power, fuel flow
    02_temperatures.png    per-cylinder EGT, CHT, oil temperature
    03_lubrication.png     oil pressure and oil temperature
    04_vibration.png       per-cylinder vibration RMS + crest factor
    05_health.png          overall + per-subsystem health indicators
    06_prognostics.png     RUL and mission reliability
    07_residuals.png       real vs healthy-twin residuals for key channels

`--scenario throttle-transient` and `--scenario fusion` are separate modes with their
own plots (08_throttle_transient.png, 09_sensor_fusion.png) — see `run_fusion_validation`
for what the latter proves: an isolated secondary-CHT-probe fault, with the fused
estimate staying close to the true simulated CHT while the classifier names that specific
probe rather than the engine's cooling subsystem.

Usage:
    python -m scripts.validate_physics [--fault bearing_wear] [--minutes 10]
    python -m scripts.validate_physics --scenario fusion
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.physics.fault_models import FAULT_TYPES  # noqa: E402
from app.sim.simulation_loop import SimulationLoop  # noqa: E402

OUTPUT_DIR = Path(__file__).parent / "output"
TICK_S = 0.1

# Consistent colour language across every plot.
C_PRIMARY = "#0072B2"
C_ACCENT = "#D55E00"
C_THIRD = "#009E73"
C_FOURTH = "#CC79A7"
C_FAULT = "#B00020"
CYL_COLORS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#56B4E9", "#E69F00"]


def style(ax, title: str, ylabel: str, fault_at: float | None) -> None:
    ax.set_title(title, fontsize=11, loc="left", fontweight="600")
    ax.set_ylabel(ylabel, fontsize=9)
    ax.grid(alpha=0.25, linewidth=0.6)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(labelsize=8)
    if fault_at is not None:
        ax.axvline(fault_at, color=C_FAULT, linestyle="--", linewidth=1.2, alpha=0.8)


def run(fault_type: str, minutes: float, inject_at_min: float, severity: float,
        ramp_s: float, time_scale: float) -> dict:
    sim = SimulationLoop()
    sim.set_time_scale(time_scale)

    total_sim_s = minutes * 60.0
    inject_at_s = inject_at_min * 60.0
    ticks = int(total_sim_s / (TICK_S * time_scale))
    inject_tick = int(inject_at_s / (TICK_S * time_scale))

    rec: dict[str, list] = {k: [] for k in (
        "t", "rpm", "map", "boost", "power", "fuel", "cht", "oil_t", "oil_p",
        "airspeed", "altitude", "overall", "rul", "reliability", "phase", "severity",
    )}
    rec["egt"] = []
    rec["vib"] = []
    rec["crest"] = []
    rec["subs"] = []
    rec["resid"] = []

    injected = False
    for i in range(ticks):
        if i == inject_tick:
            sim.inject_fault(fault_type, severity, ramp_s)
            injected = True
        frame = sim.tick(TICK_S)
        state = sim.plant.state

        rec["t"].append(sim.sim_time_s / 60.0)
        rec["rpm"].append(frame.rpm)
        rec["map"].append(frame.manifold_pressure_kpa)
        rec["boost"].append(frame.boost_pressure_kpa)
        rec["power"].append(state.power_brake_kw)
        rec["fuel"].append(frame.fuel_flow_lph)
        rec["cht"].append(frame.cht_c)
        rec["oil_t"].append(frame.oil_temp_c)
        rec["oil_p"].append(frame.oil_pressure_kpa)
        rec["airspeed"].append(frame.airspeed_ms)
        rec["altitude"].append(frame.altitude_m)
        rec["overall"].append(frame.health.overall_score)
        rec["rul"].append(frame.rul_minutes)
        rec["reliability"].append(frame.mission_reliability.score)
        rec["phase"].append(frame.mission_phase)
        rec["severity"].append(sim.faults.severity(fault_type))
        rec["egt"].append([c.egt_c for c in frame.cylinders])
        rec["vib"].append([c.vibration_rms for c in frame.cylinders])
        rec["crest"].append([f.crest_factor for f in state.vibration_features])
        rec["subs"].append(dict(frame.health.subsystem_scores))
        rec["resid"].append(dict(sim.diagnostics.residual_mean))

    rec["_inject_min"] = inject_at_min if injected else None
    rec["_diagnosis"] = sim.diagnostics
    return rec


def run_throttle_transient(
    hold_s: float = 40.0,
    step_s: float = 25.0,
    low: float = 0.20,
    high: float = 1.00,
) -> dict:
    """Step the throttle 20% -> 100% -> 20% and record the response.

    This is the sharpest test of whether the dynamics are actually dynamics. A model that
    computes steady-state values and calls them physics will jump instantly; a real engine
    cannot. What we are checking for:

      * **Turbo lag** — boost must trail the throttle step by roughly `turbo_tau_s`,
        which in turn delays manifold pressure and torque.
      * **Rotational inertia** — RPM must ramp, not step, as the crank accelerates against
        the propeller load.
      * **Thermal lag** — EGT follows within seconds (small gas mass, fast probe), CHT
        over tens of seconds (26 kJ/K of aluminium), oil slower still. The *ordering* of
        those time constants is the thing to verify.
      * **Stability** — no oscillation growth, no divergence. An unstable integrator
        shows up here first, because a step excites every mode at once.

    Run at 1x so the sub-stepping is doing real work rather than being smeared by time
    acceleration.
    """
    sim = SimulationLoop()
    sim.set_time_scale(1.0)
    # Hold a steady phase so the transient is not confounded by the mission profile
    # changing altitude and airspeed underneath it.
    sim.jump_phase("cruise")

    schedule = [(hold_s, low), (step_s, high), (hold_s, low)]

    rec: dict[str, list] = {k: [] for k in (
        "t", "throttle", "rpm", "map", "boost", "power", "fuel",
        "cht", "oil_t", "oil_p", "egt_mean", "cov",
    )}

    t_elapsed = 0.0
    for duration, throttle in schedule:
        sim.set_throttle(throttle)
        for _ in range(int(duration / TICK_S)):
            frame = sim.tick(TICK_S)
            state = sim.plant.state
            t_elapsed += TICK_S
            rec["t"].append(t_elapsed)
            rec["throttle"].append(throttle)
            rec["rpm"].append(frame.rpm)
            rec["map"].append(frame.manifold_pressure_kpa)
            rec["boost"].append(frame.boost_pressure_kpa)
            rec["power"].append(state.power_brake_kw)
            rec["fuel"].append(frame.fuel_flow_lph)
            rec["cht"].append(frame.cht_c)
            rec["oil_t"].append(frame.oil_temp_c)
            rec["oil_p"].append(frame.oil_pressure_kpa)
            rec["egt_mean"].append(state.egt_mean_c)
            rec["cov"].append(frame.combustion_instability_pct)

    rec["_step_up_s"] = hold_s
    rec["_step_down_s"] = hold_s + step_s
    return rec


def _rise_time(times: list[float], values: list[float], start_s: float,
               end_s: float, fraction: float = 0.632) -> float | None:
    """Time for a signal to cover `fraction` of its excursion after a step.

    0.632 is one time constant for a first-order system, so this reads directly as tau."""
    window = [(t, v) for t, v in zip(times, values) if start_s <= t <= end_s]
    if len(window) < 5:
        return None
    v0 = window[0][1]
    v_end = max(v for _, v in window) if window[-1][1] > v0 else min(v for _, v in window)
    span = v_end - v0
    if abs(span) < 1e-6:
        return None
    target = v0 + span * fraction
    for t, v in window:
        if (span > 0 and v >= target) or (span < 0 and v <= target):
            return t - start_s
    return None


def _report_transient(rec: dict) -> None:
    t = rec["t"]
    up = rec["_step_up_s"]
    down = rec["_step_down_s"]

    print("\n--- transient response (time to 63% of excursion after the step) ---")
    for label, key in (
        ("boost pressure", "boost"),
        ("manifold pressure", "map"),
        ("RPM", "rpm"),
        ("EGT (mean)", "egt_mean"),
        ("CHT", "cht"),
        ("oil temperature", "oil_t"),
    ):
        tau = _rise_time(t, rec[key], up, down)
        print(f"  {label:20s} {'—' if tau is None else f'{tau:6.2f} s'}")

    # Stability: after the step-down and a settling period, the signal should be flat.
    tail = [v for tv, v in zip(t, rec["rpm"]) if tv > down + 15.0]
    if len(tail) > 20:
        mean = sum(tail) / len(tail)
        spread = max(tail) - min(tail)
        print(
            f"\n  post-transient RPM: mean {mean:.0f}, peak-to-peak {spread:.0f} "
            f"({'stable' if spread < 120 else 'CHECK — possible oscillation'})"
        )


def plot_throttle_transient(rec: dict) -> list[Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    t = rec["t"]
    up, down = rec["_step_up_s"], rec["_step_down_s"]

    def mark(ax) -> None:
        ax.axvline(up, color=C_FAULT, ls="--", lw=1.1, alpha=0.8)
        ax.axvline(down, color=C_FAULT, ls="--", lw=1.1, alpha=0.8)
        ax.grid(alpha=0.25, linewidth=0.6)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(labelsize=8)

    fig, axes = plt.subplots(5, 1, figsize=(11, 12), sharex=True)

    axes[0].plot(t, rec["throttle"], color="#666", lw=1.4, drawstyle="steps-post")
    axes[0].set_ylabel("throttle", fontsize=9)
    axes[0].set_title("Commanded throttle (step input)", fontsize=11, loc="left",
                      fontweight="600")
    mark(axes[0])

    axes[1].plot(t, rec["boost"], color=C_ACCENT, lw=1.3, label="Boost")
    axes[1].plot(t, rec["map"], color=C_PRIMARY, lw=1.3, label="MAP")
    axes[1].legend(fontsize=8, frameon=False)
    axes[1].set_ylabel("kPa (abs)", fontsize=9)
    axes[1].set_title("Intake pressures — turbo lag", fontsize=11, loc="left",
                      fontweight="600")
    mark(axes[1])

    axes[2].plot(t, rec["rpm"], color=C_PRIMARY, lw=1.3)
    axes[2].set_ylabel("RPM", fontsize=9)
    axes[2].set_title("Crankshaft speed — rotational inertia", fontsize=11, loc="left",
                      fontweight="600")
    mark(axes[2])

    axes[3].plot(t, rec["egt_mean"], color=C_FAULT, lw=1.3, label="EGT (mean)")
    axes[3].plot(t, rec["cht"], color=C_ACCENT, lw=1.3, label="CHT")
    axes[3].plot(t, rec["oil_t"], color=C_THIRD, lw=1.3, label="Oil temp")
    axes[3].legend(fontsize=8, frameon=False)
    axes[3].set_ylabel("°C", fontsize=9)
    axes[3].set_title("Thermal response — note the ordering of time constants",
                      fontsize=11, loc="left", fontweight="600")
    mark(axes[3])

    axes[4].plot(t, rec["cov"], color=C_FOURTH, lw=1.3)
    axes[4].set_ylabel("COV %", fontsize=9)
    axes[4].set_xlabel("time (s)", fontsize=9)
    axes[4].set_title("Combustion stability through the transient", fontsize=11,
                      loc="left", fontweight="600")
    mark(axes[4])

    fig.suptitle("Rapid throttle transition: 20% → 100% → 20%", fontsize=13,
                 fontweight="600")
    fig.tight_layout()
    path = OUTPUT_DIR / "08_throttle_transient.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return [path]


def plot_all(rec: dict, fault_type: str) -> list[Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    t = rec["t"]
    fault_at = rec["_inject_min"]
    written: list[Path] = []
    n_cyl = len(rec["egt"][0]) if rec["egt"] else 0

    def save(fig, name: str) -> None:
        path = OUTPUT_DIR / name
        fig.tight_layout()
        fig.savefig(path, dpi=130)
        plt.close(fig)
        written.append(path)

    # 01 — engine core
    fig, axes = plt.subplots(4, 1, figsize=(11, 10), sharex=True)
    axes[0].plot(t, rec["rpm"], color=C_PRIMARY, lw=1.2)
    style(axes[0], "Crankshaft speed", "RPM", fault_at)
    axes[1].plot(t, rec["map"], color=C_PRIMARY, lw=1.2, label="Manifold (MAP)")
    axes[1].plot(t, rec["boost"], color=C_ACCENT, lw=1.2, label="Boost (compressor out)")
    axes[1].legend(fontsize=8, frameon=False)
    style(axes[1], "Intake pressures", "kPa (abs)", fault_at)
    axes[2].plot(t, rec["power"], color=C_THIRD, lw=1.2)
    style(axes[2], "Brake power", "kW", fault_at)
    axes[3].plot(t, rec["fuel"], color=C_FOURTH, lw=1.2)
    style(axes[3], "Fuel flow", "L/h", fault_at)
    axes[3].set_xlabel("mission time (min)", fontsize=9)
    fig.suptitle(f"Engine core — fault: {fault_type}", fontsize=13, fontweight="600")
    save(fig, "01_engine_core.png")

    # 02 — temperatures
    fig, axes = plt.subplots(3, 1, figsize=(11, 8.5), sharex=True)
    for i in range(n_cyl):
        axes[0].plot(t, [row[i] for row in rec["egt"]],
                     color=CYL_COLORS[i % len(CYL_COLORS)], lw=1.0, label=f"Cyl {i+1}")
    axes[0].legend(fontsize=8, frameon=False, ncol=n_cyl)
    style(axes[0], "Exhaust gas temperature (per cylinder)", "°C", fault_at)
    axes[1].plot(t, rec["cht"], color=C_ACCENT, lw=1.2)
    style(axes[1], "Cylinder head temperature", "°C", fault_at)
    axes[2].plot(t, rec["oil_t"], color=C_THIRD, lw=1.2)
    style(axes[2], "Oil temperature", "°C", fault_at)
    axes[2].set_xlabel("mission time (min)", fontsize=9)
    fig.suptitle(f"Temperatures — fault: {fault_type}", fontsize=13, fontweight="600")
    save(fig, "02_temperatures.png")

    # 03 — lubrication
    fig, axes = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
    axes[0].plot(t, rec["oil_p"], color=C_PRIMARY, lw=1.2)
    style(axes[0], "Oil pressure", "kPa", fault_at)
    axes[1].plot(t, rec["oil_t"], color=C_ACCENT, lw=1.2)
    style(axes[1], "Oil temperature", "°C", fault_at)
    axes[1].set_xlabel("mission time (min)", fontsize=9)
    fig.suptitle(f"Lubrication — fault: {fault_type}", fontsize=13, fontweight="600")
    save(fig, "03_lubrication.png")

    # 04 — vibration
    fig, axes = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True)
    for i in range(n_cyl):
        axes[0].plot(t, [row[i] for row in rec["vib"]],
                     color=CYL_COLORS[i % len(CYL_COLORS)], lw=1.0, label=f"Cyl {i+1}")
    axes[0].legend(fontsize=8, frameon=False, ncol=n_cyl)
    style(axes[0], "Vibration RMS (per cylinder)", "g", fault_at)
    if rec["crest"] and rec["crest"][0]:
        for i in range(len(rec["crest"][0])):
            axes[1].plot(t, [row[i] if i < len(row) else None for row in rec["crest"]],
                         color=CYL_COLORS[i % len(CYL_COLORS)], lw=1.0)
    style(axes[1], "Crest factor (impulsiveness — misfire tell)", "peak / RMS", fault_at)
    axes[1].set_xlabel("mission time (min)", fontsize=9)
    fig.suptitle(f"Vibration — fault: {fault_type}", fontsize=13, fontweight="600")
    save(fig, "04_vibration.png")

    # 05 — health
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    axes[0].plot(t, rec["overall"], color=C_PRIMARY, lw=1.6)
    axes[0].set_ylim(-3, 103)
    style(axes[0], "Overall health score", "0-100", fault_at)
    for idx, key in enumerate(("cylinder", "lubrication", "cooling", "fuel", "turbo")):
        axes[1].plot(t, [s[key] for s in rec["subs"]],
                     color=CYL_COLORS[idx % len(CYL_COLORS)], lw=1.2, label=key)
    axes[1].axhline(40, color=C_FAULT, ls=":", lw=1.0, alpha=0.7)
    axes[1].text(t[0], 42, "RUL failure threshold (HI=40)", fontsize=7, color=C_FAULT)
    axes[1].set_ylim(-3, 103)
    axes[1].legend(fontsize=8, frameon=False, ncol=5)
    style(axes[1], "Subsystem health indicators", "0-100", fault_at)
    axes[1].set_xlabel("mission time (min)", fontsize=9)
    fig.suptitle(f"Health — fault: {fault_type}", fontsize=13, fontweight="600")
    save(fig, "05_health.png")

    # 06 — prognostics
    fig, axes = plt.subplots(3, 1, figsize=(11, 8), sharex=True)
    rul_t = [tv for tv, r in zip(t, rec["rul"]) if r is not None]
    rul_v = [r for r in rec["rul"] if r is not None]
    if rul_v:
        axes[0].plot(rul_t, rul_v, color=C_ACCENT, lw=1.4)
    else:
        axes[0].text(0.5, 0.5, "RUL stayed null (no degradation trend)",
                     ha="center", transform=axes[0].transAxes, fontsize=9, color="grey")
    style(axes[0], "Remaining useful life", "minutes", fault_at)
    axes[1].plot(t, rec["reliability"], color=C_THIRD, lw=1.4)
    axes[1].axhline(0.85, color="#888", ls=":", lw=1.0)
    axes[1].axhline(0.60, color=C_FAULT, ls=":", lw=1.0)
    axes[1].text(t[0], 0.86, "GO above", fontsize=7, color="#666")
    axes[1].text(t[0], 0.61, "NO-GO below", fontsize=7, color=C_FAULT)
    axes[1].set_ylim(-0.03, 1.03)
    style(axes[1], "Mission reliability R(t)", "0-1", fault_at)
    axes[2].plot(t, rec["severity"], color=C_FAULT, lw=1.4)
    axes[2].set_ylim(-0.03, 1.03)
    style(axes[2], f"Injected severity (ground truth): {fault_type}", "0-1", fault_at)
    axes[2].set_xlabel("mission time (min)", fontsize=9)
    fig.suptitle(f"Prognostics — fault: {fault_type}", fontsize=13, fontweight="600")
    save(fig, "06_prognostics.png")

    # 07 — residuals vs the healthy twin
    channels = ["oil_pressure_kpa", "cht_c", "egt_spread_c", "boost_pressure_kpa",
                "vibration_rms_max", "afr_mean"]
    fig, axes = plt.subplots(len(channels), 1, figsize=(11, 12), sharex=True)
    for ax, channel in zip(axes, channels):
        ax.plot(t, [r.get(channel, 0.0) for r in rec["resid"]], color=C_PRIMARY, lw=1.1)
        ax.axhline(0, color="#999", lw=0.8)
        style(ax, f"Residual: {channel}  (real − healthy twin)", "", fault_at)
    axes[-1].set_xlabel("mission time (min)", fontsize=9)
    fig.suptitle(f"Digital-twin residuals — fault: {fault_type}",
                 fontsize=13, fontweight="600")
    save(fig, "07_residuals.png")

    return written


def run_fusion_validation(
    minutes: float = 5.0,
    inject_at_min: float = 1.5,
    severity: float = 0.85,
    ramp_s: float = 20.0,
    time_scale: float = 5.0,
) -> dict:
    """Phase 5: inject a fault on *only* the secondary CHT probe — not the primary, not
    the real engine — and record everything needed to prove two things: (a) the fused
    estimate stays close to the true simulated CHT even while the secondary's raw
    reading drifts badly, and (b) the classifier names the secondary probe specifically,
    not the engine's cooling subsystem.

    `true_cht_c` comes from `sim.diagnostics.fusion_values["true_cht_c"]` — the exact
    pre-noise physical value, captured in `SimulationLoop.tick()` before either probe's
    independent noise is drawn from it. It is diagnostic-only ground truth, the same way
    `sim.diagnostics.true_state` already is for the Phase 3 sensor-fault checks below.
    """
    sim = SimulationLoop()
    sim.set_time_scale(time_scale)

    total_sim_s = minutes * 60.0
    inject_at_s = inject_at_min * 60.0
    ticks = int(total_sim_s / (TICK_S * time_scale))
    inject_tick = int(inject_at_s / (TICK_S * time_scale))

    rec: dict[str, list] = {
        k: []
        for k in (
            "t", "true_cht", "primary_cht", "secondary_cht", "fused_cht",
            "predicted_source", "suspect_sensor", "severity",
        )
    }

    injected = False
    for i in range(ticks):
        if i == inject_tick:
            sim.inject_sensor_fault("cht_sensor_secondary_drift", severity, ramp_s)
            injected = True
        frame = sim.tick(TICK_S)
        diag = sim.diagnostics

        rec["t"].append(sim.sim_time_s / 60.0)
        rec["true_cht"].append(diag.fusion_values.get("true_cht_c", float("nan")))
        rec["fused_cht"].append(frame.fused_cht_c)
        innov = frame.cht_sensor_innovations
        rec["primary_cht"].append(
            frame.fused_cht_c + innov.primary if innov and frame.fused_cht_c is not None else float("nan")
        )
        rec["secondary_cht"].append(
            frame.fused_cht_c + innov.secondary if innov and frame.fused_cht_c is not None else float("nan")
        )
        rec["predicted_source"].append(diag.predicted_source)
        rec["suspect_sensor"].append(diag.suspect_sensor)
        rec["severity"].append(sim.sensor_faults.severity("cht_sensor_secondary_drift"))

    rec["_inject_min"] = inject_at_min if injected else None
    return rec


def _report_fusion(rec: dict) -> bool:
    """Prints the pass/fail checks Task 9 asks for and returns overall pass/fail."""
    true_cht = rec["true_cht"]
    fused = rec["fused_cht"]
    secondary = rec["secondary_cht"]

    # Average over the last 30 s of the run, once the fault has fully ramped in and the
    # filter has settled — a single last sample would be noisy.
    tail_n = min(len(true_cht), 300)
    fused_error = sum(
        abs(f - t) for f, t in zip(fused[-tail_n:], true_cht[-tail_n:])
    ) / tail_n
    secondary_error = sum(
        abs(s - t) for s, t in zip(secondary[-tail_n:], true_cht[-tail_n:])
    ) / tail_n

    fused_close = fused_error < 3.0
    secondary_drifted = secondary_error > 10.0

    tail_sources = rec["predicted_source"][-tail_n:]
    tail_suspects = rec["suspect_sensor"][-tail_n:]
    source_ok = tail_sources.count("sensor_fault") / tail_n > 0.8
    suspect_ok = tail_suspects.count("cht_sensor_secondary") / tail_n > 0.8

    print("\n--- sensor fusion validation: secondary CHT probe fault ---")
    print(f"  mean |fused - true| (last {tail_n} samples)      {fused_error:6.2f} degC  "
          f"{'PASS' if fused_close else 'FAIL'} (< 3.0 degC)")
    print(f"  mean |secondary raw - true| (last {tail_n} samples) {secondary_error:6.2f} degC  "
          f"{'PASS' if secondary_drifted else 'FAIL'} (> 10.0 degC, i.e. it really did drift)")
    print(f"  predicted_source == 'sensor_fault'                 "
          f"{'PASS' if source_ok else 'FAIL'} "
          f"({tail_sources.count('sensor_fault')}/{tail_n} samples)")
    print(f"  suspect_sensor == 'cht_sensor_secondary'            "
          f"{'PASS' if suspect_ok else 'FAIL'} "
          f"({tail_suspects.count('cht_sensor_secondary')}/{tail_n} samples)")

    return fused_close and secondary_drifted and source_ok and suspect_ok


def plot_fusion(rec: dict) -> list[Path]:
    """The single clearest proof this feature works: true CHT, both raw probes and the
    fused estimate on one chart. The fused line should track the true line closely
    throughout — including after the secondary probe visibly departs from both."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    t = rec["t"]
    fault_at = rec["_inject_min"]

    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                              gridspec_kw={"height_ratios": [3, 1]})

    ax = axes[0]
    ax.plot(t, rec["true_cht"], color="#333333", lw=2.2, ls="--",
             label="True CHT (ground truth)")
    ax.plot(t, rec["primary_cht"], color=C_PRIMARY, lw=1.1, alpha=0.85,
             label="Primary probe (raw)")
    ax.plot(t, rec["secondary_cht"], color=C_FAULT, lw=1.1, alpha=0.85,
             label="Secondary probe (raw) — faulted")
    ax.plot(t, rec["fused_cht"], color=C_THIRD, lw=2.0,
             label="Fused estimate")
    ax.legend(fontsize=8, frameon=False, loc="upper left")
    style(ax, "Sensor fusion under an isolated secondary-probe fault", "°C", fault_at)

    ax2 = axes[1]
    ax2.plot(t, rec["severity"], color=C_FAULT, lw=1.3)
    ax2.set_ylim(-0.05, 1.05)
    style(ax2, "Injected fault severity (secondary probe only)", "severity", fault_at)
    ax2.set_xlabel("mission time (min)", fontsize=9)

    fig.suptitle(
        "Phase 5 validation — CHT dual-sensor fusion vs. an isolated probe fault",
        fontsize=13, fontweight="600",
    )
    fig.tight_layout()
    path = OUTPUT_DIR / "09_sensor_fusion.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return [path]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fault", default="bearing_wear", choices=list(FAULT_TYPES))
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--inject-at", type=float, default=3.0,
                        help="minutes into the mission to inject the fault")
    parser.add_argument("--severity", type=float, default=0.85)
    parser.add_argument("--ramp", type=float, default=45.0)
    parser.add_argument("--time-scale", type=float, default=10.0)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--scenario",
        default="fault",
        choices=["fault", "throttle-transient", "fusion"],
        help="'fault' injects a fault mid-mission; 'throttle-transient' steps the "
             "throttle 20%%->100%%->20%% to check turbo and thermal lag; 'fusion' "
             "injects a fault on only the secondary CHT probe to validate sensor fusion",
    )
    args = parser.parse_args()

    if args.quiet:
        logging.disable(logging.CRITICAL)
    else:
        logging.basicConfig(level=logging.WARNING)

    if args.scenario == "throttle-transient":
        print("Simulating a rapid throttle transient: 20% -> 100% -> 20%…")
        rec = run_throttle_transient()
        written = plot_throttle_transient(rec)
        _report_transient(rec)
        print(f"\nWrote {len(written)} plots to {OUTPUT_DIR}/")
        for path in written:
            print(f"  {path.name}")
        return

    if args.scenario == "fusion":
        print("Simulating sensor fusion under an isolated secondary CHT-probe fault…")
        rec = run_fusion_validation()
        written = plot_fusion(rec)
        passed = _report_fusion(rec)
        print(f"\nWrote {len(written)} plots to {OUTPUT_DIR}/")
        for path in written:
            print(f"  {path.name}")
        if not passed:
            sys.exit(1)
        return

    print(f"Simulating {args.minutes:.0f} min, injecting '{args.fault}' at "
          f"{args.inject_at:.0f} min (severity {args.severity}, ramp {args.ramp:.0f}s)…")
    rec = run(args.fault, args.minutes, args.inject_at, args.severity,
              args.ramp, args.time_scale)

    written = plot_all(rec, args.fault)

    final_health = rec["overall"][-1]
    final_rul = rec["rul"][-1]
    final_rel = rec["reliability"][-1]
    diagnosis = rec["_diagnosis"]
    subs = rec["subs"][-1]
    worst = min(subs, key=lambda k: subs[k])

    print("\n--- final state ---")
    print(f"  overall health      {final_health:.1f}")
    print(f"  worst subsystem     {worst} = {subs[worst]:.1f}")
    print(f"  RUL                 {final_rul if final_rul is not None else 'null'} min")
    print(f"  mission reliability {final_rel:.3f}")
    print(f"  classifier says     {diagnosis.predicted_fault} "
          f"(confidence {diagnosis.prediction_confidence:.2f}, "
          f"model {'loaded' if diagnosis.classifier_available else 'NOT trained'})")
    print(f"\nWrote {len(written)} plots to {OUTPUT_DIR}/")
    for path in written:
        print(f"  {path.name}")


if __name__ == "__main__":
    main()
