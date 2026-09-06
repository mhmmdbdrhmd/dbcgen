"""Node identity from Address Claimed.

Every J1939 ECU announces itself on PGN 60928 with a 64-bit NAME.  Decoding it
against the export's manufacturer and function tables turns an anonymous source
address into a named node -- the ECU's own words rather than an inference.

Where a source address never claims (it only ever transmits, or the recording
missed the claim), the node still gets a name, but one that says only what is
known: the address itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from ..evidence import Evidence, from_standard, inferred
from ..standard.tables import IdentityTables
from ..standard.isobus import to_identifier
from .ids import decompose

PGN_ADDRESS_CLAIMED = 60928


@dataclass(frozen=True)
class Name:
    """The decoded 64-bit J1939 NAME."""

    identity: int
    manufacturer_code: int
    ecu_instance: int
    function_instance: int
    function: int
    vehicle_system: int
    vehicle_system_instance: int
    industry_group: int
    arbitrary_address_capable: int
    raw: int

    @classmethod
    def decode(cls, payload: bytes) -> Optional["Name"]:
        if len(payload) < 8:
            return None
        v = int.from_bytes(payload[:8], "little")

        def f(lo: int, n: int) -> int:
            return (v >> lo) & ((1 << n) - 1)

        return cls(
            identity=f(0, 21),
            manufacturer_code=f(21, 11),
            ecu_instance=f(32, 3),
            function_instance=f(35, 5),
            function=f(40, 8),
            vehicle_system=f(49, 7),
            vehicle_system_instance=f(56, 4),
            industry_group=f(60, 3),
            arbitrary_address_capable=f(63, 1),
            raw=v,
        )

    def describe(self, tables: IdentityTables) -> str:
        return (
            f"{tables.manufacturer(self.manufacturer_code)} "
            f"(mfg {self.manufacturer_code}, identity {self.identity}); "
            f"function {self.function} "
            f"{tables.function(self.function, self.industry_group, self.vehicle_system)}; "
            f"vehicle system {tables.vehicle_system(self.industry_group, self.vehicle_system)}; "
            f"industry group {tables.industry_group(self.industry_group)}; "
            f"ECU instance {self.ecu_instance}"
        )


@dataclass
class NodeIdentity:
    source_address: int
    name: str
    comment: str = ""
    evidence: Optional[Evidence] = None
    decoded: Optional[Name] = None


def _identifier(name: Name, sa: int, tables: IdentityTables) -> str:
    """A readable node name: what the ECU says it does, plus its address.

    The address is always appended, because two ECUs on one bus routinely
    declare the same function and would otherwise collide.
    """
    role = tables.function(name.function, name.industry_group, name.vehicle_system)
    ident = to_identifier(role, "ECU")
    # Function 0 with manufacturer 0 is an unprogrammed or generic claim; the
    # manufacturer is more informative in that case.
    if not ident or ident.lower().startswith(("reserved", "notavailable")):
        ident = to_identifier(tables.manufacturer(name.manufacturer_code), "ECU")
    return f"{ident[:28]}_{sa:02X}"


def identify(frameset, tables: IdentityTables) -> Dict[int, NodeIdentity]:
    """Map every source address seen on the bus to a node identity."""
    nodes: Dict[int, NodeIdentity] = {}
    claims: Dict[int, bytes] = {}

    for group in frameset:
        if not group.extended:
            continue
        j = decompose(group.can_id)
        nodes.setdefault(
            j.sa,
            NodeIdentity(
                source_address=j.sa,
                name=f"ECU_{j.sa:02X}",
                comment="",
                evidence=inferred(
                    "source-address",
                    f"transmits on the bus from source address 0x{j.sa:02X}, "
                    f"but no Address Claimed (PGN 60928) was recorded for it",
                    sa=j.sa,
                ),
            ),
        )
        if j.pgn == PGN_ADDRESS_CLAIMED and group.frames:
            claims.setdefault(j.sa, group.frames[0].data)

    for sa, payload in claims.items():
        name = Name.decode(payload)
        if name is None:
            continue
        nodes[sa] = NodeIdentity(
            source_address=sa,
            name=_identifier(name, sa, tables),
            comment=name.describe(tables),
            evidence=from_standard(
                "j1939-81:AddressClaimed",
                f"decoded from the node's own Address Claimed on PGN 60928, "
                f"NAME 0x{name.raw:016X}",
                sa=sa,
                name=f"{name.raw:016X}",
            ),
            decoded=name,
        )

    # Two ECUs can legitimately claim the same role; keep names unique.
    seen: Dict[str, int] = {}
    for sa in sorted(nodes):
        base = nodes[sa].name
        if base in seen:
            nodes[sa].name = f"{base}_{sa:02X}"
        seen[base] = sa
    return nodes
