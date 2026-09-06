"""J1939 SLOT conventions -> DBC value tables.

The export names each parameter's SLOT (`SAEbs02`, `SAEbs03`, ...) but does not
list the state labels; those live in the standard's prose.  What *is* fixed by
J1939, for every discrete parameter, is the top of the range:

    all-ones          "not available" / "don't care"
    all-ones minus 1  "error indicator"

So a 2-bit state always ends `10 = error, 11 = not available`, and a 3-bit state
always ends `110 = error, 111 = not available`.  Those two entries can be
emitted for any SAEbs parameter without guessing, and they match the reference
DBC's hand-written tables exactly.

The remaining low codes are parameter-specific and are deliberately left
unlabelled: inventing "Active"/"Inactive" would be a guess dressed as a fact.
Values are emitted as `convention`, never as `standard`.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

from ..evidence import Evidence, by_convention

_SAEBS = re.compile(r"^SAEbs(\d+)$", re.I)

NOT_AVAILABLE = "NotAvailable"
ERROR = "Error"


def bit_state_width(slot_name: str) -> Optional[int]:
    """Width in bits encoded in a SAEbsNN slot name, if it is one."""
    m = _SAEBS.match((slot_name or "").strip())
    if not m:
        return None
    width = int(m.group(1))
    return width if 1 <= width <= 32 else None


def value_table(slot_name: str, length_bits: Optional[int] = None) -> Dict[int, str]:
    """The J1939 top-of-range codes for a discrete parameter.

    Returns an empty table for anything that is not a discrete state, and for
    1-bit parameters, which have no room for the error codes.
    """
    width = bit_state_width(slot_name)
    if width is None:
        return {}
    if length_bits is not None:
        width = length_bits
    if width < 2:
        return {}
    top = (1 << width) - 1
    return {top: NOT_AVAILABLE, top - 1: ERROR}


def evidence(slot_name: str, width: int) -> Evidence:
    return by_convention(
        f"j1939-slot:{slot_name}",
        f"J1939 reserves the top two codes of a {width}-bit discrete parameter: "
        f"{(1 << width) - 2} = error, {(1 << width) - 1} = not available. "
        f"Lower codes are parameter-specific and are not published in the free export.",
        slot=slot_name,
        width=width,
    )
