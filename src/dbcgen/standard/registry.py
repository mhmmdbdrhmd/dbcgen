"""The catalogue of standards `dbcgen` can build from.

What this repository ships is a list of *where to get* each database and how to
read it -- never the databases themselves.  That is partly hygiene, because a
fetched copy stays current and a vendored one rots, and partly the law: the
ISOBUS registry publishes no terms of use at all, and silence is not a licence.
Only sources whose licence plainly permits it would ever be a candidate for
vendoring, and none needs to be.

Each source states its licence and attribution so a generated file can carry
them, and states the provenance its signals get, because these are not claims
of equal strength:

    standard   the body that defines the protocol says so
    community  an openly-licensed database reconstructed by other people
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from ..model import Database


@dataclass(frozen=True)
class Source:
    """One selectable standard."""

    name: str
    title: str
    protocol: str
    origin: str
    """The provenance every signal from this source is marked with."""
    licence: str
    attribution: str
    summary: str
    needs_network: bool
    variants: bool = False
    """True when the source is a family of files and needs `name:variant`."""

    @property
    def label(self) -> str:
        return f"{self.name}{':<variant>' if self.variants else ''}"


SOURCES: List[Source] = [
    Source(
        name="isobus",
        title="J1939 / ISO 11783 (ISOBUS)",
        protocol="J1939",
        origin="standard",
        licence="no stated terms -- fetched at run time, never redistributed",
        attribution="isobus.net, the ISO 11783-11 online database",
        summary=(
            "Agricultural and heavy-duty parameter groups. 102 of 3,213 groups "
            "carry a published bit layout; the rest are named only, because SAE "
            "sells their layouts in the Digital Annex."
        ),
        needs_network=True,
    ),
    Source(
        name="nmea2000",
        title="NMEA 2000 (marine)",
        protocol="J1939",
        origin="community",
        licence="Apache-2.0",
        attribution="CANboat -- https://github.com/canboat/canboat",
        summary=(
            "Marine instrumentation: navigation, engines, tanks, AC systems. "
            "Reconstructed from observed traffic because NMEA sells its "
            "specification, so signals are marked community, not standard. "
            "Single-frame groups only."
        ),
        needs_network=True,
    ),
    Source(
        name="obd2",
        title="OBD-II service 01 (live data)",
        protocol="OBD-II",
        origin="standard",
        licence="formulas published in SAE J1979 and republished everywhere",
        attribution="SAE J1979 service 01 definitions",
        summary=(
            "The diagnostic parameters every road vehicle answers on the "
            "connector under the dashboard: engine speed, vehicle speed, "
            "coolant and intake temperatures, fuel trims, throttle. Needs no "
            "download and works offline."
        ),
        needs_network=False,
    ),
    Source(
        name="opendbc",
        title="Production vehicles (opendbc)",
        protocol="vehicle CAN",
        origin="community",
        licence="MIT",
        attribution="opendbc, comma.ai -- https://github.com/commaai/opendbc",
        summary=(
            "Per-platform maps for production cars, reverse-engineered by "
            "driving them. Describes PARTICULAR VEHICLES rather than a "
            "protocol, so a match is a strong lead, not a guarantee. Choose a "
            "platform with 'opendbc:<platform>'."
        ),
        needs_network=True,
        variants=True,
    ),
]

BY_NAME = {s.name: s for s in SOURCES}
DEFAULT = "isobus"


def get(name: str) -> Source:
    base = name.split(":", 1)[0]
    if base not in BY_NAME:
        known = ", ".join(sorted(BY_NAME))
        raise KeyError(f"unknown source {base!r}; known sources are {known}")
    return BY_NAME[base]


def variant_of(name: str) -> Optional[str]:
    return name.split(":", 1)[1] if ":" in name else None


def build(name: str, refresh: bool = False, **kw) -> Database:
    """Build a database from a named source.

    `isobus` is not handled here: it is the only source that also participates
    in the recording pipeline, so it keeps its own entry point in `generate`.
    """
    source = get(name)
    variant = variant_of(name)

    if source.name == "nmea2000":
        from . import nmea2000

        return nmea2000.ensure_and_build(refresh=refresh, **kw)

    if source.name == "obd2":
        from . import obd2

        return obd2.build(**kw)

    if source.name == "opendbc":
        from . import opendbc

        if not variant:
            available = ", ".join(n for n, _ in opendbc.platforms()[:8])
            raise KeyError(
                "opendbc is a family of per-vehicle files, so it needs a "
                f"platform: 'opendbc:<platform>'. Try one of: {available} ... "
                "Run 'dbcgen sources --list opendbc' for all of them."
            )
        return opendbc.ensure_and_build(variant, refresh=refresh)

    raise KeyError(f"source {name!r} is not built here")
