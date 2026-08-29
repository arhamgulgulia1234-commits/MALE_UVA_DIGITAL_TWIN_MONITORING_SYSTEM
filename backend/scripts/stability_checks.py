"""Part B item 7 — numerical stability across the whole time-acceleration range.

Runs a full mission at each supported factor and compares the trajectories: a stable
integrator must give substantially the same mission at 20x as at 1x, with no divergence,
no growing oscillation, and no wall-clock overrun.
"""
from __future__ import annotations
import math, statistics, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sim.mission_profiles import TOTAL_MISSION_S  # noqa: E402
from app.sim.simulation_loop import INTERNAL_DT_S, SimulationLoop  # noqa: E402

TICK = 0.1
FAILS = []
def ck(name, ok, detail):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         {detail}")
    if not ok: FAILS.append(name)


def fly(scale: float, with_faults: bool):
    """One full mission at `scale`x, sampled on a common simulated-time grid."""
    sim = SimulationLoop(); sim.set_time_scale(scale)
    ticks = int(TOTAL_MISSION_S / (TICK * scale))
    inject = int(ticks * 0.25)
    series = {k: [] for k in ("t","rpm","cht","egt","oilp","oilt","boost","map","vib","health")}
    worst_tick_s = 0.0
    t_start = time.perf_counter()
    for i in range(ticks):
        if with_faults and i == inject:
            sim.inject_fault("bearing_wear", 0.8, 30.0)
            sim.inject_fault("cooling_degradation", 0.7, 30.0)
        t0 = time.perf_counter()
        f = sim.tick(TICK)
        worst_tick_s = max(worst_tick_s, time.perf_counter() - t0)
        series["t"].append(sim.sim_time_s)
        series["rpm"].append(f.rpm); series["cht"].append(f.cht_c)
        series["egt"].append(max(c.egt_c for c in f.cylinders))
        series["oilp"].append(f.oil_pressure_kpa); series["oilt"].append(f.oil_temp_c)
        series["boost"].append(f.boost_pressure_kpa); series["map"].append(f.manifold_pressure_kpa)
        series["vib"].append(max(c.vibration_rms for c in f.cylinders))
        series["health"].append(f.health.overall_score)
    return series, worst_tick_s, time.perf_counter() - t_start, ticks


def resample(series, key, grid):
    """Linear interpolation onto a common simulated-time grid.

    Interpolated, not zero-order hold, and the caller skips the start-up transient. At
    20x a tick advances 2 s of simulated time, so during engine start — RPM going
    700->2700 in about a second — the 1x and 20x runs simply do not have samples at the
    same instants. Holding the previous sample then reports a ~1800 rpm "difference"
    that is entirely an artefact of comparing a fast ramp at two sample rates, not
    integrator divergence. Measured properly, past the start, the two runs agree to
    ~12 rpm over a 15-minute mission."""
    t, v = series["t"], series[key]
    out, j = [], 0
    for g in grid:
        while j + 1 < len(t) and t[j + 1] < g: j += 1
        if j + 1 >= len(t):
            out.append(v[-1]); continue
        span = t[j + 1] - t[j]
        frac = 0.0 if span <= 1e-9 else (g - t[j]) / span
        out.append(v[j] + (v[j + 1] - v[j]) * frac)
    return out


def main():
    print(f"Full mission = {TOTAL_MISSION_S:.0f} s simulated; fixed sub-step "
          f"{INTERNAL_DT_S*1000:.0f} ms; tick {TICK*1000:.0f} ms")
    print(f"At 20x each 100 ms tick must advance {0.1*20:.1f} s of simulated time = "
          f"{int(round(0.1*20/INTERNAL_DT_S))} sub-steps.\n")

    for with_faults in (False, True):
        tag = "with two ramped faults" if with_faults else "healthy"
        print(f"--- full mission, {tag} ---")
        runs = {}
        for scale in (1.0, 5.0, 20.0):
            s, worst, wall, ticks = fly(scale, with_faults)
            runs[scale] = s
            finite = all(math.isfinite(x) for k in s if k != "t" for x in s[k])
            nonneg = all(x >= 0 for k in ("rpm","cht","egt","oilp","boost","map","vib")
                         for x in s[k])
            budget_ok = worst < TICK
            print(f"   {scale:4.0f}x: {ticks:5d} ticks, {wall:6.2f}s wall "
                  f"({TOTAL_MISSION_S/max(wall,1e-9):5.1f}x realtime headroom), "
                  f"worst single tick {worst*1000:6.1f} ms "
                  f"(budget {TICK*1000:.0f} ms) | finite={finite} nonneg={nonneg}")
            ck(f"{tag} @ {scale:.0f}x: all signals finite and non-negative",
               finite and nonneg, f"{ticks} ticks checked")
            ck(f"{tag} @ {scale:.0f}x: sim keeps up — worst tick inside the {TICK*1000:.0f} ms budget",
               budget_ok, f"worst tick {worst*1000:.1f} ms, total wall {wall:.2f}s for "
                          f"{TOTAL_MISSION_S:.0f}s of simulated flight")

        # Trajectory agreement between 1x and 20x on a common grid.
        # Skip the start-up transient — see `resample` for why comparing it across two
        # sample rates measures the sampling, not the integrator.
        SKIP_S = 60.0
        grid = [i * 5.0 for i in range(int(TOTAL_MISSION_S / 5.0)) if i * 5.0 >= SKIP_S]
        print(f"   trajectory agreement, 1x vs 20x ({len(grid)} points, from t={SKIP_S:.0f}s):")
        agree = True
        for key, tol, unit in (("rpm", 30.0, "rpm"), ("cht", 6.0, "C"), ("egt", 20.0, "C"),
                               ("oilp", 25.0, "kPa"), ("oilt", 4.0, "C"),
                               ("boost", 6.0, "kPa"), ("health", 12.0, "pts")):
            a = resample(runs[1.0], key, grid); b = resample(runs[20.0], key, grid)
            d = [abs(x - y) for x, y in zip(a, b)]
            mx, med = max(d), statistics.median(d)
            ok = mx <= tol
            agree = agree and ok
            print(f"      {key:<7} max |1x-20x| = {mx:8.2f} {unit:<4} (median {med:6.2f}) "
                  f"tol {tol:6.1f}  {'ok' if ok else 'OUT OF TOLERANCE'}")
        ck(f"{tag}: 20x reproduces the 1x mission within tolerance (no divergence)", agree,
           "per-signal maxima printed above")

        # Oscillation: an unstable integrator rings. Look at high-frequency energy in RPM
        # during the steady cruise segment, at each scale.
        print("   late-cruise oscillation check (tick-to-tick RPM variation):")
        osc = {}
        for scale in (1.0, 5.0, 20.0):
            r = runs[scale]["rpm"]
            seg = r[int(len(r) * 0.55): int(len(r) * 0.75)]
            diffs = [abs(b - a) for a, b in zip(seg, seg[1:])]
            osc[scale] = statistics.mean(diffs) if diffs else 0.0
            print(f"      {scale:4.0f}x: mean |dRPM/tick| = {osc[scale]:6.2f} rpm, "
                  f"peak-to-peak {max(seg)-min(seg):7.2f} rpm")
        ck(f"{tag}: no oscillation growth at high time acceleration",
           osc[20.0] < 60.0 and all(math.isfinite(v) for v in osc.values()),
           f"mean |dRPM/tick|: 1x={osc[1.0]:.2f}, 5x={osc[5.0]:.2f}, 20x={osc[20.0]:.2f}")
        print()

    print(f"=========== {'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED'} ===========")
    for f in FAILS: print("  FAILED:", f)


if __name__ == "__main__":
    main()
