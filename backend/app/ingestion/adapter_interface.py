"""The seam between "where engine data comes from" and everything downstream.

Right now the data comes from a physics simulation. On a real airframe it comes off a CAN
bus from an ECU/FADEC. The entire digital-twin, residual, PHM and dashboard stack should
not know or care which — and after this interface exists, it does not.

    EngineDataAdapter (abstract)
        |
        +-- SimulatedAdapter   -> wraps app/sim/simulation_loop.py            [implemented]
        +-- CANBusAdapter      -> SocketCAN / J1939 / FADEC serial            [stub]
        +-- ReplayAdapter      -> recorded missions from SQLite               [see replay_engine]

`RawEngineData` is deliberately *raw*: engineering units straight off the sensors, no
health scores, no residuals, no derived analytics. It is the boundary at which a real ECU
frame and a simulated frame become indistinguishable. Everything above it — the twin, the
anomaly detector, the classifier — operates identically either way, which is what makes
the swap to real hardware a configuration change rather than a rewrite.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RawEngineData:
    """One sample of raw engine instrumentation, in engineering units.

    This is the contract a real ECU integration must satisfy. Fields are optional where a
    given installation may not have that sensor — a naturally aspirated engine has no
    boost transducer, and not every airframe instruments every cylinder."""

    timestamp: float

    # ---- rotational / load -------------------------------------------------
    rpm: float | None = None
    manifold_pressure_kpa: float | None = None
    boost_pressure_kpa: float | None = None
    throttle_position: float | None = None      # 0-1

    # ---- combustion --------------------------------------------------------
    egt_c: list[float] = field(default_factory=list)     # per cylinder
    cht_c: float | None = None
    injection_timing_deg: float | None = None
    fuel_flow_lph: float | None = None
    lambda_ratio: float | None = None           # measured AFR / stoichiometric

    # ---- lubrication -------------------------------------------------------
    oil_temp_c: float | None = None
    oil_pressure_kpa: float | None = None

    # ---- vibration ---------------------------------------------------------
    vibration_rms: list[float] = field(default_factory=list)
    #: Raw accelerometer window, if the installation streams one. The FFT feature
    #: extraction in app/physics/vibration_model.py consumes this directly.
    vibration_samples: list[float] = field(default_factory=list)
    vibration_sample_rate_hz: float | None = None

    # ---- electrical --------------------------------------------------------
    battery_voltage_v: float | None = None
    alternator_output_v: float | None = None

    # ---- airframe / environment -------------------------------------------
    altitude_m: float | None = None
    airspeed_ms: float | None = None
    ambient_temperature_c: float | None = None

    # ---- provenance --------------------------------------------------------
    source: str = "unknown"
    #: Per-signal validity. A real bus drops frames and reports sensor faults out of
    #: band; the PHM layer must be able to tell "0 kPa" from "no reading".
    valid: dict[str, bool] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class EngineDataAdapter(ABC):
    """Source of raw engine data. Implementations must be non-blocking per call."""

    #: Human-readable name, surfaced in diagnostics.
    name: str = "abstract"

    @abstractmethod
    def read_frame(self) -> RawEngineData | None:
        """Return the most recent sample, or None if nothing new is available."""

    def start(self) -> None:
        """Open the underlying transport. Default: nothing to do."""

    def stop(self) -> None:
        """Release the transport. Default: nothing to do."""

    @property
    def connected(self) -> bool:
        return True


class SimulatedAdapter(EngineDataAdapter):
    """Wraps the physics simulation so it presents the same interface real hardware will.

    This is the adapter in use today. It reads the already-computed plant state rather
    than re-deriving anything, so it adds no cost to the tick."""

    name = "simulated"

    def __init__(self, simulation_loop) -> None:
        self._sim = simulation_loop

    def read_frame(self) -> RawEngineData | None:
        state = getattr(self._sim.plant, "state", None)
        frame = self._sim.get_latest()
        if state is None or frame is None:
            return None

        return RawEngineData(
            timestamp=frame.timestamp,
            rpm=state.rpm,
            manifold_pressure_kpa=state.manifold_pressure_kpa,
            boost_pressure_kpa=state.boost_pressure_kpa,
            throttle_position=getattr(self._sim, "throttle", None),
            egt_c=list(state.egt_c),
            cht_c=state.cht_c,
            injection_timing_deg=state.injection_timing_deg,
            fuel_flow_lph=state.fuel_flow_lph,
            lambda_ratio=(
                state.afr_mean / self._sim.p.afr_stoich if state.afr_mean else None
            ),
            oil_temp_c=state.oil_temp_c,
            oil_pressure_kpa=state.oil_pressure_kpa,
            vibration_rms=list(state.vibration_rms),
            battery_voltage_v=state.battery_voltage_v,
            alternator_output_v=state.alternator_output_v,
            altitude_m=frame.altitude_m,
            airspeed_ms=frame.airspeed_ms,
            ambient_temperature_c=frame.ambient_temperature_c,
            source="simulated",
            valid={},
        )


class CANBusAdapter(EngineDataAdapter):
    """Stub: read a real engine over CAN (SocketCAN) from an ECU/FADEC.

    **Not implemented.** This documents the integration precisely enough that wiring it up
    is an afternoon's work against a real bus, and it is the reason `RawEngineData` looks
    the way it does.

    Transport
    ---------
    On Linux, SocketCAN presents the bus as a network interface::

        sudo ip link set can0 type can bitrate 500000
        sudo ip link set up can0

    then `python-can` gives frames::

        import can
        bus = can.interface.Bus(channel="can0", bustype="socketcan")
        msg = bus.recv(timeout=0.01)     # msg.arbitration_id, msg.data (8 bytes)

    Decoding
    --------
    Raw CAN carries 8 data bytes per frame with no self-description; the meaning comes
    from a database. Two realistic cases:

    * **J1939** (the SAE standard most engine controllers speak). The 29-bit identifier
      encodes a PGN; each PGN has defined SPNs with scaling and offset. Relevant ones:

        - PGN 61444 (EEC1)  : engine speed, SPN 190, 0.125 rpm/bit
        - PGN 65262 (ET1)   : coolant/oil temperature, SPN 110/175, 1 degC/bit, -40 offset
        - PGN 65263 (EFL/P1): oil pressure, SPN 100, 4 kPa/bit
        - PGN 65266 (LFE)   : fuel rate, SPN 183, 0.05 L/h per bit
        - PGN 65270 (IC1)   : boost pressure, SPN 102, 2 kPa/bit
        - PGN 65030 / OEM   : per-cylinder EGT is usually proprietary

    * **Proprietary FADEC frames**, described by a vendor DBC file. `cantools` loads the
      DBC and decodes by name, which is preferable to hand-rolled bit shifts::

          import cantools
          db = cantools.database.load_file("fadec.dbc")
          decoded = db.decode_message(msg.arbitration_id, msg.data)

    Implementation notes for whoever builds this
    -------------------------------------------
    1. **Run the bus read on its own thread.** Frames arrive faster than the 10 Hz tick and
       `recv()` blocks; buffer into a latest-value-per-signal dict and have `read_frame()`
       snapshot it. `read_frame()` must not block the event loop.
    2. **Signals arrive independently.** RPM at 100 Hz and oil temperature at 1 Hz do not
       share a frame. Hold last-known values with their own timestamps and populate
       `valid[signal] = False` once a value goes stale past its expected period — the PHM
       layer must distinguish "0 kPa" from "no reading", or a dropped frame will look
       exactly like a catastrophic oil pressure loss.
    3. **Units.** Convert to the engineering units in `RawEngineData` at the boundary and
       nowhere else. Every model downstream assumes kPa, degC, L/h, RPM.
    4. **Vibration almost never comes over CAN.** Accelerometers are usually a separate
       high-rate path (SPI/I2S into the edge computer). Fill `vibration_samples` plus
       `vibration_sample_rate_hz` and let the existing FFT feature extraction handle it.
    5. **The bus is a safety-critical shared medium.** This adapter must be read-only.
       Never transmit onto an engine control bus from the PHM system.

    Once this returns populated `RawEngineData`, nothing downstream changes: the digital
    twin still runs its healthy reference model against the same commands, residuals are
    computed the same way, and the dashboard cannot tell the difference.
    """

    name = "canbus"

    def __init__(self, channel: str = "can0", bitrate: int = 500_000) -> None:
        self.channel = channel
        self.bitrate = bitrate
        self._connected = False

    def start(self) -> None:
        raise NotImplementedError(
            "CANBusAdapter is a documented stub. Implement with python-can + cantools "
            "against your ECU's DBC or J1939 PGN set — see the class docstring."
        )

    def read_frame(self) -> RawEngineData | None:
        raise NotImplementedError("CANBusAdapter is a documented stub.")

    @property
    def connected(self) -> bool:
        return self._connected
