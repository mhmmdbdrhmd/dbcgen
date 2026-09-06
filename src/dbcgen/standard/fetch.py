"""Fetch and cache the public ISOBUS/J1939 parameter export.

Source: https://www.isobus.net/isobus/attachments/isoExport_csv.zip -- the
ISOBUS Data Dictionary export, served without authentication.  It is fetched
and cached, never vendored into the repository, so the tool always states which
dated revision of the standard produced a given DBC.

Offline first: once cached, nothing here touches the network unless asked to.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

EXPORT_URL = "https://www.isobus.net/isobus/attachments/isoExport_csv.zip"
EXPORT_NAME = "isoExport_csv.zip"
META_NAME = "isoExport_csv.meta.json"

#: Files the parser expects inside the archive.
REQUIRED_MEMBERS = (
    "SPNs and PGNs.csv",
    "Manufacturer IDs.csv",
    "Global NAME Functions.csv",
    "Industry Groups.csv",
    "Global Source Addresses.csv",
)

USER_AGENT = "dbcgen (+https://github.com/mhmmdbdrhmd/dbcgen) python-urllib"


class StandardDataError(RuntimeError):
    pass


@dataclass
class CacheInfo:
    """Provenance of the cached copy, quoted in generated DBC headers."""

    path: Path
    url: str
    sha256: str
    size: int
    fetched_at: str
    last_modified: str = ""

    def short(self) -> str:
        return f"{self.url} sha256:{self.sha256[:12]} fetched {self.fetched_at}"


def cache_dir() -> Path:
    """Cache location.  Honours DBCGEN_CACHE, then XDG_CACHE_HOME."""
    env = os.environ.get("DBCGEN_CACHE")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "dbcgen"


def _meta_path(directory: Path) -> Path:
    return directory / META_NAME


def read_cache_info(directory: Optional[Path] = None) -> Optional[CacheInfo]:
    directory = directory or cache_dir()
    archive, meta = directory / EXPORT_NAME, _meta_path(directory)
    if not archive.exists() or not meta.exists():
        return None
    try:
        d = json.loads(meta.read_text())
    except (OSError, ValueError):
        return None
    return CacheInfo(
        path=archive,
        url=d.get("url", EXPORT_URL),
        sha256=d.get("sha256", ""),
        size=d.get("size", 0),
        fetched_at=d.get("fetched_at", ""),
        last_modified=d.get("last_modified", ""),
    )


def _body_hint(payload: bytes) -> str:
    """Describe a non-archive response, so a failure is diagnosable from the
    error alone rather than from server logs nobody has."""
    if not payload:
        return ""
    head = payload[:80].decode("utf-8", "replace").replace("\n", " ").strip()
    return f" -- last response was {len(payload)} bytes beginning {head!r}"


def _validate(payload: bytes) -> None:
    """Refuse anything that is not the archive we expect.

    A captive portal or an error page would otherwise be cached as if it were
    the standard, and every DBC built afterwards would silently lose its
    authoritative layer.
    """
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(payload)) as z:
            names = set(z.namelist())
    except zipfile.BadZipFile as exc:
        raise StandardDataError(
            f"downloaded {len(payload)} bytes from the standard export URL, but it "
            f"is not a zip archive"
        ) from exc
    missing = [m for m in REQUIRED_MEMBERS if m not in names]
    if missing:
        raise StandardDataError(
            f"standard export is missing expected member(s): {', '.join(missing)}"
        )


#: One transient failure reaching isobus.net should not end a run: it is a
#: single free server and the file is small, so a couple of spaced retries cost
#: nothing and remove the commonest reason a first run fails.
FETCH_ATTEMPTS = 3
FETCH_BACKOFF_S = 2.0


def fetch(directory: Optional[Path] = None, timeout: int = 120) -> CacheInfo:
    """Download the export and replace the cached copy."""
    directory = directory or cache_dir()
    directory.mkdir(parents=True, exist_ok=True)

    req = urllib.request.Request(EXPORT_URL, headers={"User-Agent": USER_AGENT})
    last_error: Optional[Exception] = None
    payload = b""
    last_modified = ""
    for attempt in range(FETCH_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
                last_modified = resp.headers.get("Last-Modified", "")
            # Validation is inside the loop on purpose.  The server answers a
            # request it will not serve with 200 and an HTML page rather than an
            # HTTP error, so "downloaded successfully" and "downloaded the
            # export" are different things, and only the second may stop the
            # retries.  Validating after the loop is what made a concurrent
            # request fail in one second with no retry at all.
            _validate(payload)
            break
        except Exception as exc:  # network, TLS, HTTP, or a non-archive body
            last_error = exc
            if attempt + 1 < FETCH_ATTEMPTS:
                time.sleep(FETCH_BACKOFF_S * (attempt + 1))
    else:
        raise StandardDataError(
            f"could not fetch {EXPORT_URL} after {FETCH_ATTEMPTS} attempts: "
            f"{last_error}{_body_hint(payload)}"
        ) from last_error

    archive = directory / EXPORT_NAME
    tmp = archive.with_suffix(".zip.part")
    tmp.write_bytes(payload)
    tmp.replace(archive)  # atomic, so a failed run cannot leave a torn cache

    info = CacheInfo(
        path=archive,
        url=EXPORT_URL,
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        last_modified=last_modified,
    )
    _meta_path(directory).write_text(
        json.dumps(
            {
                "url": info.url,
                "sha256": info.sha256,
                "size": info.size,
                "fetched_at": info.fetched_at,
                "last_modified": info.last_modified,
            },
            indent=2,
        )
    )
    return info


def ensure(directory: Optional[Path] = None, refresh: bool = False) -> CacheInfo:
    """Return a cached export, downloading only if absent or `refresh` is set."""
    directory = directory or cache_dir()
    if not refresh:
        info = read_cache_info(directory)
        if info is not None:
            return info
    return fetch(directory)


def open_member(info: CacheInfo, member: str) -> str:
    """Read one CSV member as text, stripping the UTF-8 BOM the export carries."""
    with zipfile.ZipFile(info.path) as z:
        return z.read(member).decode("utf-8-sig", errors="replace")
