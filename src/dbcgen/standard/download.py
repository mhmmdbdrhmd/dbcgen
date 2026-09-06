"""Fetching and caching an arbitrary named asset.

`fetch.py` handles the one asset this tool has always needed -- the ISOBUS
export -- and does it with that file's own naming and validation baked in.  The
source registry needs the same behaviour for several unrelated files, so the
generic part lives here: download once, keep it under the cache directory, note
where it came from, and never vendor it into the repository.

The retry rule is the one learned from the ISOBUS server: a host that will not
serve a request may answer 200 with an HTML page rather than an HTTP error, so
"the download succeeded" and "the download is the file" are separate questions
and only the second may stop the retries.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .fetch import (
    FETCH_ATTEMPTS,
    FETCH_BACKOFF_S,
    USER_AGENT,
    CacheInfo,
    StandardDataError,
    _body_hint,
    cache_dir,
)

#: A validator returns nothing and raises StandardDataError if the bytes are
#: not the asset that was asked for.
Validator = Callable[[bytes], None]


@dataclass(frozen=True)
class Asset:
    """One downloadable file the registry knows how to get."""

    name: str
    url: str
    filename: str
    licence: str
    attribution: str
    validate: Optional[Validator] = None


def _meta_for(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".meta.json")


def read_cached(asset: Asset, directory: Optional[Path] = None) -> Optional[CacheInfo]:
    """The cached copy, or None when it has not been fetched yet."""
    directory = directory or cache_dir()
    path = directory / asset.filename
    meta = _meta_for(path)
    if not (path.exists() and meta.exists()):
        return None
    try:
        d = json.loads(meta.read_text())
    except (OSError, ValueError):
        return None
    return CacheInfo(
        path=path,
        url=d.get("url", asset.url),
        sha256=d.get("sha256", ""),
        size=d.get("size", 0),
        fetched_at=d.get("fetched_at", ""),
        last_modified=d.get("last_modified", ""),
    )


def download(
    asset: Asset, directory: Optional[Path] = None, timeout: int = 120
) -> CacheInfo:
    """Fetch the asset and replace any cached copy."""
    directory = directory or cache_dir()
    directory.mkdir(parents=True, exist_ok=True)

    req = urllib.request.Request(asset.url, headers={"User-Agent": USER_AGENT})
    last_error: Optional[Exception] = None
    payload = b""
    last_modified = ""
    for attempt in range(FETCH_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
                last_modified = resp.headers.get("Last-Modified", "")
            if asset.validate is not None:
                asset.validate(payload)
            break
        except Exception as exc:  # network, TLS, HTTP, or a body that is not it
            last_error = exc
            if attempt + 1 < FETCH_ATTEMPTS:
                time.sleep(FETCH_BACKOFF_S * (attempt + 1))
    else:
        raise StandardDataError(
            f"could not fetch {asset.name} from {asset.url} after "
            f"{FETCH_ATTEMPTS} attempts: {last_error}{_body_hint(payload)}"
        ) from last_error

    path = directory / asset.filename
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(payload)
    tmp.replace(path)  # atomic, so a failed run cannot leave a torn cache

    info = CacheInfo(
        path=path,
        url=asset.url,
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        last_modified=last_modified,
    )
    _meta_for(path).write_text(
        json.dumps(
            {
                "url": info.url,
                "sha256": info.sha256,
                "size": info.size,
                "fetched_at": info.fetched_at,
                "last_modified": info.last_modified,
                "licence": asset.licence,
                "attribution": asset.attribution,
            },
            indent=2,
        )
    )
    return info


def ensure(
    asset: Asset, directory: Optional[Path] = None, refresh: bool = False
) -> CacheInfo:
    """Return a cached asset, downloading only if absent or `refresh` is set."""
    if not refresh:
        info = read_cached(asset, directory)
        if info is not None:
            return info
    return download(asset, directory)
