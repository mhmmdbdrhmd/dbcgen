"""Public CAN recordings that ship with the DBC that decodes them.

A reverse-engineering tool can only be trusted if its output is checked against
a layout somebody else published.  These are logs released with their own DBC,
so the reference is independent of anything this project decided.

Source: https://github.com/CSS-Electronics/api-examples (MIT).  Raw files are
downloaded on demand into `benchmarks/data/`, which is gitignored -- the repo
records where the data comes from, never a copy of it.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

REPO = "CSS-Electronics/api-examples"
REF = "master"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{REF}/examples"
API_BASE = f"https://api.github.com/repos/{REPO}"
LICENCE = "MIT (CSS-Electronics/api-examples)"

DATA_DIR = Path(__file__).resolve().parent / "data"


@dataclass
class Dataset:
    """A recording plus the reference DBC its publisher decodes it with."""

    key: str
    description: str
    logs: List[str]
    dbc: str
    bitrate: str = "classical CAN, 250 kbit/s"
    notes: str = ""
    #: How much of the bus the reference DBC actually describes.  A sparse
    #: reference bounds recall from above: signals it never names cannot be
    #: scored, whether or not the decoder found them.
    ground_truth: str = ""
    extra_dbcs: List[str] = field(default_factory=list)

    @property
    def dir(self) -> Path:
        return DATA_DIR / self.key


DATASETS = {
    "j1939-truck": Dataset(
        key="j1939-truck",
        description="Heavy-duty J1939 bus logged with a CANedge",
        # The other logs in this repository under LOG/ are 11-bit diagnostic
        # and battery traffic, not J1939; only this one carries a real
        # heavy-duty bus with 29-bit PGN-bearing identifiers.
        logs=["data-processing/LOG/958D2219/00002501/00002081.MF4"],
        dbc="data-processing/dbc_files/CSS-Electronics-SAE-J1939-DEMO.dbc",
        bitrate="classical CAN, 250 kbit/s",
        ground_truth=(
            "sparse: the demo DBC defines 2 signals (EEC1/EngineSpeed, "
            "CCVS1/WheelBasedVehicleSpeed). It bounds signal recall, but it "
            "exercises real J1939 traffic and the name-only lookup path, "
            "since SPN 190 is name-only in the free export."
        ),
    ),
    "gnss": Dataset(
        key="gnss",
        description="CANmod.gps module on an 11-bit bus",
        logs=[
            "other/concatenate-mf4-by-period/LOG/2F6913DB/00001089/00000001-63BEF5CD.MF4",
            "other/concatenate-mf4-by-period/LOG/2F6913DB/00001089/00000002-63BEFDF9.MF4",
        ],
        dbc="other/concatenate-mf4-by-period/dbc_files/gnss.dbc",
        bitrate="classical CAN, 500 kbit/s",
        ground_truth=(
            "dense: 19 messages and 221 signals, every bit accounted for. "
            "This is the primary accuracy measurement -- but note the ids are "
            "11-bit, so it scores bit-layout inference, not J1939 lookup."
        ),
    ),
}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "dbcgen benchmarks"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _via_api(rel: str) -> bytes:
    """Fetch through the GitHub API.

    raw.githubusercontent intermittently answers 400 to unauthenticated bulk
    requests, which would otherwise make the benchmark unrunnable at exactly
    the moment somebody first tries it.  The contents endpoint carries files up
    to 1 MB inline; anything larger is read through the blob it points at,
    which goes to 100 MB.
    """
    meta = json.loads(_get(f"{API_BASE}/contents/examples/{rel}?ref={REF}"))
    content = meta.get("content") or ""
    if content:
        return base64.b64decode(content)
    blob = json.loads(_get(meta["git_url"]))
    return base64.b64decode(blob["content"])


def _download(url: str, dest: Path, attempts: int = 4, rel: str = "") -> Path:
    """Download once, then cache.  Retries because these are multi-megabyte
    files over TLS and a handshake timeout mid-run should not lose the rest."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest

    last: Exception | None = None
    for attempt in range(attempts):
        # Alternate between the raw host and the API, so a fault on either one
        # costs a retry rather than the whole run.
        use_api = bool(rel) and attempt % 2 == 1
        try:
            payload = _via_api(rel) if use_api else _get(url)
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(payload)
            tmp.replace(dest)  # atomic: a partial file is never left behind
            return dest
        except Exception as exc:
            last = exc
            if attempt < attempts - 1:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"could not download {url}: {last}")


def fetch(dataset: Dataset) -> dict:
    """Download a dataset's logs and reference DBC.  Cached after the first run."""
    paths = {"logs": [], "dbc": None}
    for rel in dataset.logs:
        paths["logs"].append(
            _download(f"{RAW_BASE}/{rel}", dataset.dir / Path(rel).name, rel=rel)
        )
    paths["dbc"] = _download(
        f"{RAW_BASE}/{dataset.dbc}", dataset.dir / Path(dataset.dbc).name, rel=dataset.dbc
    )
    return paths


def fetch_all() -> dict:
    return {key: fetch(ds) for key, ds in DATASETS.items()}


if __name__ == "__main__":
    for key, paths in fetch_all().items():
        ds = DATASETS[key]
        print(f"{key}: {ds.description}")
        print(f"  licence     : {LICENCE}")
        print(f"  reference   : {paths['dbc'].name} ({paths['dbc'].stat().st_size} B)")
        for p in paths["logs"]:
            print(f"  log         : {p.name} ({p.stat().st_size} B)")
        print(f"  ground truth: {ds.ground_truth}")
