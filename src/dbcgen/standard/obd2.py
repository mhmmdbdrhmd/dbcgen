"""OBD-II Mode 01 live data, as a multiplexed message.

Every road vehicle since the late 1990s answers these on the connector under
the dashboard, which makes this the one source that needs no recording, no
fetch and no guess about what kind of bus you are on.

The encoding is fixed by SAE J1979 and is republished everywhere; the formulas
below are those definitions written out, in the same way `network_layer.py`
writes out the J1939-21 layouts the ISOBUS export does not carry.  They are
marked `standard` for the same reason.

A response frame is

    byte 0   number of data bytes that follow
    byte 1   0x41, the service (0x01) with the response bit set
    byte 2   the parameter id  <- the multiplexer
    byte 3+  A, B, C, D

so the parameter id selects what the remaining bytes mean, which is exactly
what a DBC multiplexer expresses.  Only service 01 is emitted: describing
several services in one message needs extended multiplexing (`SG_MUL_VAL_`),
which many tools -- including the recorder this pairs with -- decode wrongly
rather than refusing, and a signal that is silently wrong is worse than one
that is absent.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..evidence import from_standard
from ..model import BIG, Database, Message, Signal

DOCUMENT = "SAE J1979"

#: Functional broadcast request, and the first ECU's response.  A bus may carry
#: replies on 0x7E9..0x7EF too, one per responding controller; they share this
#: layout exactly.
REQUEST_ID = 0x7DF
RESPONSE_ID_FIRST = 0x7E8
RESPONSE_ID_LAST = 0x7EF

#: Bit positions in the response frame.  `A` is byte 3.  Multi-byte values are
#: big-endian, so a Motorola start bit is the most significant bit: byte 3's
#: MSB is 31, byte 4's is 39.
_PID_BIT = 16
_A_BIT_LE = 24
_A_MSB = 31

#: name, pid, bytes, factor, offset, unit, description
#: The formula is `physical = raw * factor + offset`, with `raw` read across
#: `bytes` starting at A.
_PIDS: Tuple[Tuple[str, int, int, float, float, str, str], ...] = (
    ("CalculatedEngineLoad", 0x04, 1, 100.0 / 255.0, 0.0, "%", "A*100/255"),
    ("EngineCoolantTemperature", 0x05, 1, 1.0, -40.0, "degC", "A-40"),
    ("ShortTermFuelTrimBank1", 0x06, 1, 100.0 / 128.0, -100.0, "%", "A/1.28-100"),
    ("LongTermFuelTrimBank1", 0x07, 1, 100.0 / 128.0, -100.0, "%", "A/1.28-100"),
    ("ShortTermFuelTrimBank2", 0x08, 1, 100.0 / 128.0, -100.0, "%", "A/1.28-100"),
    ("LongTermFuelTrimBank2", 0x09, 1, 100.0 / 128.0, -100.0, "%", "A/1.28-100"),
    ("FuelPressure", 0x0A, 1, 3.0, 0.0, "kPa", "A*3"),
    ("IntakeManifoldAbsolutePressure", 0x0B, 1, 1.0, 0.0, "kPa", "A"),
    ("EngineSpeed", 0x0C, 2, 0.25, 0.0, "rpm", "(A*256+B)/4"),
    ("VehicleSpeed", 0x0D, 1, 1.0, 0.0, "km/h", "A"),
    ("TimingAdvance", 0x0E, 1, 0.5, -64.0, "deg", "A/2-64"),
    ("IntakeAirTemperature", 0x0F, 1, 1.0, -40.0, "degC", "A-40"),
    ("MassAirFlowRate", 0x10, 2, 0.01, 0.0, "g/s", "(A*256+B)/100"),
    ("ThrottlePosition", 0x11, 1, 100.0 / 255.0, 0.0, "%", "A*100/255"),
    ("RunTimeSinceEngineStart", 0x1F, 2, 1.0, 0.0, "s", "A*256+B"),
    ("DistanceTravelledWithMilOn", 0x21, 2, 1.0, 0.0, "km", "A*256+B"),
    ("FuelRailGaugePressure", 0x23, 2, 10.0, 0.0, "kPa", "(A*256+B)*10"),
    ("CommandedEgr", 0x2C, 1, 100.0 / 255.0, 0.0, "%", "A*100/255"),
    ("EgrError", 0x2D, 1, 100.0 / 128.0, -100.0, "%", "A/1.28-100"),
    ("FuelTankLevelInput", 0x2F, 1, 100.0 / 255.0, 0.0, "%", "A*100/255"),
    ("DistanceSinceCodesCleared", 0x31, 2, 1.0, 0.0, "km", "A*256+B"),
    ("AbsoluteBarometricPressure", 0x33, 1, 1.0, 0.0, "kPa", "A"),
    ("ControlModuleVoltage", 0x42, 2, 0.001, 0.0, "V", "(A*256+B)/1000"),
    ("AbsoluteLoadValue", 0x43, 2, 100.0 / 255.0, 0.0, "%", "(A*256+B)*100/255"),
    ("RelativeThrottlePosition", 0x45, 1, 100.0 / 255.0, 0.0, "%", "A*100/255"),
    ("AmbientAirTemperature", 0x46, 1, 1.0, -40.0, "degC", "A-40"),
    ("AbsoluteThrottlePositionB", 0x47, 1, 100.0 / 255.0, 0.0, "%", "A*100/255"),
    ("AcceleratorPedalPositionD", 0x49, 1, 100.0 / 255.0, 0.0, "%", "A*100/255"),
    ("AcceleratorPedalPositionE", 0x4A, 1, 100.0 / 255.0, 0.0, "%", "A*100/255"),
    ("CommandedThrottleActuator", 0x4C, 1, 100.0 / 255.0, 0.0, "%", "A*100/255"),
    ("EngineOilTemperature", 0x5C, 1, 1.0, -40.0, "degC", "A-40"),
    ("EngineFuelRate", 0x5E, 2, 0.05, 0.0, "L/h", "(A*256+B)/20"),
)


def _signal_for(entry) -> Signal:
    name, pid, width, factor, offset, unit, formula = entry
    return Signal(
        name=f"{name}",
        # A big-endian field starts at the most significant bit of its first
        # byte, and every multi-byte OBD-II value is big-endian.
        start_bit=_A_MSB,
        length=8 * width,
        byte_order=BIG,
        factor=factor,
        offset=offset,
        minimum=offset,
        maximum=offset + factor * ((1 << (8 * width)) - 1),
        unit=unit,
        multiplexer=f"m{pid}",
        comment=f"PID 0x{pid:02X}, {formula}",
        evidence=from_standard(
            f"{DOCUMENT}:PID{pid:02X}",
            f"service 01 parameter 0x{pid:02X}; {formula} as {DOCUMENT} defines it",
            pid=pid,
        ),
    )


def signals() -> List[Signal]:
    """The multiplexer, then one signal per parameter."""
    out = [
        Signal(
            name="ParameterId",
            start_bit=_PID_BIT,
            length=8,
            multiplexer="M",
            comment="Service 01 parameter id; selects what bytes 3..6 mean",
            values={pid: name for name, pid, *_ in _PIDS},
            evidence=from_standard(
                f"{DOCUMENT}:PID",
                "byte 2 of the response carries the parameter id, which selects "
                "the meaning of the bytes after it",
            ),
        ),
    ]
    out += [_signal_for(entry) for entry in _PIDS]
    return out


def build(response_ids: Optional[List[int]] = None) -> Database:
    """A database for OBD-II service 01 responses."""
    ids = response_ids or [RESPONSE_ID_FIRST]
    messages: List[Message] = []
    for index, can_id in enumerate(ids):
        messages.append(
            Message(
                frame_id=can_id,
                name=f"OBD2_Response_{can_id:03X}",
                length=8,
                extended=False,
                comment=(
                    f"OBD-II service 01 response from ECU {index + 1} "
                    f"(0x{can_id:03X}); multiplexed on the parameter id"
                ),
                evidence=from_standard(
                    f"{DOCUMENT}:response",
                    f"11-bit 0x{can_id:03X} is the service 01 response address "
                    f"for ECU {index + 1}, per {DOCUMENT}",
                ),
                signals=signals(),
            )
        )

    db = Database(
        version=f"generated by dbcgen from {DOCUMENT} service 01",
        nodes=[],
        messages=messages,
    )
    db.header_notes = [
        f"Generated by dbcgen from the {DOCUMENT} service 01 (live data) "
        f"definitions. No recording was read.",
        "Requests go to 0x7DF (all ECUs) or 0x7E0..0x7E7 (one ECU); replies come "
        f"back on 0x{RESPONSE_ID_FIRST:03X}..0x{RESPONSE_ID_LAST:03X}, one per "
        "controller that answers. Only the reply is described here.",
        "Only service 01 is described. Other services would need extended "
        "multiplexing, which several tools decode wrongly rather than refusing.",
        "A vehicle answers only the parameters it supports; ask it with PID 0x00 "
        "before assuming a signal here exists on your car.",
    ]
    return db


#: How many parameters this module describes -- quoted by `dbcgen sources`.
PARAMETER_COUNT = len(_PIDS)
