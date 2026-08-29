"""Part E item 15 — do the optimizer's hard safety bounds actually hold on a degraded
engine, and is the max_power recommendation genuinely more conservative than nominal?"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.engine_params import PARAMS as p
from app.ml.operating_point_optimizer import optimize_operating_point

FAILS = []
def ck(name, ok, detail):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         {detail}")
    if not ok: FAILS.append(name)

HEALTH_STATES = {
    "pristine": None,
    "moderately worn": {"bearing_wear": 0.35, "cooling_degradation": 0.30,
                        "turbo_wear": 0.25},
    "badly degraded": {"bearing_wear": 0.85, "cooling_degradation": 0.80,
                       "oil_pump_degradation": 0.70, "piston_ring_wear": 0.65,
                       "turbo_wear": 0.60},
}

ALT, AMB = 2400.0, 25.0

def show(tag, r):
    rec, base = r.recommended, r.baseline
    print(f"\n  --- objective=max_power, engine={tag} ---")
    print(f"    effective limits: " + ", ".join(f"{k}={v:.0f}" for k, v in r.effective_limits.items()))
    print(f"    recommended: throttle {rec.throttle_pct:.1f}%  afr_trim {rec.afr_trim:+.2f}  "
          f"timing {rec.injection_timing_trim_deg:+.2f} deg   feasible={rec.feasible}")
    print(f"    predicted:   power {rec.power_kw:.2f} kW  CHT {rec.cht_c:.1f}  "
          f"EGT {rec.egt_max_c:.1f}  oil {rec.oil_temp_c:.1f}C/"
          f"{rec.oil_pressure_kpa:.1f}kPa")
    print(f"    baseline (book cruise {p.nominal_cruise_throttle*100:.0f}%): "
          f"power {base.power_kw:.2f} kW  CHT {base.cht_c:.1f}  "
          f"EGT {base.egt_max_c:.1f}  oil {base.oil_temp_c:.1f}C/"
          f"{base.oil_pressure_kpa:.1f}kPa  feasible={base.feasible}")
    for c in rec.safety:
        mark = "ok " if c.ok else "VIOLATED"
        print(f"      {mark} {c.label:<22} {c.value:8.1f} {c.unit:<4} vs {c.direction} "
              f"{c.limit:8.1f}  margin {c.margin:+8.1f}")

results = {}
for tag, hs in HEALTH_STATES.items():
    r = optimize_operating_point(ALT, AMB, "max_power", hs)
    results[tag] = r
    show(tag, r)

print("\n--- assertions ---")

# The hard bound that actually matters is NOT "the recommendation always satisfies every
# limit" — on a wrecked engine no setpoint does, and a brute-force sweep of 1155 points
# confirms it. The bound that matters is: a violating point is never presented as safe.
for tag, r in results.items():
    viol = [c.label for c in r.recommended.safety if not c.ok]
    honest = (not viol and r.recommended.feasible) or (viol and not r.recommended.feasible)
    ck(f"max_power on a {tag} engine never presents a limit-breaching point as feasible",
       honest,
       f"violations: {viol or 'none'}; feasible flag = {r.recommended.feasible}")

bad = results["badly degraded"]; good = results["pristine"]; mid = results["moderately worn"]

ck("an infeasible result says so in plain language rather than reading as a recommendation",
   (not bad.feasible)
   and any("NOT a recommendation" in n for n in bad.safety_notes)
   and any("No setpoint" in n for n in bad.safety_notes),
   " || ".join(bad.safety_notes[-2:]))

ck("a degraded engine gets a DERATED limit envelope, not the pristine one",
   all(bad.effective_limits[k] < good.effective_limits[k]
       for k in ("cht_limit_c", "egt_limit_c", "oil_temp_limit_c"))
   and bad.effective_limits["oil_pressure_min_kpa"] >= good.effective_limits["oil_pressure_min_kpa"],
   " | ".join(f"{k}: {good.effective_limits[k]:.0f} -> {bad.effective_limits[k]:.0f}"
              for k in bad.effective_limits))

ck("max_power on a badly degraded engine is genuinely more conservative than on a "
   "pristine one (lower throttle AND lower predicted power)",
   (bad.recommended.throttle_pct < good.recommended.throttle_pct
    and bad.recommended.power_kw < good.recommended.power_kw),
   f"throttle {good.recommended.throttle_pct:.1f}% -> {bad.recommended.throttle_pct:.1f}%; "
   f"power {good.recommended.power_kw:.2f} -> {bad.recommended.power_kw:.2f} kW")

# The substantive case: a worn engine where a safe point DOES exist. The book cruise
# setting is already illegal there, so this is the real test of whether the optimizer
# pulls the engine back inside its envelope rather than just picking a smaller number.
ck("on a worn engine where the book setting is already outside limits, max_power returns "
   "a point that is genuinely inside them",
   (not mid.baseline.feasible) and mid.recommended.feasible
   and all(c.ok for c in mid.recommended.safety),
   f"book cruise feasible={mid.baseline.feasible} "
   f"(oil press {mid.baseline.oil_pressure_kpa:.1f} vs {mid.effective_limits['oil_pressure_min_kpa']:.1f} kPa, "
   f"CHT {mid.baseline.cht_c:.1f}); recommendation feasible={mid.recommended.feasible} "
   f"(oil press {mid.recommended.oil_pressure_kpa:.1f}, CHT {mid.recommended.cht_c:.1f}, "
   f"throttle {mid.recommended.throttle_pct:.1f}%)")

ck("that conservative point is thermally cooler than the book setting it replaces, not "
   "merely lower-powered",
   mid.recommended.cht_c < mid.baseline.cht_c
   and mid.recommended.oil_temp_c < mid.baseline.oil_temp_c,
   f"CHT {mid.baseline.cht_c:.1f} -> {mid.recommended.cht_c:.1f} C; "
   f"oil temp {mid.baseline.oil_temp_c:.1f} -> {mid.recommended.oil_temp_c:.1f} C; "
   f"oil pressure {mid.baseline.oil_pressure_kpa:.1f} -> {mid.recommended.oil_pressure_kpa:.1f} kPa")

for obj in ("max_range", "max_engine_life", "balanced"):
    for tag, hs in (("moderately worn", HEALTH_STATES["moderately worn"]),
                    ("badly degraded", HEALTH_STATES["badly degraded"])):
        r = optimize_operating_point(ALT, AMB, obj, hs)
        viol = [c.label for c in r.recommended.safety if not c.ok]
        honest = (not viol and r.recommended.feasible) or (viol and not r.recommended.feasible)
        ck(f"'{obj}' on a {tag} engine never presents a breaching point as feasible", honest,
           f"throttle {r.recommended.throttle_pct:.1f}%, power {r.recommended.power_kw:.2f} kW, "
           f"CHT {r.recommended.cht_c:.1f} C, violations {viol or 'none'}, "
           f"feasible={r.recommended.feasible}")

print(f"\n=========== {'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED'} ===========")
for f in FAILS: print("  FAILED:", f)
