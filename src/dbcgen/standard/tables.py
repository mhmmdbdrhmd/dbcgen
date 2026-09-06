"""The identity lookup tables from the ISOBUS export.

These turn a raw Address Claimed payload into readable node names: an 11-bit
manufacturer code becomes a company, a function id becomes a role, an industry
group becomes a domain.  All four tables are fully populated in the free export.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Dict, Optional

from .fetch import CacheInfo, open_member

MANUFACTURERS = "Manufacturer IDs.csv"
GLOBAL_FUNCTIONS = "Global NAME Functions.csv"
IG_FUNCTIONS = "IG Specific NAME Function.csv"
INDUSTRY_GROUPS = "Industry Groups.csv"
GLOBAL_ADDRESSES = "Global Source Addresses.csv"


@dataclass
class IdentityTables:
    manufacturers: Dict[int, str] = field(default_factory=dict)
    industry_groups: Dict[int, str] = field(default_factory=dict)
    functions: Dict[int, str] = field(default_factory=dict)
    """Function ids 0..127, which mean the same in every industry group."""
    ig_functions: Dict[tuple, str] = field(default_factory=dict)
    """(industry_group, vehicle_system, function) -> description, for ids >= 128."""
    vehicle_systems: Dict[tuple, str] = field(default_factory=dict)
    source_addresses: Dict[int, str] = field(default_factory=dict)

    def manufacturer(self, code: int) -> str:
        return self.manufacturers.get(code, f"Manufacturer{code}")

    def function(self, function_id: int, industry_group: int = 0, vehicle_system: int = 0) -> str:
        """Function ids below 128 are global; at and above 128 they are
        specific to the industry group and vehicle system."""
        if function_id < 128:
            return self.functions.get(function_id, f"Function{function_id}")
        for key in (
            (industry_group, vehicle_system, function_id),
            (industry_group, 0, function_id),
            (0, 0, function_id),
        ):
            if key in self.ig_functions:
                return self.ig_functions[key]
        return f"Function{function_id}"

    def vehicle_system(self, industry_group: int, vehicle_system: int) -> str:
        return self.vehicle_systems.get(
            (industry_group, vehicle_system), f"VehicleSystem{vehicle_system}"
        )

    def industry_group(self, ig: int) -> str:
        return self.industry_groups.get(ig, f"IndustryGroup{ig}")

    def source_address(self, sa: int) -> Optional[str]:
        return self.source_addresses.get(sa)


def _rows(text: str):
    return csv.DictReader(io.StringIO(text))


def _as_int(value: str) -> Optional[int]:
    value = (value or "").strip()
    return int(value) if value.isdigit() else None


def load(info: CacheInfo) -> IdentityTables:
    t = IdentityTables()

    for row in _rows(open_member(info, MANUFACTURERS)):
        code = _as_int(row.get("value", ""))
        if code is not None:
            t.manufacturers[code] = (row.get("manufacturer") or "").strip()

    for row in _rows(open_member(info, INDUSTRY_GROUPS)):
        ig = _as_int(row.get("industry_group_id", ""))
        if ig is not None:
            t.industry_groups[ig] = (row.get("industry_group_description") or "").strip()

    for row in _rows(open_member(info, GLOBAL_FUNCTIONS)):
        fid = _as_int(row.get("function_id", ""))
        if fid is not None:
            t.functions[fid] = (row.get("function_description") or "").strip()

    for row in _rows(open_member(info, IG_FUNCTIONS)):
        ig = _as_int(row.get("industry_group_id", ""))
        vs = _as_int(row.get("vehicle_system_id", ""))
        fid = _as_int(row.get("function_id", ""))
        if ig is None or vs is None:
            continue
        vs_desc = (row.get("vehicle_system_description") or "").strip()
        if vs_desc:
            t.vehicle_systems.setdefault((ig, vs), vs_desc)
        if fid is not None:
            t.ig_functions[(ig, vs, fid)] = (row.get("function_description") or "").strip()

    for row in _rows(open_member(info, GLOBAL_ADDRESSES)):
        sa = _as_int(row.get("source_address_id", ""))
        if sa is not None:
            t.source_addresses[sa] = (row.get("name") or "").strip()

    return t
