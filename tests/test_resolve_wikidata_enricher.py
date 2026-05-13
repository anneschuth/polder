"""Tests voor `make_wikidata_enricher` zonder netwerk.

We mocken `lookup_person_by_name` zodat we de strictheid van label-match en
plausibele-leeftijd-filter kunnen testen zonder echte Wikidata-calls.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch


def test_label_matches_strict() -> None:
    from polder.resolve.wikidata_enricher import _label_matches

    assert _label_matches("geurtsen", "evelyn", "Evelyn Geurtsen")
    assert _label_matches("geurtsen", "evelyn", "E. Geurtsen")
    assert _label_matches("geurtsen", None, "E. Geurtsen")
    # Family ontbreekt in label -> geen match
    assert not _label_matches("geurtsen", "evelyn", "Evelyn Janssen")
    # Given ontbreekt -> geen match
    assert not _label_matches("geurtsen", "evelyn", "Jan Geurtsen")


def test_plausible_age_window() -> None:
    from polder.resolve.wikidata_enricher import _is_plausible_age

    today = date(2026, 1, 1)
    assert _is_plausible_age(2000, today=today)  # 26 jaar
    assert _is_plausible_age(1950, today=today)  # 76 jaar
    assert not _is_plausible_age(1940, today=today)  # 86 jaar — te oud
    assert not _is_plausible_age(2020, today=today)  # 6 jaar — te jong


def test_enricher_returns_year_for_unique_plausible(tmp_path: Path) -> None:
    from polder.resolve.wikidata_enricher import make_wikidata_enricher

    fake = [
        {"qid": "Q1", "label": "Evelyn Geurtsen", "birth_year": 1975, "description": "ambtenaar"},
    ]
    enricher = make_wikidata_enricher(cache_dir=tmp_path)
    with patch("polder.resolve.wikidata_enricher.lookup_person_by_name", return_value=fake):
        assert enricher("Evelyn Geurtsen", None) == 1975


def test_enricher_skips_implausible_age(tmp_path: Path) -> None:
    """Een wikidata-hit op 'Evelyn Geurtsen, 1942' is te oud — niet accepteren."""
    from polder.resolve.wikidata_enricher import make_wikidata_enricher

    fake = [
        {
            "qid": "Q1",
            "label": "Evelyn Geurtsen",
            "birth_year": 1942,
            "description": "historische persoon",
        },
    ]
    enricher = make_wikidata_enricher(cache_dir=tmp_path)
    with patch("polder.resolve.wikidata_enricher.lookup_person_by_name", return_value=fake):
        # 1942 valt buiten het 18-80-window in 2026 (84 jaar oud).
        assert enricher("Evelyn Geurtsen", None) is None


def test_enricher_skips_ambiguous_candidates(tmp_path: Path) -> None:
    """Twee plausibele matches -> niet auto-disambigueren."""
    from polder.resolve.wikidata_enricher import make_wikidata_enricher

    fake = [
        {"qid": "Q1", "label": "Jan Jansen", "birth_year": 1970, "description": "x"},
        {"qid": "Q2", "label": "Jan Jansen", "birth_year": 1980, "description": "y"},
    ]
    enricher = make_wikidata_enricher(cache_dir=tmp_path)
    with patch("polder.resolve.wikidata_enricher.lookup_person_by_name", return_value=fake):
        assert enricher("Jan Jansen", None) is None


def test_enricher_skips_label_mismatch(tmp_path: Path) -> None:
    """Wikidata levert verkeerde familie terug -> niet accepteren."""
    from polder.resolve.wikidata_enricher import make_wikidata_enricher

    fake = [
        {"qid": "Q1", "label": "Evelyn Janssen", "birth_year": 1975, "description": "x"},
    ]
    enricher = make_wikidata_enricher(cache_dir=tmp_path)
    with patch("polder.resolve.wikidata_enricher.lookup_person_by_name", return_value=fake):
        assert enricher("Evelyn Geurtsen", None) is None


def test_enricher_honors_existing_birth_hint(tmp_path: Path) -> None:
    """Als proposal al een birth-year heeft, geen wikidata-call doen."""
    from polder.resolve.wikidata_enricher import make_wikidata_enricher

    calls = []

    def spy(*args, **kwargs):
        calls.append(args)
        return []

    enricher = make_wikidata_enricher(cache_dir=tmp_path)
    with patch("polder.resolve.wikidata_enricher.lookup_person_by_name", side_effect=spy):
        result = enricher("Evelyn Geurtsen", 1980)
    assert result == 1980
    assert calls == []
