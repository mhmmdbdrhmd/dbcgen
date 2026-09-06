"""Layouts for the J1939 network-layer parameter groups.

The isobus.net export names these groups but gives them no bit layout, because
they are defined in the network-layer parts of the standard rather than in the
application-layer tables the export mirrors.  Those parts -- J1939-21 (transport
and request) and J1939-81 (address claiming) -- are not the paywalled Digital
Annex material; the field layouts below are the published definitions, and they
are what every open J1939 stack implements.

They are marked `standard`, citing the part that defines them.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..evidence import from_standard
from ..model import Signal

J1939_21 = "J1939-21"
J1939_81 = "J1939-81"

PGN_REQUEST = 59904
PGN_TP_DT = 60160
PGN_TP_CM = 60416
PGN_ADDRESS_CLAIMED = 60928

#: TP.CM control byte values, which multiplex the rest of the payload.
TP_CM_RTS = 16
TP_CM_CTS = 17
TP_CM_END_OF_MSG_ACK = 19
TP_CM_BAM = 32
TP_CM_ABORT = 255

TP_CM_CONTROL_BYTES = {
    TP_CM_RTS: "RequestToSend",
    TP_CM_CTS: "ClearToSend",
    TP_CM_END_OF_MSG_ACK: "EndOfMsgAck",
    TP_CM_BAM: "BroadcastAnnounce",
    TP_CM_ABORT: "ConnectionAbort",
}


def _sig(name, start, length, doc, detail, **kw) -> Signal:
    return Signal(
        name=name,
        start_bit=start,
        length=length,
        evidence=from_standard(f"{doc}:{name}", detail),
        **kw,
    )


def address_claimed_signals() -> List[Signal]:
    """The 64-bit J1939 NAME, little-endian bit fields (J1939-81 clause 4)."""
    d = "Address Claimed NAME field"
    return [
        _sig("Identity", 0, 21, J1939_81, d),
        _sig("ManufacturerCode", 21, 11, J1939_81, d),
        _sig("ECUInstance", 32, 3, J1939_81, d),
        _sig("FunctionInstance", 35, 5, J1939_81, d),
        _sig("Function", 40, 8, J1939_81, d),
        _sig("NameReserved", 48, 1, J1939_81, d),
        _sig("VehicleSystem", 49, 7, J1939_81, d),
        _sig("VehicleSystemInstance", 56, 4, J1939_81, d),
        _sig("IndustryGroup", 60, 3, J1939_81, d),
        _sig(
            "ArbitraryAddressCapable",
            63,
            1,
            J1939_81,
            d,
            values={0: "NotCapable", 1: "Capable"},
        ),
    ]


def request_signals() -> List[Signal]:
    """PGN 59904: three bytes naming the parameter group being requested."""
    return [
        _sig(
            "RequestedPGN",
            0,
            24,
            J1939_21,
            "Parameter group being requested, little-endian",
        )
    ]


def tp_dt_signals() -> List[Signal]:
    """PGN 60160: a sequence number then seven payload bytes."""
    sigs = [
        _sig("SequenceNumber", 0, 8, J1939_21, "Packet sequence number, 1-based", minimum=1)
    ]
    for i in range(1, 8):
        sigs.append(
            _sig(f"Data{i}", i * 8, 8, J1939_21, f"Transported payload byte {i} of 7")
        )
    return sigs


def tp_cm_signals() -> List[Signal]:
    """PGN 60416, multiplexed on the control byte.

    The five control bytes give the payload five different meanings, which is
    exactly what a DBC multiplexer expresses, so a trace viewer shows only the
    fields that apply to the frame in front of it.
    """
    doc, d = J1939_21, "Transport protocol connection management"
    sigs = [
        _sig("ControlByte", 0, 8, doc, d, multiplexer="M", values=dict(TP_CM_CONTROL_BYTES))
    ]

    def mux(name, start, length, code, detail, **kw):
        sigs.append(
            _sig(name, start, length, doc, detail, multiplexer=f"m{code}", **kw)
        )

    for code in (TP_CM_RTS, TP_CM_END_OF_MSG_ACK, TP_CM_BAM):
        label = TP_CM_CONTROL_BYTES[code]
        mux(f"TotalMessageSize_{label}", 8, 16, code, f"{label}: total bytes to transfer")
        mux(f"TotalPackets_{label}", 24, 8, code, f"{label}: total packet count")
    mux("MaxPacketsPerCTS_RequestToSend", 32, 8, TP_CM_RTS,
        "RequestToSend: maximum packets the sender may send per CTS")

    mux("PacketsToSend_ClearToSend", 8, 8, TP_CM_CTS, "ClearToSend: packets the receiver will accept")
    mux("NextPacket_ClearToSend", 16, 8, TP_CM_CTS, "ClearToSend: next packet number expected")
    mux("AbortReason_ConnectionAbort", 8, 8, TP_CM_ABORT, "ConnectionAbort: reason code")

    for code, label in TP_CM_CONTROL_BYTES.items():
        mux(f"TransportedPGN_{label}", 40, 24, code, f"{label}: the PGN being transported")
    return sigs


J1939_73 = "J1939-73"
PGN_DM1 = 65226

#: The four lamps a diagnostic message reports, and the bit each starts at.
#: Every one is a two-bit state using J1939's standard codes.
_DM1_LAMPS = (
    ("ProtectLampStatus", 0),
    ("AmberWarningLampStatus", 2),
    ("RedStopLampStatus", 4),
    ("MalfunctionIndicatorLampStatus", 6),
)
_LAMP_STATES = {0: "Off", 1: "On", 2: "Error", 3: "NotAvailable"}


def dm1_signals() -> List[Signal]:
    """PGN 65226 as a single frame, per J1939-73.

    The export lists DM1 as multi-packet and flattens its suspect parameter
    number into one 19-bit field starting at bit 16.  That is not the frame
    layout: J1939-73 splits the SPN around the failure mode indicator, so the
    flattened row overlaps FMI and would place both wrongly.  A message
    carrying one active trouble code fits a single 8-byte frame -- the common
    case, and the only one a DBC can express -- so the published single-frame
    layout is used instead.
    """
    doc, d = J1939_73, "Active diagnostic trouble code"
    sigs: List[Signal] = []
    for name, bit in _DM1_LAMPS:
        sigs.append(
            _sig(name, bit, 2, doc, f"{d}: lamp state", values=dict(_LAMP_STATES))
        )
        sigs.append(
            _sig(
                f"{name}Flash",
                bit + 8,
                2,
                doc,
                f"{d}: flash state of the same lamp",
                values=dict(_LAMP_STATES),
            )
        )
    sigs += [
        _sig("SPN_LowBytes", 16, 16, doc, f"{d}: low 16 bits of the suspect parameter number"),
        _sig("FMI", 32, 5, doc, f"{d}: failure mode indicator"),
        _sig("SPN_HighBits", 37, 3, doc, f"{d}: high 3 bits of the suspect parameter number"),
        _sig("OccurrenceCount", 40, 7, doc, f"{d}: how many times the fault has occurred"),
        _sig(
            "SPN_ConversionMethod",
            47,
            1,
            doc,
            f"{d}: 0 means the SPN fields are packed as defined here",
        ),
    ]
    return sigs


#: PGN -> (signal factory, DLC, fallback name, description)
NETWORK_LAYER: Dict[int, tuple] = {
    PGN_DM1: (dm1_signals, 8, "DM1",
              "J1939-73 active diagnostic trouble codes, single-frame form"),
    PGN_ADDRESS_CLAIMED: (address_claimed_signals, 8, "AddressClaimed",
                          "J1939-81 address claim carrying the 64-bit NAME"),
    PGN_REQUEST: (request_signals, 3, "Request",
                  "J1939-21 request for a parameter group"),
    PGN_TP_DT: (tp_dt_signals, 8, "TPDT",
                "J1939-21 transport protocol data transfer"),
    PGN_TP_CM: (tp_cm_signals, 8, "TPCM",
                "J1939-21 transport protocol connection management"),
}


def signals_for(pgn: int) -> Optional[List[Signal]]:
    entry = NETWORK_LAYER.get(pgn)
    return entry[0]() if entry else None


def info_for(pgn: int) -> Optional[tuple]:
    """(dlc, fallback_name, description) for a network-layer PGN."""
    entry = NETWORK_LAYER.get(pgn)
    return (entry[1], entry[2], entry[3]) if entry else None
