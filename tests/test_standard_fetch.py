"""Fetching the standard export.

The export comes from one free server that answers a request it will not serve
with 200 and an HTML page rather than an HTTP error.  These tests pin the two
behaviours that follow from that, both of which were learned from a CI failure.
"""

import pytest

from dbcgen.standard import fetch


def test_a_non_archive_response_is_refused_not_cached():
    """An error page must never be cached as if it were the standard."""
    with pytest.raises(fetch.StandardDataError):
        fetch._validate(b"<!DOCTYPE html><html>Service Unavailable</html>")


def test_a_non_archive_response_is_retried(monkeypatch, tmp_path):
    """A body that is not the archive has to re-enter the retry loop.

    Validating after the loop instead of inside it is what made a transient
    failure fatal in one second, with none of the three attempts used.
    """
    attempts = []
    real_zip = (fetch.cache_dir() / fetch.EXPORT_NAME)
    if not real_zip.exists():
        pytest.skip("standard export not cached; run 'dbcgen fetch-standard'")
    good = real_zip.read_bytes()

    class FakeResponse:
        def __init__(self, body):
            self.body = body
            self.headers = {}

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        attempts.append(1)
        # Fail the way the server actually fails, then succeed.
        return FakeResponse(b"<!DOCTYPE html><html>busy</html>"
                            if len(attempts) == 1 else good)

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)

    info = fetch.fetch(tmp_path)
    assert len(attempts) == 2, "the bad body should have been retried"
    assert info.path.exists()


def test_the_error_says_what_came_back():
    """A failure has to be diagnosable without the server's logs."""
    body = b"<!DOCTYPE html><title>Error</title>"
    hint = fetch._body_hint(body)
    assert f"{len(body)} bytes" in hint and "DOCTYPE" in hint
    assert fetch._body_hint(b"") == ""
