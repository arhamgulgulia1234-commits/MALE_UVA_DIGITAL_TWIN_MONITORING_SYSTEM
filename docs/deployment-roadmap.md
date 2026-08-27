# Deployment Roadmap

From the simulated prototype in this repository to a CAN-bus-fed, edge-deployed system
running against a real MALE UAV engine and a real ground control station.

The organising idea is that **most of this system does not need to change**. The physics
twin, the residual layer, the anomaly detector, the classifier, the RUL and reliability
models and the entire dashboard all operate on `RawEngineData` and `TelemetryFrame`. What
changes is where those two structures come from. That is why
`backend/app/ingestion/adapter_interface.py` exists and why it exists *now*, before there
is any hardware to talk to.

```
   TODAY                                    TARGET
   ─────                                    ──────
   SimulatedAdapter                         CANBusAdapter
   (physics sim)                            (SocketCAN / J1939 / FADEC)
        │                                        │
        └──────────► RawEngineData ◄─────────────┘
                          │
                   (unchanged from here down)
                          ▼
              digital twin + residuals
                          ▼
              anomaly / classifier / RUL
                          ▼
                   TelemetryFrame
                          ▼
                  WebSocket → dashboard
```

---

## Stage 1 — Hardware-in-the-loop on a test cell (current + next step)

**Goal:** prove the pipeline against a real engine on a bench before anything flies.

- Implement `CANBusAdapter` per the docstring in `app/ingestion/adapter_interface.py`:
  `python-can` over SocketCAN, decoding either J1939 PGNs or a vendor DBC with `cantools`.
- Run the bus reader on its own thread with a latest-value-per-signal cache. Signals
  arrive at different rates (RPM at 100 Hz, oil temperature at 1 Hz); `read_frame()` must
  snapshot, never block.
- **Populate `RawEngineData.valid` honestly.** A stale or missing signal must be marked
  invalid, not passed through as its last value or as zero. A dropped CAN frame that
  reads as "oil pressure 0 kPa" is indistinguishable from catastrophic oil loss, and the
  PHM layer will do exactly what you would expect with that input.
- Add a vibration path. Accelerometers rarely appear on CAN; expect SPI/I2S into the edge
  computer at 1–10 kHz. Fill `vibration_samples` + `vibration_sample_rate_hz` and the
  existing FFT feature extraction handles it unchanged.
- **The adapter must be strictly read-only.** Never transmit onto an engine control bus.

**Calibrate the twin against the real engine.** This is the substantial work, not the
plumbing. Every coefficient in `app/core/engine_params.py` was tuned for plausible
*relative* behaviour, not against a dynamometer. Run the real engine through a sweep of
speed/load/altitude-equivalent points, then fit: volumetric efficiency, friction
coefficients, thermal masses and conductances, the oil viscosity curve, and the
compressor map. Until this is done the residuals measure "how wrong our model is" rather
than "how degraded this engine is", and every diagnosis inherits that error.

## Stage 2 — Edge deployment on the air vehicle

**Goal:** run the twin onboard so diagnosis survives loss of the datalink.

- Target class: Jetson Orin Nano / Raspberry Pi CM4 with a CAN HAT, or the mission
  computer if it has headroom. The tick loop is light — the FFT dominates, and it is a
  1024-point transform at 10 Hz.
- Run the full twin + PHM stack onboard, not just data forwarding. A UAV that loses its
  link must still know its own engine is failing; that judgement is what triggers an
  autonomous return-to-base.
- Downlink budget: the full `TelemetryFrame` at 10 Hz is far too much for a constrained
  satcom link. Send health, RUL, reliability and advisories at ~1 Hz, raw channels at a
  reduced rate, and burst full-rate data only around an anomaly. The recording layer
  (`app/db/`) already stores everything locally for post-flight retrieval.
- Persist to onboard storage exactly as `app/db/repository.py` does now, and offload the
  SQLite file after landing. Mission replay and reporting then work unchanged on real
  flights.

## Stage 3 — Ground control station integration

- The dashboard becomes a GCS panel rather than a standalone page. It already consumes a
  single WebSocket and holds no server state, so embedding it is mostly a routing concern.
- Replace the single shared token (`app/core/security.py`) with real identity — see
  Security below.
- Fleet view: the current schema is single-airframe. Add a `vehicle_id` to the frame and
  a per-vehicle connection registry. The database schema already separates missions, so
  the change is additive.

## Stage 4 — Fleet learning

- Retrain the classifier on real labelled failures as they accumulate, rather than only on
  simulated ones. The synthetic data remains useful for classes that are rare in service.
- Compare residual trends across airframes to separate "this engine is degrading" from
  "our model is biased" — a fleet-wide common-mode residual is a modelling error, not
  fifty simultaneous failures.

---

## Security: from demo token to defence-grade

`app/core/security.py` implements a single shared bearer token, disabled by default. It is
deliberately minimal and is **not** presented as adequate for deployment. Its real value is
structural: every control mutation and the telemetry socket already pass through one choke
point, so the work below is contained rather than a rewrite.

What a real deployment needs:

| Concern | Today | Required |
|---|---|---|
| Transport | Plain WS/HTTP, CORS-limited | mTLS, pinned CA, no plaintext fallback |
| Identity | One shared secret | Per-airframe and per-operator certificates |
| Authorisation | All-or-nothing | Role separation — observers cannot inject faults or command |
| Token handling | `?token=` accepted on the WS handshake (browsers cannot set headers) | Short-lived ticket issued over the authenticated REST channel; query strings land in logs |
| Integrity | None | Signed telemetry frames so a recording cannot be silently altered |
| Key management | Env var | HSM or secure element, scheduled rotation, revocation |
| Audit | Application logs | Tamper-evident log of every command, with operator identity |
| Command path | Control endpoints mutate the sim | On a real vehicle these must not exist, or must be hard-gated — the PHM system is an observer, and a compromised PHM system must not be able to command an engine |

That last row is the important one. Fault injection is a simulation affordance. On real
hardware the control surface either disappears or is restricted to a ground test mode that
is physically incapable of reaching a flying vehicle.

---

## What would need genuine research

Being straight about the boundaries of the current design:

- **Model calibration** dominates the value and the effort. An uncalibrated twin produces
  confident residuals that mean nothing.
- **RUL against real run-to-failure data.** The current predictor extrapolates a health
  trend; there is no substitute for observing actual failures, and aero engines fail
  rarely by design. Expect to seed with accelerated bench testing.
- **Vibration is synthesised, not solved.** `vibration_model.py` produces a plausible
  spectrum from known fault content. Real crank/valvetrain dynamics, and real bearing
  defect frequencies, are a different modelling problem — and are the single richest
  diagnostic channel on a real engine.
- **Certification.** Nothing here has been developed to DO-178C/DO-254 or an equivalent
  process. A PHM system that influences continued-airworthiness decisions is subject to
  that scrutiny, and it constrains architecture from the start rather than being added at
  the end.
