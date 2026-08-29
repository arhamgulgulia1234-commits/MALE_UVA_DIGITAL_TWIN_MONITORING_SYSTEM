# Hardening sprint — test report

**Date:** 2026-08-29
**Scope:** no new features, endpoints, models or UI components. Find bugs, verify
correctness, report honestly on what works versus what only looks complete.
**Environment:** Linux, 16 cores, Python 3.12.3, Node 24.19.0, npm 11.17.0.

Everything below was executed, not read. The verification harnesses are checked in under
`backend/scripts/` so any claim here can be re-run:

| Script | Covers |
| --- | --- |
| `scripts/hardening_checks.py` | Part B item 5 — the nine physical sanity conditions, plus recovery-after-clear |
| `scripts/stability_checks.py` | Part B item 7 — numerical stability 1x / 5x / 20x |
| `scripts/classifier_report.py` | Part D item 12 — cross-validated classifier metrics |
| `scripts/rul_checks.py` | Part D item 13 — RUL and GO/CAUTION/NO-GO vs degradation rate |
| `scripts/optimizer_safety.py` | Part E item 15 — optimizer hard safety bounds |
| `scripts/validate_physics.py` | pre-existing; re-run, plots regenerated |

---

## Headline

**Seven genuine bugs found and fixed.** Three of them were safety-inverting — the system
was confidently reporting the opposite of the truth:

1. **RUL reported 409 minutes of remaining life on an engine sitting at its failure
   threshold** (the underlying fit correctly said 0.0). The faster the degradation, the
   more optimistic the readout became.
2. **A cylinder-localised spark fault put its EGT signature on the three healthy
   cylinders** (+134 °C) while the genuinely faulty one read −102 °C — the fault
   signature inverted and attributed to the wrong hardware.
3. **A subsystem correctly reporting 0 minutes to failure was silently discarded** in
   favour of a healthier subsystem's longer estimate, so the RUL readout jumped *up* at
   the exact moment it should have bottomed out.

The remaining four: an end-to-end auth break, a frame-dropping race in the WebSocket
broadcast, silent clamping of out-of-range control commands, and a 500 on any request
carrying a NaN.

**What is genuinely solid:** the digital-twin isolation (bit-exact), integration
stability at 20x, the async loop over a 16-minute continuous run (+1 ms drift), the
classifier's physical-vs-sensor separation (1 error in 25 019 samples), the optimizer's
infeasibility honesty, and the 3D scene's zero-React-reconciliation design.

---

## Part A — build and run

### A1. Fresh install — **PASS**

`backend/.venv` and `frontend/node_modules` deleted and rebuilt from
`requirements.txt` / `package.json`.

* **Backend:** clean. 34 packages, no conflicts, no build failures.
* **Frontend:** clean. 492 packages in 9 s, no peer-dependency conflicts.

Warnings worth knowing about (neither is a blocker, neither was introduced this session):

* **9 npm deprecation warnings** — transitive: `inflight`, `glob@7`/`glob@10`,
  `rimraf@3`, `@humanwhocodes/*`, `eslint@8.57.1`, `recharts@2` (v2 branch no longer
  active), `three-mesh-bvh@0.7.8` (flagged incompatible with this three.js version).
* **5 high-severity npm audit findings**, all inside `next@14.2.35` and its bundled
  `postcss` (SSRF, cache poisoning, DoS, path traversal). `npm audit fix --force` wants
  `next@16.3.3` — a two-major-version jump. **Not attempted**: a framework major upgrade
  is not a hardening change. See *Known risks*.

### A2. Cold start — **PASS**

Backend startup is clean end to end:

```
Database ready.
GIL switch interval 5 ms -> 1 ms ...
Telemetry auth disabled — set TELEMETRY_AUTH_ENABLED=true to enforce.
Starting Phase 2 physics simulation.
Loaded fault classifier (15 classes) from .../fault_classifier.joblib
Application startup complete.
```

No errors, no deprecation warnings, no unraisable exceptions. Run additionally under
`python -X dev -W default` (development mode, all warnings enabled): the only extra
output was asyncio *slow-callback* notices during module import (0.71 s / 0.41 s / 0.10 s)
— an artefact of `-X dev`'s 100 ms threshold applying to one-off import work, not a
steady-state problem. They do not recur once running.

Frontend: `next dev` ready in 1.8 s, no warnings. `next build` compiles clean,
`tsc --noEmit` clean, `next lint` clean — before and after every change in this session.

### A3. TODO / FIXME / stub sweep

Full-tree grep for `TODO|FIXME|XXX|HACK|not implemented|NotImplemented|stub|placeholder`.
Everything found, with a verdict on each:

| File:line | Content | Verdict |
| --- | --- | --- |
| `frontend/components/dashboard/MissionReliabilityCard.tsx:60` | "TODO(phase-2): score currently a heuristic … replace with `mission_reliability.py` output" | **Stale and wrong — FIXED.** The card already renders `frame.mission_reliability`, which *is* that module's Weibull output. Comment removed, replaced with an accurate note. |
| `frontend/components/dashboard/RULPanel.tsx:30` | "TODO(phase-2): replace linear-decay heuristic with `rul_predictor.py` output" | **Stale and wrong — FIXED.** Already renders `frame.rul_minutes` from that predictor. Comment removed, replaced with an accurate note. |
| `frontend/components/dashboard/VibrationSpectrum.tsx:9` | "TODO(phase-2): … once `vibration_model.py` lands, swap for a rolling-FFT display" | **Premise stale — FIXED.** `vibration_model.py` *has* landed and does derive spectral features; they are used as residual channels but never put on the wire, so the component genuinely can only plot RMS. Comment rewritten to say that, and that a true FFT display needs a frame-schema change first. |
| `backend/app/ingestion/adapter_interface.py:142,214,220` | `CANBusAdapter` raising `NotImplementedError` | **Legitimate, left alone.** A deliberately documented integration seam, described as a stub in the class docstring, the README and `docs/architecture.md`. Not something "supposed to be filled in". |
| `ml-notebooks/README.md`, `frontend/public/models/README.md` | "Empty for now" | Accurate placeholders for genuinely empty directories. Left alone. |
| `ScenarioBuilder.tsx`, `OptimizerPanel.tsx` (`placeholder=`) | HTML input placeholders | Not stubs. |
| `Crankshaft.tsx` ("propeller hub stub", "stub blade roots") | Geometry naming | Not stubs. |

**After cleanup, the only `TODO`/`NotImplemented` left in application code is the
documented CAN adapter seam.**

### A4. TelemetryFrame field parity — **PASS**

Compared programmatically (introspecting `TelemetryFrame.model_fields` against the parsed
`lib/types.ts` interface) across all eight shared shapes: `TelemetryFrame`,
`CylinderReading`, `SubsystemScores`, `HealthState`, `MissionReliability`, `ActiveFault`,
`MaintenanceAdvisory`, `ClassifierExplanation`.

**No missing fields in either direction. No type mismatches.** All 26 `TelemetryFrame`
fields including every Phase 2–4 addition (`battery_voltage_v`, `alternator_output_v`,
`injection_timing_deg`, `combustion_instability_pct`, `ambient_temperature_c`,
`bsfc_g_per_kwh`, `efficiency_trend`, `maintenance_advisories`, `is_replay`,
`replay_mission_id`) are present on both sides with matching types.

One cosmetic difference, deliberately not "fixed": `rul_minutes` is `Optional[float] = None`
in Pydantic (optional) but `number | null` in TS (required-but-nullable). The backend
always emits the key, so this is correct as written — TS is the stricter of the two.

Verified against a live frame off the WebSocket: every field populated, nothing null that
should not be.

---

## Part B — physics correctness

### B5. The nine sanity conditions — **13 / 13 PASS** (after two fixes)

A tenth check, **B5.10 recovery after clearing a fault**, was added later in
response to a user report — see below. It also passes, bringing the suite to 14.

Run via `python -m scripts.hardening_checks`.

| # | Condition | Result |
| --- | --- | --- |
| 1 | Power rises monotonically with throttle | **PASS** — sea level 0.2→1.0 throttle: 3.89 → 11.69 → 22.17 → 33.89 → 42.43 → 53.07 kW. Also verified at a **pinned 2600 RPM**: −1.26 → 11.82 → 28.42 → 43.76 → 60.51 kW. |
| 2 | Power falls with altitude at fixed throttle | **PASS** — 0.85 throttle: 45.16 (SL) → 43.31 (1 km) → 38.02 (2 km) → 29.02 (3.5 km) → 21.15 (5 km) → 12.66 kW (7 km). |
| 3 | EGT rises when AFR is pushed lean | **PASS after two fixes** — see below. |
| 4 | Oil pressure falls as oil temp rises at fixed RPM | **PASS** — 2600 RPM: 520.0 (60 °C) → 473.3 → 363.6 → 293.3 → 245.5 kPa (140 °C). |
| 5 | Oil pressure falls further with bearing wear / pump degradation | **PASS** — healthy 386.3 → 178.3 kPa (`bearing_wear` 0.8) / 176.2 kPa (`oil_pump_degradation` 0.8). |
| 6 | CHT rises with `cooling_degradation`, faster at higher throttle | **PASS** — Δ = +78.6 °C at 0.4 throttle, +118.6 at 0.7, +140.2 at 1.0. Monotonic in throttle, as required. |
| 7 | Vibration RMS rises smoothly, not instantly | **PASS** — 0.1042 → 0.2699 g over a 45 s ramp; largest single-tick jump 0.0135 g = **8.2 %** of the total rise; reaches 50 % of the rise at t+22.6 s. |
| 8 | No signal ever NaN / inf / negative | **PASS** — **14 400 frames** across a full mission plus one run per fault type (11 faults), checking RPM, MAP, boost, CHT, oil temp/pressure, fuel flow, per-cylinder EGT and vibration, COV, battery voltage, BSFC, health, reliability. **Zero violations.** |
| 9 | Twin output never responds to fault injection | **PASS, bit-exact** — see below. |

#### B5.9 — digital-twin isolation (the load-bearing claim)

Method: run a simulation to steady state, then `deepcopy` it into two identical branches.
One branch gets `bearing_wear` + `cooling_degradation` + `misfire` all injected at
severity 1.0; the other gets nothing. Step both 900 ticks (90 s) and compare the twin's
13 deterministic channels every tick.

> **max |twin_faulted − twin_healthy| over 90 s = 0.000e+00** across every channel,
> while the real engine moved oil pressure 382.9 → 111.9 kPa and CHT 166.7 → 195.5 °C.

Not "small" — **identically zero**. The twin is provably unaffected.

#### FIX 1 — the EGT-vs-AFR peak was not where the model said it was

`afr_peak_egt` was documented as "AFR at which EGT peaks" and set to 16.0. The model's
*net* EGT actually peaked at **AFR ≈ 14.5**, because `_egt_afr_shape` multiplied a wide
bell on top of an exhaust-temperature rise that already falls monotonically as
`1/(AFR+1)` — the monotonic term dominated and dragged the peak two full AFR units rich
of where the parameter claimed it was.

This was not cosmetic. `afr_command_max = 16.5` is the optimizer's lean authority and was
commented "just lean of the EGT peak at `afr_peak_egt`". It actually sat ~2 AFR units
*lean* of the true peak, in a region where the model reports EGT **falling** as the
mixture is leaned further — so an EGT-limited optimization could lean past peak and read
that as improving its EGT margin.

**Fix** (`engine_model.py`): `_egt_afr_shape` now divides out both AFR-dependent terms the
caller already applies — the charge-mass ratio and the off-stoichiometric combustion
penalty — and returns the bell itself. The net rise is then exactly `bell(AFR)` times the
combustion-quality factors that are *not* mixture-related (spark, timing, misfire), which
keep modulating EGT independently. `afr_peak_egt` retuned to 15.0 (slightly lean of
stoichiometric, matching aero practice) and `afr_egt_width` to 7.3 so the rich-of-peak →
peak rise is ~8 %, about the 100 °F published mixture sweeps show.

Verified: net EGT peak now measured at **AFR 14.92 against a declared 15.0** — 0.08 AFR
error, the residue being RPM feedback rather than the AFR terms. Absolute EGT calibration
is preserved (healthy steady state 713–740 °C across the altitude range, inside the
400–850 °C healthy band).

#### FIX 2 — a per-cylinder fault applied engine-wide (the serious one)

`spark_degradation` is in `CYLINDER_LOCALISED_FAULTS`, but its work-extraction penalty was
applied to a single engine-wide `eta_thermal`, which the per-cylinder EGT calculation then
used for *every* cylinder. Because exhaust heat is `eta_comb_i · (1 − eta_thermal)`,
inflating `(1 − eta_thermal)` globally heated all four cylinders.

Measured before the fix, at severity 1.0, throttle 0.75:

| | affected cylinder | three healthy cylinders | brake power |
| --- | --- | --- | --- |
| before | **−102 °C** | **+134 °C each** | 33.5 → 19.3 kW (−42 %) |
| after | **+46 °C** | −2.0 °C (RPM feedback only) | 33.5 → 29.8 kW (−11 %) |

The signature was inverted *and* attributed to the wrong hardware, and brake power was
over-penalised roughly fourfold because all four cylinders paid one cylinder's loss.

**Fix:** `eta_thermal` is now computed per cylinder, so the spark penalty lands only on
the cylinder with the weak plug. This also corrected the power over-penalty as a
consequence.

A second, related correction: even once localised, the affected cylinder still ran
*cooler*, because `f_spark_eta_comb_loss = 0.30` (unburned fuel) outweighed
`f_spark_eta_thermal_loss = 0.26` (late burn). That contradicted the parameter's own
comment — *"late burn → less work, hotter EGT"* — and the mag-check signature every pilot
is taught. `spark_degradation` models a plug that still lights the mixture but lights it
late; whole dropped cycles are the separate `misfire` fault. `f_spark_eta_comb_loss`
reduced to **0.12**.

The two faults are now cleanly separable by the *sign* of the EGT deviation on the same
cylinder — a genuine improvement in diagnosability:

* `spark_degradation` @ 1.0 → affected cylinder **+46 °C**
* `misfire` @ 1.0 → affected cylinder **−335 °C**

Visible in the regenerated `scripts/output/02_temperatures.png`: after injection only the
faulted cylinder diverges upward; the other three stay together.

#### On the EGT question as originally posed

The stated condition was "EGT rises when AFR is pushed lean". The precise truth, now
asserted by the harness in three parts:

* **B5.3a** Leaning toward peak raises EGT, and the net peak sits at `afr_peak_egt`
  (689.5 °C @ AFR 12.40 → 767.8 °C @ AFR 14.92).
* **B5.3b** Both named faults raise EGT on the affected cylinder over the range where they
  lean the charge toward peak — `fuel_injector_clog` @ 0.3: **+36.5 °C**;
  `spark_degradation` @ 0.9: **+43.1 °C**.
* **B5.3c** Past peak it correctly comes back down. A severe injector clog (0.9) drives
  that cylinder ~3.5 AFR *lean of peak*, so it reads **−85.5 °C** — cooler than its
  neighbours, with a 97.5 °C spread still flagging it plainly.

**Worth knowing for the demo:** the API's default injection severity is 0.8. At that
severity `fuel_injector_clog` shows the affected cylinder running *cool*, not hot. That is
correct physics, and the docstring now says so, but it is the opposite of what someone
expecting "clog → hot cylinder" will look for. Peak EGT rise occurs around severity
**0.3**.

### B5.10 — recovery after clearing a fault — **PASS** (added after user report)

**This was a gap in my own testing.** Every check in this report injected faults and
watched them develop; **none of them ever cleared one.** A user reported that RUL and
mission reliability "do not increase again" after removing an injected fault. They
investigated the one path I had not.

Verified: **every fault type does recover** — health returns above 95, RUL returns to
null, recommendation returns to GO. But the time it takes splits sharply in two, and for
three faults it is slow enough to look broken.

Simulated seconds after `POST /control/clear-fault` (severity itself reaches zero in 8 s
in every case):

| Fault | → GO | health > 95 | RUL null |
| --- | --- | --- | --- |
| injection_timing_drift | 1 s | 1 s | 1 s |
| spark_degradation | 1 s | 9 s | 79 s |
| turbo_wear | 9 s | 14 s | 1 s |
| bearing_wear | 10 s | 15 s | 1 s |
| oil_pump_degradation | 10 s | 15 s | 1 s |
| air_filter_clog | 11 s | 16 s | 1 s |
| misfire | 13 s | 17 s | 35 s |
| battery_alternator_degradation | 13 s | 18 s | 1 s |
| **fuel_injector_clog** | 37 s | **319 s** | 65 s |
| **piston_ring_wear** | 29 s | **485 s** | 109 s |
| **cooling_degradation** | **257 s** | **1080 s** | 268 s |

**This is not a logic bug — it is real thermal inertia, and the numbers check out.** At
the moment `cooling_degradation` is cleared the cylinder head is **80 °C hotter than the
twin**. Traced directly, the CHT residual decays 80.3 → 68.5 → 55.2 → 44.1 → 35.1 → 27.1
→ 20.4 → 15.4 → 11.6 °C at 60 s intervals — a clean exponential with **τ ≈ 3.7 min**,
which for a 26 kJ/K head implies a cooling conductance of ~119 W/K. That is the right
order for a cruising air-cooled head. Getting an 80 °C excursion back under the anomaly
gate takes about four time constants. The engine really is still too hot; the model is
telling the truth.

Neither filter in the PHM chain contributes meaningfully: the residual monitor is a 6 s
EWMA and the anomaly detector a 4 s EWMA, both symmetric.

**Why it reads as "stuck" to an operator, which is the legitimate complaint:**

* All recovery times are in **simulated** seconds. At the dashboard's default **1x**,
  `cooling_degradation` takes **3.7 real minutes** to return to GO and ~18 to reach full
  health. At 5x that is 45 s; at 20x, 11 s.
* In the first ~60 s at 1x the numbers barely move — health 44.3 → 50.9, RUL pinned at
  0.0. Anyone watching for half a minute concludes nothing is happening.
* The ControlDeck button flips back to "tap to inject" within 8 s, because
  `active_faults` correctly empties. So the UI says the fault is *gone* while the
  reliability card says **NO-GO**, with nothing on screen explaining that the engine is
  simply cooling down.

**The genuine defect here is the missing explanation, not the numbers.** There is no
"recovering" state anywhere in the UI. Adding one is a new UI affordance, which this
sprint's scope excludes, so it is **not fixed** — see *Known risks*. `scripts/hardening_checks.py`
now carries `c10_recovery()` so a real regression in the recovery path is
distinguishable from this honest thermal lag.

*(My first version of this check reported `misfire` and `spark_degradation` as failing to
recover. That was the check's own flaw — it stopped as soon as health and the
recommendation recovered, before RUL had nulled, which trails both. Corrected to wait for
all three.)*

### B7. Numerical stability at maximum time acceleration — **PASS**

Full 930 s mission flown at 1x, 5x and 20x, healthy and with two ramped faults.
At 20x each 100 ms tick advances 2.0 s of simulated time = 100 fixed 20 ms sub-steps.

| Scale | Ticks | Wall clock | Realtime headroom | Worst single tick (budget 100 ms) | Finite / non-negative |
| --- | --- | --- | --- | --- | --- |
| 1x | 9300 | 98.8 s | 9.4x | 83.9 ms | yes |
| 5x | 1860 | 18.6 s | 50.1x | 24.6 ms | yes |
| 20x | 465 | 10.5 s | **88.7x** | **31.3 ms** | yes |

**Trajectory agreement, 1x vs 20x**, linearly interpolated onto a common simulated-time
grid (174 points):

| Signal | max \|1x − 20x\| | median |
| --- | --- | --- |
| RPM | 11.50 rpm | 2.85 |
| CHT | 2.80 °C | 0.80 |
| EGT | 8.25 °C | 1.93 |
| Oil pressure | 12.00 kPa | 2.77 |
| Oil temp | 1.70 °C | 0.30 |
| Boost | 1.59 kPa | 0.35 |
| Health | 1.90 pts | 0.10 |

**No divergence. No oscillation growth** — mean tick-to-tick |ΔRPM| in late cruise is
3.36 / 3.26 / 3.38 rpm at 1x / 5x / 20x, i.e. flat. **The loop never falls behind**: worst
tick at 20x uses 31 % of its budget. No sub-step or integration-method change was needed.

> **Methodology note, because it nearly produced a false alarm.** A first pass compared
> the runs with zero-order-hold sampling over the *whole* mission and reported a 1785 rpm
> discrepancy. All of it sits at t = 0.0 s: during engine start RPM goes ~700 → 2700 in
> about a second, and at 20x there is only one sample every 2 s, so the two runs simply do
> not have samples at the same instants. That measures the sampling, not the integrator.
> Interpolating and skipping the first 60 s gives the table above. The harness now does
> this by default, with the reasoning recorded in `resample()`'s docstring.

### Also re-run

`scripts/validate_physics.py` in both modes. The **rapid-throttle-transition scenario**
(20 % → 100 % → 20 %) confirms the dynamics are dynamics, with the time-constant ordering
intact:

```
boost pressure     1.10 s      EGT (mean)      3.30 s
manifold pressure  0.50 s      oil temperature 10.70 s
RPM                0.60 s      CHT            13.90 s
post-transient RPM: mean 1467, peak-to-peak 16 (stable)
```

All 8 plots regenerated and inspected. Turbo lag is visible on the step *down* (boost
decays over ~2 s while MAP drops immediately), rotational inertia is visible as an RPM
ramp, and the COV(IMEP) transient-hold logic behaves as documented.

**One documentation discrepancy, not fixed:** `run_throttle_transient`'s docstring says
"EGT follows within seconds …, CHT over tens of seconds, **oil slower still**". Measured,
oil (10.7 s) is *faster* than CHT (13.9 s). Both numbers are dominated by very small
excursions — CHT moves only ~3 °C across the step because the cooling-flap schedule opens
with throttle, so more heat and more cooling largely cancel. The ordering claim in that
comment is unreliable at this excursion size; the *model* is fine, the sentence
overstates. Flagged rather than retuned, since changing the flap schedule to make the
comment true would be a physics change made for a comment's sake.

---

## Part C — async loop / concurrency — **8 / 8 PASS**

### C8. 16-minute continuous run — **PASS**

One socket held open for 16 real minutes while the simulation, twin and broadcast ran
continuously.

```
8507 frames, 8.86 Hz
inter-frame gap: median 112 ms, p99 123 ms, max 153 ms
frame-timestamp vs wall-clock drift over the whole run: +1 ms
```

**+1 ms of drift over 16 minutes.** The median gap was 112 ms at t+2 min and 113 ms at
t+14 min — no creep. The loops do not block each other and do not desynchronise.

**Observation (not a bug, but worth knowing):** the stream runs at **~8.9 Hz, not the
configured 10 Hz**. `run_simulation` does `await asyncio.sleep(settings.tick_seconds)`
and *then* does the tick's work, so the period is 100 ms + compute rather than a fixed
100 ms. Simulated time stays correct — the tick integrates the measured `wall_dt`, which
is why drift is +1 ms — so this only affects the frame *rate*, not the physics. Left
alone: making it a compensating scheduler is a behaviour change, not a bug fix.

### C9. Connection churn — **PASS**

**300 sockets opened and closed in 0.5 s** (12 waves of 25 concurrent).

```
broadcast rate 9.17 Hz before  ->  9.17 Hz after
backend still serving frames: True
```

No crash, no leaked connections, no measurable slowdown.

#### FIX 3 — frame-dropping race in the broadcast loop

`ConnectionManager.broadcast_json` iterated `self.active` directly while `await
ws.send_json(...)` yields. During that await, the endpoint coroutine for a *different*
socket can notice its client is gone and call `disconnect()`, which removes an entry from
the same list. Mutating a list while a `for` walks it by index makes the loop skip
whichever element shifts into the vacated slot — a still-connected client silently misses
that frame. Rapid tab open/close is exactly the workload that triggers it.

**Fix:** iterate a snapshot (`for ws in list(self.active)`). One line, with the reasoning
recorded in the comment.

### C10. Kill the frontend mid-session — **PASS**

Driven in real headless Chrome over CDP. Loaded the dashboard, confirmed status pill
`Live` with numbers changing, then killed `next-server`.

```
BEFORE  backend healthy=True pids=['17575']   status='Live'   numbers changing=True
kill    frontend HTTP='000' (down)            backend healthy=True pids=['17575']
restart frontend HTTP='200'
AFTER   status='Live'  canvas=True  numbers changing=True
        backend healthy=True pids=['17575']
```

**Same backend PID throughout — never restarted.** The dashboard reconnected on its own
and resumed live telemetry. `ReconnectingSocket`'s capped exponential backoff (500 ms →
8 s, reset on open) works as designed.

### C11. Mission and replay transition churn — **PASS (6 sub-checks)**

* Mission start → end leaves `recording=False`. **PASS**
* **10 back-to-back start/end cycles** — zero errors, clean final state. **PASS**
* Duplicate start and duplicate end both return **409, not 500**. **PASS**
* **8 rapid replay start/stop cycles** — `replay.active=False` afterwards and
  **0 of 27 frames** in the following 3 s carried `is_replay`. **No orphaned
  broadcaster.** **PASS**
* Replay genuinely replays (40/45 frames flagged) and stopping it returns the socket to
  live physics (27/27 frames live). **PASS**
* Replay of an unknown mission returns **404, not 500**. **PASS**

---

## Part D — ML pipeline, real numbers

### D12. Retrained from scratch — **accuracy 99.70 %**

Both scripts re-run from nothing after the physics fixes (the fixes change the training
distribution, so this was mandatory, not optional):

```
python -m app.ml.train.generate_training_data --episodes 12
  -> 40 139 samples x 54 features, 15 classes, 168 episodes
python -m app.ml.train.train_classifier
```

The shipped split holds out **whole episodes**, which is the honest thing to do —
consecutive 100 ms samples of one slowly-ramping fault are near-duplicates, and splitting
them randomly reports a meaningless ~100 %. That single-split run gives 99 % accuracy, but
with only 42 test episodes some classes get one test episode, so per-class figures rest on
a single fault ramp.

To get properly-supported numbers I ran **6-fold GroupKFold** so every one of the 168
episodes is tested exactly once (`scripts/classifier_report.py`).

```
GroupKFold accuracy: mean 0.9970   min 0.9843   max 1.0000
Pooled over all 40 139 out-of-fold samples: 0.9970
```

Per class, pooled out-of-fold:

| Class | Precision | Recall | F1 | Support |
| --- | --- | --- | --- | --- |
| air_filter_clog | 1.000 | 1.000 | 1.000 | 1792 |
| battery_alternator_degradation | 1.000 | 1.000 | 1.000 | 1777 |
| bearing_wear | 0.999 | 1.000 | 1.000 | 1777 |
| cooling_degradation | 1.000 | 0.999 | 1.000 | 1766 |
| egt_sensor_drift | 1.000 | 1.000 | 1.000 | 1800 |
| **fuel_injector_clog** | **0.944** | 1.000 | 0.971 | 1776 |
| healthy | 0.999 | 1.000 | 1.000 | 15120 |
| injection_timing_drift | 1.000 | 1.000 | 1.000 | 1798 |
| misfire | 1.000 | 1.000 | 1.000 | 1789 |
| oil_pressure_sensor_noise | 1.000 | 1.000 | 1.000 | 1799 |
| oil_pump_degradation | 1.000 | 1.000 | 1.000 | 1792 |
| **piston_ring_wear** | 1.000 | **0.941** | 0.970 | 1791 |
| rpm_sensor_stuck | 0.999 | 0.992 | 0.995 | 1779 |
| spark_degradation | 1.000 | 1.000 | 1.000 | 1783 |
| turbo_wear | 1.000 | 0.999 | 1.000 | 1800 |

**Every off-diagonal confusion in the entire pooled matrix** — there are only four:

| Confusion | Count | % of that class |
| --- | --- | --- |
| `piston_ring_wear` → `fuel_injector_clog` | 105 / 1791 | **5.9 %** |
| `rpm_sensor_stuck` → `healthy` | 15 / 1779 | 0.8 % |
| `turbo_wear` → `bearing_wear` | 1 / 1800 | 0.1 % |
| `cooling_degradation` → `rpm_sensor_stuck` | 1 / 1766 | 0.1 % |

**Stated plainly: `piston_ring_wear` is the weakest class at 94.1 % recall**, and
essentially all of its error is a single confusion with `fuel_injector_clog`. Physically
reasonable — both lean the charge and lift EGT — but it is the one place the classifier
is meaningfully imperfect, and it drags `fuel_injector_clog`'s precision to 0.944.

#### Physical-fault vs sensor-fault confusion (the key claim)

| truth \ predicted | healthy | physical | sensor | total |
| --- | --- | --- | --- | --- |
| healthy | 15120 | 0 | 0 | 15120 |
| physical | 0 | 19640 | **1** | 19641 |
| sensor | 15 | **0** | 5363 | 5378 |

* Physical fault called a **sensor** fault: **1 / 19 641 = 0.005 %**
* Sensor fault called a **physical** fault: **0 / 5378 = 0.000 %**

**The claim holds.** In 25 019 fault samples the physical/sensor boundary was crossed
exactly once. The 15 `rpm_sensor_stuck` → `healthy` errors are misses, not
misattributions — the system fails to notice, it does not blame the wrong thing.

> **Caveat, stated because it matters:** this is simulated data scored against the same
> physics the twin uses. The classifier is being asked to separate signatures that were
> generated by the model it is implicitly inverting. Real-hardware accuracy will be lower,
> and 99.7 % should be presented as "separable in simulation", not as a field number.

### D13. RUL and mission reliability across degradation rates — **6 / 6 PASS** (after two fixes)

`bearing_wear` to severity 0.95 at three ramp rates — slow (900 s), medium (240 s), fast
(45 s) — injected 4 minutes into a 26-minute mission.

```
seconds from injection until RUL reaches 0:   fast 56 s   medium 124 s   slow 395 s

RUL at matched time since injection:
   +90 s :  slow 48.9   medium  7.2   fast 0.0
  +150 s :  slow 11.5   medium  0.0   fast 0.0
  +240 s :  slow  3.4   medium  0.0   fast 0.0

first CAUTION (min):  fast 4.50   medium 5.27   slow 6.67
first NO-GO   (min):  fast 4.67   medium 5.48   slow 7.40
```

Faster degradation shortens RUL and flips the recommendation sooner, monotonically, on
every measure. A healthy engine reports **null RUL in 520/520 samples** and stays **GO**
throughout — no fabricated numbers.

#### FIX 4 — RUL was catastrophically optimistic on fast faults

The reported RUL is an EMA of the fitted estimate, seeded at the `max_minutes` clamp (600)
the first time a trend becomes fittable, with a symmetric 20 s time constant. A 20 s
constant cannot come down from 600 faster than a fault can develop.

Measured on the 45 s ramp:

> health **40.2** (at the failure threshold) — underlying fit said **0.0 minutes**,
> the frame reported **409 minutes**.

The faster the collapse, the more optimistic the readout — precisely backwards for a
safety monitor.

**Fix** (`rul_predictor.py`): the smoothing is now **asymmetric**. A *rising* RUL is still
damped with the 20 s constant (that is what stops the readout flickering, which is why
the smoothing exists); a *falling* one uses a 3 s constant. Lag is acceptable when the
news is getting better; it is not when it is getting worse. Same scenario now reports
**5.9 min** against a raw 0.0, and the slow ramp tracks the raw fit within ~15 %
throughout.

#### FIX 5 — a legitimate RUL of zero was discarded as falsy

Worst-subsystem selection read:

```python
if best is None or estimate.minutes < (best.minutes or math.inf):
```

`0.0 or math.inf` evaluates to `math.inf`. So the instant a subsystem correctly reported
**0 minutes to failure**, any healthier subsystem's longer estimate replaced it. Observed
live: the readout climbed from 1.0 min back up to **15.4 min** at the exact moment the
lubrication HI crossed the failure threshold.

`best.minutes` can never be `None` at that point — the loop `continue`s on `None` two
lines above — so the guard bought nothing and cost correctness. **Fix:** compare
`best.minutes` directly. The readout now holds at 0.0 once past the threshold.

#### Known limitation (not a bug, not fixed)

The RUL fit uses a fixed 5-minute least-squares window. A fault that develops much faster
than that window cannot be resolved until the window fills with post-fault data: at the
moment a 45 s collapse passes health 85, the window is still ~99 % healthy history, so the
estimate is uninformative. Measured at matched *time since injection* the ordering is
correct, and time-to-RUL-zero orders correctly (56 / 124 / 395 s) — but a comparison at
matched *health* looks inverted for this reason. Fixing it properly means an adaptive or
multi-scale window, which is a design change beyond this sprint. The GO/CAUTION/NO-GO
recommendation, which is what an operator acts on, flips correctly and promptly in all
three cases (NO-GO within 40 s of injection on the fast ramp).

---

## Part E — API and input robustness

### E14. Malformed and out-of-range input — **72 / 72 PASS** (after two fixes)

60 malformed cases plus 12 valid controls, across every `/control/*`, `/simulate/*` and
`/optimize/*` route. First run: **58 / 72**.

#### FIX 6 — control endpoints silently clamped out-of-range commands

Fourteen cases returned **200 OK** having quietly clamped the input:

| Request | Was | Now |
| --- | --- | --- |
| `throttle {"value": 5.0}` | `200 {"throttle": 1.0}` | **422** |
| `throttle {"value": -3.0}` | `200 {"throttle": 0.0}` | **422** |
| `throttle {"value": NaN}` | `200 {"throttle": 1.0}` | **422** |
| `fault {"severity": 9.0}` | `200`, echoing `target_severity: 9.0` | **422** |
| `fault {"severity": -2.0}` | `200` | **422** |
| `fault {"ramp_seconds": -10}` | `200` | **422** |
| `sensor-fault {"severity": 50}` | `200` | **422** |
| `time-scale {"factor": 0 / -5 / 1e9}` | `200`, clamped to 0.1 / 0.1 / 50 | **422** |
| `ambient-temperature 5000 / -400 °C` | `200`, accepted | **422** |
| `setpoint {"afr_trim": 500}` | `200`, echoing `afr_trim: 500.0` | **422** |
| `setpoint {"injection_timing_trim_deg": 900}` | `200` | **422** |

The `setpoint` cases were the worst: the response echoed back a setpoint the engine was
never going to run. A caller sending a percentage where a 0–1 fraction was expected got
full throttle and no indication anything was wrong.

**Fix** (`core/models.py`): real `Field` bounds on every request model. The bounds are
**sourced from `PARAMS`**, not restated as literals, so widening the ECU's trim authority
or the modelled weather envelope cannot leave the API validating against stale numbers:

```python
afr_trim: Optional[float] = Field(default=None, ge=_P.afr_trim_min, le=_P.afr_trim_max)
ambient_temperature_c: Optional[float] = Field(
    default=None, ge=_P.scenario_ambient_min_c, le=_P.scenario_ambient_max_c)
```

#### FIX 7 — any NaN in a request produced a 500

`throttle {"value": NaN}` returned **500 Internal Server Error**. The cause was not the
validation — Pydantic rejected it correctly — but the error *response*: FastAPI's 422 body
echoes the offending input back, and serialising `nan` raises
`ValueError: Out of range float values are not JSON compliant`. So a correctly-rejected
request died serialising its own rejection, and the caller saw a bare 500 with no
explanation. This affected any non-finite float on any endpoint.

**Fix** (`main.py`): a `RequestValidationError` handler that recursively replaces
non-finite floats before serialising. Now:

```
HTTP 422
{"detail":[{"type":"less_than_equal","loc":["body","value"],
            "msg":"Input should be less than or equal to 1","input":"nan","ctx":{"le":1.0}}]}
```

**Already correct before this session** (no change needed): every `/simulate/scenario` and
`/optimize/*` bound, unknown fault/sensor-fault/phase/scenario/preset names, unknown
mission IDs (404), negative and absurd altitudes, ISA-deviation limits, waypoint bounds,
label length, and pagination limits. The scenario endpoint's 400 body carries *reasons*
plus the validity envelope, which is genuinely better than a bare message.

### E15. Optimizer hard safety bounds — **14 / 14 PASS**

Three health states at 2400 m / 25 °C, all four objectives.

**The substantive case** — a worn engine (`bearing_wear` 0.35, `cooling_degradation` 0.30,
`turbo_wear` 0.25) where the book cruise setting is *already illegal*:

| | throttle | power | CHT | oil temp | oil pressure | feasible |
| --- | --- | --- | --- | --- | --- | --- |
| book cruise (78 %) | 78 % | 24.72 kW | 212.9 °C | 117.2 °C | 164.0 kPa | **False** |
| `max_power` recommendation | 78.6 % | 22.99 kW | **137.6 °C** | **98.5 °C** | **216.5 kPa** | **True** |

This is the exact question asked — and the answer is that it is **genuinely more
conservative, not just a smaller number**: CHT down 75 °C, oil temp down 19 °C, oil
pressure *up* 52 kPa and back inside its limit. Every hard limit satisfied.

**Limit derating is real**, not cosmetic — a degraded engine gets a pulled-in envelope:

```
cht_limit_c 230 -> 208 | egt_limit_c 850 -> 832
oil_temp_limit_c 130 -> 122 | oil_pressure_min_kpa 200 -> 225
```

**Pristine vs badly degraded, `max_power`:** throttle 100 % → 79.6 %, power 41.35 →
13.43 kW, CHT 193.8 → 171.1 °C. Conservative on every axis.

**On the badly-degraded engine the optimizer reports `feasible=False`** — and it is right
to. I brute-forced **1155 setpoints** across the whole search box: the best achievable oil
pressure anywhere is 15.0 kPa (the lubrication model's floor) against a 225 kPa
requirement. **No feasible setpoint exists.** The optimizer says so in plain language
rather than dressing up a violating point:

> "No setpoint in the search space keeps oil pressure inside its 225 kPa limit — the
> closest point still sits 210 kPa below it."
> "This is the least-unsafe point found, **NOT a recommendation**. The engine should not
> be dispatched at these conditions in this condition."

The bound that actually matters — *a limit-breaching point is never presented as safe* —
holds for all four objectives at all three health states. Verified 14 ways.

*(My first version of this check asserted "the recommendation always satisfies every
limit" and failed. That assertion was wrong, not the code: on a wrecked engine no setpoint
satisfies every limit, and refusing to return anything would be less useful than returning
the closest point clearly labelled infeasible.)*

**Observation:** the optimizer drives exactly to the constraint boundary — the worn-engine
recommendation sits at oil pressure 216.5 vs a 216.5 kPa limit, margin **+0.0**. Correct
for a constrained optimizer, but there is no margin buffer: any modelling error puts it
over. Worth a deliberate decision before flight, not before a demo.

---

## Part F — 3D visualisation performance

### F16. Profiled in real Chrome over CDP — **PASS on the design question**

No new dependencies: raw CDP against the system `google-chrome`, driving the live
dashboard against the live backend.

**No React re-render per frame — confirmed by measurement.** A `MutationObserver` on the
canvas subtree across ~6 s (≈53 telemetry frames):

```
{"canvasMut": 0, "docMut": 6087}
```

**Zero mutations inside the canvas.** The 3D scene never reconciles through React. Static
review agrees: every one of the nine `engine3d` components reads
`useTelemetryStore.getState()` *imperatively inside `useFrame`* and mutates refs — none
uses the subscribing `useTelemetryStore(selector)` form. `EngineLabelsOverlay` is the one
exception and is deliberate: it is DOM, sits outside the Canvas, and polls on an interval.

**Fault injection is not a performance problem.** Interleaved A/B/A/B (healthy → 4 severe
faults → healthy → …, three cycles) so session drift is separable from the fault effect:

```
healthy mean 19.0 fps    faulted mean 18.6 fps    fault cost: 2.1 %
```

All overlays firing at once — misfire, bearing wear, cooling degradation, plus a sensor
fault — costs about **2 %**. Nothing to fix.

**No leak.** A 4.5-minute continuous session sampling every 20 s:

```
rAF fps: 19.6 -> 19.6  (-0.2 %)
JS heap MB: 131 ... 226 ... 109 ... 210   (sawtooth, floor does not rise)
```

Frame rate is **flat**, and the heap sawtooths between 109 and 226 MB with the floor *not*
growing — normal GC behaviour, not a leak. An apparent −15 % decline in an earlier
short-session measurement was warm-up noise; it did not reproduce over the longer run.
The store's frame buffer is correctly bounded (`BUFFER_SIZE = 600`).

**The absolute FPS number is not meaningful and should not be quoted.** This environment
has no GPU, so Chrome ran on SwiftShader — a *software* rasteriser. ~19 fps is the CPU
rendering the scene pixel by pixel. On any machine with real GPU acceleration this will be
substantially higher. **What I could not do in this environment is measure
GPU-accelerated FPS**, so if a target frame rate matters for the demo, measure it on the
actual demo machine. See *Known risks*.

---

## All fixes, in one place

| # | File | Bug | Severity |
| --- | --- | --- | --- |
| 1 | `physics/engine_model.py`, `core/engine_params.py` | Net EGT-vs-AFR peak at 14.5 while `afr_peak_egt` declared 16.0; optimizer's lean authority sat lean of the true peak where the model reports EGT falling | Physics + optimizer safety model |
| 2 | `physics/engine_model.py`, `core/engine_params.py` | Cylinder-localised spark fault applied engine-wide: signature inverted and put on the 3 healthy cylinders (+134 °C) while the faulty one read −102 °C; power over-penalised 4x | **Serious** |
| 3 | `api/ws_telemetry.py` | Broadcast iterated a list that concurrent disconnects mutate — connected clients silently dropped frames | Correctness |
| 4 | `ml/rul_predictor.py` | Symmetric EMA seeded at the 600-minute clamp: reported 409 min RUL on an engine at its failure threshold; worse the faster the fault | **Safety-inverting** |
| 5 | `ml/rul_predictor.py` | `(best.minutes or math.inf)` discarded a legitimate RUL of 0.0 as falsy; readout jumped *up* at the failure threshold | **Safety-inverting** |
| 6 | `core/models.py` | 14 control endpoints silently clamped out-of-range input and returned 200, echoing values the engine would never run | API contract |
| 7 | `main.py` | Any NaN in a request body → 500, because the 422 error body could not serialise the echoed NaN | API contract |
| — | 3 frontend components | Stale/false `TODO(phase-2)` comments claiming backend ML output was a frontend heuristic | Documentation |
| — | `frontend/hooks/useTelemetryStream.ts` | See below | **Feature broken end-to-end** |

### The auth bug (found outside the numbered checklist)

With `TELEMETRY_AUTH_ENABLED=true`, the REST controls authenticate correctly (they send
`Authorization: Bearer`), but `useTelemetryStream.ts` opened the WebSocket with **no
token at all**. Verified against a real auth-enabled backend:

```
REST no token      -> 401      WS as the frontend opens it  -> REJECTED (1008)
REST bad token     -> 401      WS with ?token=<secret>      -> CONNECTED
REST good token    -> 200
```

The result would be a dashboard that renders, whose buttons work, and whose telemetry sits
in "reconnecting" **forever**, with nothing indicating a token is the problem. The backend
already accepts `?token=` precisely because browsers cannot set headers on a WebSocket
handshake (`core/security.py` documents this); the frontend simply never used it.

**Fix:** `authorizedUrl()` appends the URL-encoded token when one is configured, leaving
the URL untouched in the default open-demo configuration. Verified: the exact URL shape the
fixed code produces now connects and receives frames.

---

## Known risks — what is NOT fixed or NOT verified

Stated plainly, so nothing here is a surprise before the demo.

### Not fixed

1. **5 high-severity npm advisories in `next@14.2.35`** (SSRF, cache poisoning, DoS, path
   traversal, plus bundled `postcss` path traversal). The fix is `next@16`, two majors up
   and a breaking change. Out of scope for a hardening sprint, and irrelevant to a local
   demo, but it is a real finding for anything internet-facing. `three-mesh-bvh@0.7.8` is
   also flagged incompatible with the pinned three.js.

2. **`piston_ring_wear` recall 94.1 %**, essentially all of it confused with
   `fuel_injector_clog` (105/1791). Physically reasonable — both lean the charge and lift
   EGT — but if the demo injects piston ring wear, expect roughly a 1-in-17 chance the
   classifier names the wrong fault. Every other class is ≥ 99.2 %.

3. **RUL cannot resolve a fault faster than its 5-minute fit window.** Ordering and
   time-to-zero are correct, and the GO/CAUTION/NO-GO recommendation is prompt, but the
   RUL *number* during the first window of a very fast collapse is optimistic. A proper
   fix is an adaptive or multi-scale window — a design change.

4. **The optimizer returns points with exactly zero margin** on the binding constraint
   (oil pressure 216.5 vs a 216.5 kPa limit). Correct constrained-optimizer behaviour; a
   real system would want a deliberate margin buffer. Nobody has decided that it should
   not have one.

5. **CHT-vs-mixture is monotonic where a real engine is peaked.** `head_split` scales as
   `(AFR/14.7)^3.6` without limit, so leaning *past* peak keeps heating the head, whereas a
   real engine cools it lean-of-peak (the whole point of LOP operation). This biases the
   optimizer toward rich — conservative, so not a safety inversion — but it means the model
   cannot represent LOP cooling. Not fixed: it is a modelling gap, not a defect, and
   correcting it changes the live simulation's thermal behaviour.

6. **Telemetry streams at ~8.9 Hz, not the configured 10 Hz** (fixed sleep plus compute
   time). Simulated time is unaffected. Left alone deliberately.

7. **`run_throttle_transient`'s docstring claims oil is slower than CHT**; measured, it is
   faster (10.7 s vs 13.9 s). Both excursions are small enough that the ordering is not
   robust. Comment overstates; model is fine.

8. **Nothing in the UI indicates the engine is *recovering* after a fault is cleared.**
   The fault chip clears in 8 s while mission reliability can sit at NO-GO for minutes
   (up to 3.7 real minutes at 1x for `cooling_degradation`, ~18 for full health) while the
   cylinder head genuinely cools. The numbers are right; the absence of any "cooling
   down / recovering" signal is what makes it read as frozen. Fixing it means a new UI
   state, which is outside this sprint. **For a demo, clear faults at 5x or 20x** — the
   same recovery takes 45 s or 11 s of wall clock.

9. **`VibrationSpectrum` is named "spectrum" but plots RMS bars.** The spectral features
   exist server-side and are used as residual channels, but the frame contract carries only
   a scalar RMS per cylinder, so there is nothing to plot on a frequency axis. Making it a
   real FFT display requires widening the schema — a feature, not a fix.

### Verified but with caveats

10. **Classifier accuracy is measured on simulated data scored against the same physics the
   twin uses.** 99.7 % means "these signatures are separable in simulation". It is not a
   field number and should not be presented as one.

11. **Absolute 3D FPS was measured under software rendering (SwiftShader)** because this
    machine has no GPU. The ~19 fps figure says nothing about the demo machine. The
    *design* questions — no per-frame React reconciliation, no leak, negligible fault-injection
    cost — are answered and hold regardless of GPU. **Measure real FPS on the demo hardware.**

### Not tested at all

12. **`CANBusAdapter`** raises `NotImplementedError` by design. No real CAN/J1939 hardware
    path exists or was exercised.

13. **Multi-user / multi-dashboard concurrent *control*.** Connection churn and concurrent
    *viewers* were tested hard (300 sockets). Two operators issuing conflicting control
    commands simultaneously was not: all mutations run on one event loop so there is no
    data race, but there is no ownership model, and last-write-wins is untested as a
    user-facing behaviour.

14. **Sustained runs longer than 16 minutes**, and database growth over a long recorded
    mission. The 16-minute run showed +1 ms drift with no creep, but a multi-hour soak was
    not attempted.

15. **A latent thread-safety hazard in `physics/environment.py`.** `atmosphere()` caches
    its last result in two module-level globals, and scenarios run in a worker thread
    (`run_in_threadpool`) concurrently with the live simulation. A torn key/value pair
    would hand one thread another's atmosphere. I tried hard to reproduce it — 32 million
    concurrent reads across three threads at a 1 µs switch interval — and got **zero**
    cross-contaminated reads, because CPython does not check the eval breaker between the
    two `STORE_GLOBAL` opcodes. **It is not a live bug today**, and I left it alone rather
    than change working code on a theoretical argument. It would become real under a
    free-threaded (no-GIL) Python. Recorded here so it is a known, decided risk rather
    than a lurking one.

---

## How to re-run everything

```bash
cd backend
./.venv/bin/python -m scripts.hardening_checks     # 14 checks: physics sanity + recovery-after-clear
./.venv/bin/python -m scripts.stability_checks     # 1x / 5x / 20x integration stability
./.venv/bin/python -m scripts.rul_checks           # RUL + GO/CAUTION/NO-GO vs rate
./.venv/bin/python -m scripts.optimizer_safety     # optimizer hard bounds
./.venv/bin/python -m scripts.classifier_report    # 6-fold grouped CV metrics
./.venv/bin/python -m scripts.validate_physics --fault spark_degradation
./.venv/bin/python -m scripts.validate_physics --scenario throttle-transient
```

Retraining from scratch (~50 min for generation):

```bash
./.venv/bin/python -m app.ml.train.generate_training_data --episodes 12
./.venv/bin/python -m app.ml.train.train_classifier
```
