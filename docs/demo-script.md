# Demo Script (~3 minutes)

Suggested live-demo walkthrough for SIH26054 judges. Run backend + frontend side by side
(see README "Quickstart"). Presenter drives the **Control Deck** panel while narrating.

## 0:00 – 0:30 — Healthy baseline

- Open the dashboard. Let it sit on **cruise** phase for a few seconds.
- Point out: the **GO** banner (green, calm) in the Mission Header, the **HealthScoreGauge**
  sitting at ~97–100, **MissionReliabilityCard** showing a high score, **RULPanel** empty/dash
  (no RUL while healthy), and the **EngineCutaway3D** cylinders firing smoothly in sequence.
- Narrate: "This is the live digital twin of the aero piston engine — every value you see
  is streaming over WebSocket at 10Hz from the telemetry backend, mirroring what onboard
  sensors would report in flight."

## 0:30 – 1:00 — Walk the vitals

- Point at **EngineVitalsGrid**: RPM, per-cylinder EGT, CHT, oil pressure/temp, fuel flow,
  boost — all sparklining, all inside healthy (cyan/neutral) bands.
- Use the mission-phase quick-jump buttons in **ControlDeck** to jump climb → loiter, and
  note how RPM/EGT/altitude/fuel flow shift together sensibly per phase.

## 1:00 – 1:45 — Inject a fault live

- Open **ControlDeck** → fault-injection grid. Trigger e.g. `bearing_wear` at high severity
  with a short ramp (~15–20s so it's visible live).
- Narrate while it ramps: "I'm injecting a bearing-wear fault into the simulation right
  now — watch the lubrication subsystem score fall, vibration RMS climb on the cutaway and
  in the Vibration Spectrum panel, and a new entry appear in the Fault Alert Feed."
- Call out the **HealthScoreGauge** dropping, **overall_score** and `lubrication` subsystem
  score diverging from the others, and the **RULPanel** ticking on with a falling
  remaining-useful-life estimate.

## 1:45 – 2:15 — Mission reliability reacts

- As severity ramps past threshold, the **GO** banner transitions to **CAUTION** then
  **NO-GO** (color change + pulse animation) — narrate this as the moment an operator would
  be told to abort or shorten the mission.
- Point at **FaultAlertFeed**: severity value updating in place, color escalating from
  amber to red.

## 2:15 – 2:45 — Clear the fault, recover

- Hit **clear** on the active fault in ControlDeck. Narrate the ramp back to healthy: health
  scores climbing back to baseline, RUL panel clearing, banner returning to GO.
- Optionally bump **time-acceleration** to 5x/20x briefly to show the simulation can be
  sped up for demo/training purposes without changing the physics.

## 2:45 – 3:00 — Close

- Summarize: "This is Phase 1 — full dashboard, live mock telemetry, and the fault/health/
  RUL/mission-reliability pipeline all wired end to end. Phase 2 swaps this mock generator
  for a first-principles physics engine (thermal, lubrication, vibration, turbo models —
  see our physics-model doc), and Phase 3 adds the trained ML layer for anomaly detection,
  fault classification, and RUL prediction on top of it, using the exact same dashboard."

## Tips

- Rehearse the fault severity/ramp values beforehand — pick a `ramp_seconds` that fits your
  speaking pace (15–20s reads well live).
- `misfire` and `bearing_wear` are the most visually dramatic (fast RUL decay, clear NO-GO)
  — good first picks for the "wow" moment. `piston_ring_wear` or `air_filter_clog` are
  better for showing a slow-burn CAUTION state if you have extra time.
- If a question comes up about accuracy: this phase is a scripted-but-physically-plausible
  mock (sine baselines + correlated ramps) explicitly to let the dashboard be judged on its
  own merits before Phase 2's physics model lands.
