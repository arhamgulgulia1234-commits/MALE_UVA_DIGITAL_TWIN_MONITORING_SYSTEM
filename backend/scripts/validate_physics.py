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

Usage:
    python -m scripts.validate_physics [--fault bearing_wear] [--minutes 10]
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
    args = parser.parse_args()

    if args.quiet:
        logging.disable(logging.CRITICAL)
    else:
        logging.basicConfig(level=logging.WARNING)

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
