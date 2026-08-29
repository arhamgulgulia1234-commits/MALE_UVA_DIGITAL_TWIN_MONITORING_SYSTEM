"""Part B hardening checks — numeric pass/fail on each stated physical sanity condition.

Not a feature: this is a throwaway-grade verification harness for the hardening sprint.
Run with:  python -m scripts.hardening_checks
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.physics.fault_models import FaultState  # noqa: E402
from app.physics.plant import EnginePlant  # noqa: E402
from app.sim.simulation_loop import SimulationLoop  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}\n        {detail}")


def settle(plant: EnginePlant, throttle: float, alt: float, fs: FaultState,
           seconds: float = 240.0, dt: float = 0.02, airspeed: float = 55.0,
           ambient: float | None = None):
    """Run the plant to steady state at a fixed operating point."""
    n = int(seconds / dt)
    for _ in range(n):
        plant.substep(dt, throttle, alt, airspeed, fs, ambient_temperature_c=ambient,
                      generate_vibration=False)
    return plant.state


def fresh(seed: int = 7, noise: bool = False) -> EnginePlant:
    p = EnginePlant(seed=seed, sensor_noise=noise)
    p.reset(0.0)
    return p


# ---------------------------------------------------------------- 1. power vs throttle
def c1_power_vs_throttle():
    fs = FaultState.healthy()
    pts = []
    for thr in (0.2, 0.35, 0.5, 0.65, 0.8, 1.0):
        pl = fresh()
        # hold RPM fixed is impossible with a free crank; instead compare at a fixed
        # altitude and let RPM settle, which is the operational meaning of "more throttle
        # = more power". Also report the fixed-RPM indicated case below.
        st = settle(pl, thr, 0.0, fs)
        pts.append((thr, st.power_brake_kw))
    mono = all(pts[i][1] < pts[i + 1][1] - 1e-6 for i in range(len(pts) - 1))
    check("B5.1 power increases monotonically with throttle (sea level)", mono,
          "  ".join(f"{t:.2f}->{p:.2f}kW" for t, p in pts))

    # Fixed-RPM variant: freeze the crank by pinning rpm each substep.
    pts2 = []
    for thr in (0.2, 0.4, 0.6, 0.8, 1.0):
        pl = fresh()
        fsx = FaultState.healthy()
        for _ in range(int(120 / 0.02)):
            pl.engine.rpm = 2600.0
            pl.substep(0.02, thr, 0.0, 55.0, fsx, generate_vibration=False)
        pts2.append((thr, pl.state.torque_brake_nm * 2600.0 * 2 * math.pi / 60.0 / 1000.0))
    mono2 = all(pts2[i][1] < pts2[i + 1][1] - 1e-6 for i in range(len(pts2) - 1))
    check("B5.1b power increases monotonically with throttle at FIXED 2600 RPM", mono2,
          "  ".join(f"{t:.2f}->{p:.2f}kW" for t, p in pts2))


# ---------------------------------------------------------------- 2. power vs altitude
def c2_power_vs_altitude():
    fs = FaultState.healthy()
    pts = []
    for alt in (0.0, 1000.0, 2000.0, 3500.0, 5000.0, 7000.0):
        pl = fresh()
        st = settle(pl, 0.85, alt, fs)
        pts.append((alt, st.power_brake_kw))
    mono = all(pts[i][1] > pts[i + 1][1] + 1e-6 for i in range(len(pts) - 1))
    check("B5.2 power decreases with altitude at fixed throttle (0.85)", mono,
          "  ".join(f"{a:.0f}m->{p:.2f}kW" for a, p in pts))


# ---------------------------------------------------------------- 3. EGT vs lean faults
def c3_egt_lean():
    from app.core.engine_params import PARAMS as p

    # (a) Leaning the mixture from the rich schedule toward afr_peak_egt must raise EGT,
    #     and the NET peak must sit where afr_peak_egt claims it does. That is the whole
    #     content of "leaning raises EGT" — past the peak it correctly falls again.
    sweep = []
    for trim in [x / 10 for x in range(-9, 31, 2)]:
        pl = fresh()
        pl.engine.set_trims(afr_trim=trim)
        st = settle(pl, 0.78, 2400.0, FaultState.healthy(), seconds=400.0)
        sweep.append((st.afr_mean, max(st.egt_c)))
    peak_afr, peak_egt = max(sweep, key=lambda r: r[1])
    rich = [r for r in sweep if r[0] < peak_afr - 0.05]
    rising = all(rich[i][1] < rich[i + 1][1] + 1e-6 for i in range(len(rich) - 1))
    peak_ok = abs(peak_afr - p.afr_peak_egt) < 0.5
    check("B5.3a EGT rises as the mixture is leaned toward peak, and the net peak sits "
          "at afr_peak_egt", rising and peak_ok,
          f"EGT climbs {rich[0][1]:.1f}C @AFR{rich[0][0]:.2f} -> {peak_egt:.1f}C @AFR"
          f"{peak_afr:.2f}; afr_peak_egt={p.afr_peak_egt} (net peak error "
          f"{abs(peak_afr - p.afr_peak_egt):.2f} AFR)")

    # (b) Both named faults must raise EGT over the severity range where they lean the
    #     charge toward peak.
    base = fresh()
    st0 = settle(base, 0.75, 1500.0, FaultState.healthy())
    out, ok = [], True
    for ft, sev in (("fuel_injector_clog", 0.3), ("spark_degradation", 0.9)):
        pl = fresh()
        fs = FaultState.healthy()
        setattr(fs, ft, sev)
        idx = fs.cylinder_for(ft, 4)
        st = settle(pl, 0.75, 1500.0, fs)
        d = st.egt_c[idx] - st0.egt_c[idx]
        out.append(f"{ft}(sev {sev}): affected cyl {st0.egt_c[idx]:.1f} -> "
                   f"{st.egt_c[idx]:.1f} C ({d:+.1f})")
        if d <= 1.0:
            ok = False
    check("B5.3b EGT rises on the affected cylinder for fuel_injector_clog / "
          "spark_degradation", ok, " | ".join(out))

    # (c) Documented-and-true: past peak, a badly starved cylinder reads COOLER. Assert
    #     the direction so it stays deliberate rather than drifting.
    pl = fresh()
    fs = FaultState.healthy()
    fs.fuel_injector_clog = 0.9
    idx = fs.cylinder_for("fuel_injector_clog", 4)
    st = settle(pl, 0.75, 1500.0, fs)
    d = st.egt_c[idx] - st0.egt_c[idx]
    spread = max(st.egt_c) - min(st.egt_c)
    check("B5.3c severe injector clog drives that cylinder lean OF peak, so its EGT "
          "falls while the spread still flags it", d < -10.0 and spread > 40.0,
          f"affected cyl {d:+.1f} C, EGT spread {spread:.1f} C")


# ---------------------------------------------------------------- 4/5. oil pressure
def c4_oil_pressure_vs_temp():
    from app.physics.lubrication_model import LubricationModel
    fs = FaultState.healthy()
    pts = []
    for t_oil in (60.0, 80.0, 100.0, 120.0, 140.0):
        lm = LubricationModel()
        for _ in range(400):
            o = lm.step(0.02, 2600.0, t_oil, fs)
        pts.append((t_oil, o.oil_pressure_kpa))
    mono = all(pts[i][1] > pts[i + 1][1] + 1e-9 for i in range(len(pts) - 1))
    check("B5.4 oil pressure falls as oil temperature rises at fixed 2600 RPM", mono,
          "  ".join(f"{t:.0f}C->{p:.1f}kPa" for t, p in pts))


def c5_oil_pressure_vs_wear():
    from app.physics.lubrication_model import LubricationModel

    def run(fs):
        lm = LubricationModel()
        for _ in range(400):
            o = lm.step(0.02, 2600.0, 95.0, fs)
        return o.oil_pressure_kpa

    p_h = run(FaultState.healthy())
    fs_b = FaultState.healthy(); fs_b.bearing_wear = 0.8
    fs_p = FaultState.healthy(); fs_p.oil_pump_degradation = 0.8
    p_b, p_p = run(fs_b), run(fs_p)
    ok = p_b < p_h - 1.0 and p_p < p_h - 1.0
    check("B5.5 oil pressure falls further with bearing_wear / oil_pump_degradation", ok,
          f"healthy {p_h:.1f} | bearing_wear(0.8) {p_b:.1f} | pump_deg(0.8) {p_p:.1f} kPa")


# ---------------------------------------------------------------- 6. CHT vs cooling
def c6_cht_cooling():
    rows = []
    ok = True
    deltas = {}
    for thr in (0.4, 0.7, 1.0):
        pl = fresh()
        st_h = settle(pl, thr, 1500.0, FaultState.healthy(), seconds=600.0)
        cht_h = st_h.cht_c
        pl2 = fresh()
        fs = FaultState.healthy(); fs.cooling_degradation = 0.85
        st_f = settle(pl2, thr, 1500.0, fs, seconds=600.0)
        cht_f = st_f.cht_c
        deltas[thr] = cht_f - cht_h
        rows.append(f"thr {thr:.1f}: {cht_h:.1f} -> {cht_f:.1f} C (d={cht_f-cht_h:+.1f})")
        if cht_f <= cht_h + 0.5:
            ok = False
    check("B5.6a CHT rises when cooling_degradation active", ok, " | ".join(rows))
    faster = deltas[1.0] > deltas[0.7] > deltas[0.4]
    check("B5.6b CHT rise from cooling_degradation grows with throttle", faster,
          f"delta at thr 0.4/0.7/1.0 = {deltas[0.4]:+.1f} / {deltas[0.7]:+.1f} / {deltas[1.0]:+.1f} C")


# ---------------------------------------------------------------- 7. vibration ramp
def c7_vibration_smooth():
    sim = SimulationLoop()
    sim.set_time_scale(1.0)
    sim.jump_phase("cruise")
    series = []
    for i in range(1400):           # 140 s at 0.1 s ticks
        if i == 300:
            sim.inject_fault("bearing_wear", 0.9, 45.0)
        f = sim.tick(0.1)
        series.append(sum(c.vibration_rms for c in f.cylinders) / len(f.cylinders))
    pre = sum(series[280:300]) / 20
    post = sum(series[1000:1100]) / 100
    # biggest single-tick jump in the 5 s window straddling injection
    jumps = [abs(series[i + 1] - series[i]) for i in range(295, 350)]
    rise = post - pre
    biggest = max(jumps)
    smooth = biggest < 0.35 * rise
    # and it must actually take most of the ramp to get there
    half = pre + 0.5 * rise
    t_half = next((i for i in range(300, 1400) if series[i] >= half), None)
    took = (t_half - 300) * 0.1 if t_half else None
    check("B5.7 vibration RMS rises smoothly, not instantly, on a bearing_wear ramp",
          smooth and took is not None and took > 5.0,
          f"pre {pre:.4f} -> post {post:.4f} g; largest 1-tick jump {biggest:.4f} g "
          f"({100*biggest/max(rise,1e-9):.1f}% of total rise); time to 50% = {took}s (ramp 45s)")


# ---------------------------------------------------------------- 8. finite & non-negative
def c8_finiteness():
    faults = list(FaultState().snapshot().keys())
    bad: list[str] = []
    checked = 0
    NONNEG = ("rpm", "manifold_pressure_kpa", "boost_pressure_kpa", "cht_c",
              "oil_pressure_kpa", "fuel_flow_lph")
    for scenario in ["<full mission>"] + faults:
        sim = SimulationLoop()
        sim.set_time_scale(12.0)
        for i in range(1200):        # 1200 ticks x 0.1 s x 12 = 24 min sim
            if scenario != "<full mission>" and i == 200:
                sim.inject_fault(scenario, 1.0, 20.0)
            f = sim.tick(0.1)
            checked += 1
            vals = {
                "rpm": f.rpm, "manifold_pressure_kpa": f.manifold_pressure_kpa,
                "boost_pressure_kpa": f.boost_pressure_kpa, "cht_c": f.cht_c,
                "oil_temp_c": f.oil_temp_c, "oil_pressure_kpa": f.oil_pressure_kpa,
                "fuel_flow_lph": f.fuel_flow_lph,
                "combustion_instability_pct": f.combustion_instability_pct or 0.0,
                "battery_voltage_v": f.battery_voltage_v or 0.0,
                "bsfc": f.bsfc_g_per_kwh if f.bsfc_g_per_kwh is not None else 0.0,
                "health": f.health.overall_score,
                "reliability": f.mission_reliability.score,
            }
            for c in f.cylinders:
                vals[f"egt{c.id}"] = c.egt_c
                vals[f"vib{c.id}"] = c.vibration_rms
            for k, v in vals.items():
                if not math.isfinite(v):
                    bad.append(f"{scenario}/{k}=nonfinite@tick{i}")
                elif (k in NONNEG or k.startswith(("vib",))) and v < 0:
                    bad.append(f"{scenario}/{k}={v:.3f}@tick{i}")
            if len(bad) > 8:
                break
        if len(bad) > 8:
            break
    check("B5.8 no signal NaN/inf/negative across a full mission + every fault type",
          not bad, f"{checked} frames checked across {1+len(faults)} runs; "
                   f"violations: {bad[:8] if bad else 'none'}")


# ---------------------------------------------------------------- 9. twin isolation
def c9_twin_isolation():
    sim = SimulationLoop()
    sim.set_time_scale(1.0)
    sim.jump_phase("cruise")
    for _ in range(600):
        sim.tick(0.1)

    import copy
    base_twin = copy.deepcopy(sim.twin.plant.state.channels())

    # Fork: same sim state, one branch gets a severe fault, one does not.
    sim_a = copy.deepcopy(sim)
    sim_b = copy.deepcopy(sim)
    sim_a.inject_fault("bearing_wear", 1.0, 5.0)
    sim_a.inject_fault("cooling_degradation", 1.0, 5.0)
    sim_a.inject_fault("misfire", 1.0, 5.0)

    twin_a, twin_b, real_a, real_b = [], [], [], []
    for _ in range(900):
        fa = sim_a.tick(0.1)
        fb = sim_b.tick(0.1)
        twin_a.append(dict(sim_a.twin.plant.state.channels()))
        twin_b.append(dict(sim_b.twin.plant.state.channels()))
        real_a.append((fa.oil_pressure_kpa, fa.cht_c, fa.rpm))
        real_b.append((fb.oil_pressure_kpa, fb.cht_c, fb.rpm))

    # The twin plant carries independent vibration RNG; compare only deterministic channels.
    DET = ["rpm", "manifold_pressure_kpa", "boost_pressure_kpa", "egt_mean_c",
           "egt_spread_c", "cht_c", "oil_temp_c", "oil_pressure_kpa", "fuel_flow_lph",
           "afr_mean", "torque_brake_nm", "battery_voltage_v", "alternator_output_v"]
    worst = {c: max(abs(a[c] - b[c]) for a, b in zip(twin_a, twin_b)) for c in DET}
    twin_identical = all(v < 1e-9 for v in worst.values())
    real_moved = (abs(real_a[-1][0] - real_b[-1][0]) > 10.0
                  and abs(real_a[-1][1] - real_b[-1][1]) > 1.0)
    check("B5.9 digital twin is completely unaffected by fault injection on the real engine",
          twin_identical and real_moved,
          f"max |twin_faulted - twin_healthy| over 90 s = "
          f"{max(worst.values()):.3e} (worst channel "
          f"{max(worst, key=worst.get)}); real engine meanwhile moved "
          f"oil_p {real_b[-1][0]:.1f}->{real_a[-1][0]:.1f} kPa, "
          f"CHT {real_b[-1][1]:.1f}->{real_a[-1][1]:.1f} C")
    _ = base_twin


# ------------------------------------------------- 10. recovery after clearing a fault
def c10_recovery():
    """Clearing a fault must return health, RUL and the recommendation to nominal.

    Added after a user reported that RUL and mission reliability "do not increase again"
    once an injected fault is cleared. They do — but for the thermally-dominated faults
    it takes minutes of *simulated* time, because the engine is genuinely still hot. This
    check pins down that it always recovers, and records how long each fault takes, so a
    genuine regression is distinguishable from honest thermal inertia."""
    from app.physics.fault_models import FAULT_TYPES

    rows, never = [], []
    for ft in FAULT_TYPES:
        sim = SimulationLoop()
        sim.set_time_scale(10.0)
        sim.jump_phase("cruise")
        for _ in range(200):
            sim.tick(0.1)
        sim.inject_fault(ft, 0.85, 15.0)
        for _ in range(500):
            sim.tick(0.1)
        worst = sim.get_latest()
        sim.clear_fault(ft, 8.0)
        t0 = sim.sim_time_s
        t_go = t_health = t_rul = None
        for _ in range(12000):          # up to 20 simulated minutes
            fr = sim.tick(0.1)
            if t_go is None and fr.mission_reliability.recommendation == "GO":
                t_go = sim.sim_time_s - t0
            if t_health is None and fr.health.overall_score > 95.0:
                t_health = sim.sim_time_s - t0
            # RUL nulls last: the trend fit has to go flat, which trails the health
            # score recovering. Waiting for all three is the point — stopping at the
            # first two reports a false failure.
            if t_rul is None and fr.rul_minutes is None:
                t_rul = sim.sim_time_s - t0
            if None not in (t_go, t_health, t_rul):
                break
        final = sim.get_latest()
        recovered = (final.health.overall_score > 95.0
                     and final.rul_minutes is None
                     and final.mission_reliability.recommendation == "GO")
        if not recovered:
            never.append(ft)
        rows.append(f"{ft} worst_hp={worst.health.overall_score:.0f} "
                    f"GO@{t_go if t_go is None else round(t_go)}s "
                    f"hp>95@{t_health if t_health is None else round(t_health)}s "
                    f"RULnull@{t_rul if t_rul is None else round(t_rul)}s")

    check("B5.10 clearing a fault returns health, RUL and recommendation to nominal "
          "for every fault type", not never,
          "never recovered: " + (str(never) if never else "none") + " | "
          + " | ".join(rows))


def main() -> None:
    for fn in (c1_power_vs_throttle, c2_power_vs_altitude, c3_egt_lean,
               c4_oil_pressure_vs_temp, c5_oil_pressure_vs_wear, c6_cht_cooling,
               c7_vibration_smooth, c8_finiteness, c9_twin_isolation,
               c10_recovery):
        print(f"\n--- {fn.__name__} ---")
        fn()
    n_fail = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n=========== {len(RESULTS)-n_fail}/{len(RESULTS)} checks passed ===========")
    for name, ok, _ in RESULTS:
        if not ok:
            print(f"  FAILED: {name}")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
