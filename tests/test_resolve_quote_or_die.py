"""Tests voor `polder.resolve.quote_or_die`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx

from polder.resolve.quote_or_die import make_verifier


def test_rejects_url_outside_allowlist(tmp_path: Path) -> None:
    verify = make_verifier(cache_dir=tmp_path)
    assert verify("any snippet", "https://example.com/article") is False


def test_accepts_when_snippet_in_fetched_body(tmp_path: Path) -> None:
    body = (
        "<html><body>"
        "<p>Esther van Deursen (geboren 1972, Amsterdam) is sinds 2018 "
        "directeur Toezicht mbo bij de Inspectie van het Onderwijs.</p>"
        "</body></html>"
    )

    class _Response:
        text = body

        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, url: str):
            assert "wikidata" in url
            return _Response()

    verify = make_verifier(cache_dir=tmp_path)
    with patch("polder.resolve.quote_or_die.httpx.Client", _Client):
        assert (
            verify(
                "Esther van Deursen (geboren 1972, Amsterdam)",
                "https://www.wikidata.org/wiki/Q1",
            )
            is True
        )


def test_rejects_when_snippet_not_in_body(tmp_path: Path) -> None:
    body = "<html><body><p>Een hele andere tekst zonder de claim.</p></body></html>"

    class _Response:
        text = body

        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, url: str):
            return _Response()

    verify = make_verifier(cache_dir=tmp_path)
    with patch("polder.resolve.quote_or_die.httpx.Client", _Client):
        assert (
            verify(
                "fictieve claim die niet in de tekst staat",
                "https://www.wikidata.org/wiki/Q1",
            )
            is False
        )


def test_normalizes_accents_and_whitespace(tmp_path: Path) -> None:
    body = "<p>Andre van der Berg geboren 1965</p>"

    class _Response:
        text = body

        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, url: str):
            return _Response()

    verify = make_verifier(cache_dir=tmp_path)
    with patch("polder.resolve.quote_or_die.httpx.Client", _Client):
        # Snippet met accent en extra whitespace moet matchen op kale body.
        assert (
            verify(
                "André  van der   Berg geboren 1965",
                "https://nl.wikipedia.org/wiki/X",
            )
            is True
        )


def test_cache_avoids_second_fetch(tmp_path: Path) -> None:
    body = "<p>Bevat de quote letterlijk</p>"
    calls = {"n": 0}

    class _Response:
        text = body

        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, url: str):
            calls["n"] += 1
            return _Response()

    verify = make_verifier(cache_dir=tmp_path)
    with patch("polder.resolve.quote_or_die.httpx.Client", _Client):
        assert verify("bevat de quote letterlijk", "https://www.wikidata.org/wiki/Q1")
        assert verify("bevat de quote letterlijk", "https://www.wikidata.org/wiki/Q1")
    assert calls["n"] == 1, "tweede call moet uit cache komen"


def test_network_error_returns_false(tmp_path: Path) -> None:
    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, url: str):
            raise httpx.HTTPError("connection refused")

    verify = make_verifier(cache_dir=tmp_path)
    with patch("polder.resolve.quote_or_die.httpx.Client", _Client):
        assert verify("any text", "https://www.wikidata.org/wiki/Q1") is False


def test_empty_snippet_or_url_returns_false(tmp_path: Path) -> None:
    verify = make_verifier(cache_dir=tmp_path)
    assert verify("", "https://www.wikidata.org/wiki/Q1") is False
    assert verify("some quote", "") is False
