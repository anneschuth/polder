"""Tests voor persoon-matcher in polder.resolve.

Werkt tegen de echte `data/personen/`-dataset (read-only). Tests checken dat
voor een proposal-name-string de juiste `person:*`-id wordt teruggegeven —
de ground truth is wat de eerdere resolve-skill heeft gevonden.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Ground-truth-cases uit echte resolved-staging-files.
# Format: (proposal_name, expected_person_id).
# Deze cases moeten code-only matchen, zonder LLM-fallback.
PERSON_MATCH_CASES = [
    ("Mark Rutte", "person:rutte-m-1967"),
    ("P.A. Dijkstra", "person:dijkstra-pa-1954"),
    ("T.H.D. Struycken", "person:struycken-1969"),
    ("E. van Marum", "person:marum-1968"),
    ("drs. F. Zsolt Szabó", "person:szabo-fz-1961"),
]

# Cases waar code-only NIET kan disambigueren — er zijn meerdere candidates
# en de data heeft geen initials/birth-year om uniciteit te bepalen. Deze
# cases zijn legitieme LLM-fallback-input.
PERSON_AMBIGUOUS_CASES = [
    # 'drs. H.W.M. Schoof' matched aan zowel Dick Schoof (1957, geen initials
    # in record) als Schoof I. (7761538, eveneens geen initials/year).
    ("drs. H.W.M. Schoof", ("person:schoof-1957", "person:schoof-i-7761538")),
]


@pytest.fixture(scope="module")
def polder_index():
    """Bouw de PolderIndex eenmaal voor alle tests in deze module."""
    from polder.resolve.matcher import PolderIndex

    return PolderIndex.load(Path("data"))


@pytest.mark.parametrize("name,expected_person_id", PERSON_MATCH_CASES)
def test_match_person_ground_truth(polder_index, name: str, expected_person_id: str) -> None:
    from polder.resolve.matcher import match_person

    result = match_person(name, idx=polder_index)
    assert result.person_id == expected_person_id, (
        f"voor {name!r}: kreeg {result.person_id!r} (method={result.method}, "
        f"confidence={result.confidence}), verwachtte {expected_person_id!r}"
    )
    # family-unique heeft confidence 0.70, andere methodes >= 0.85.
    assert result.confidence >= 0.70


@pytest.mark.parametrize("name,expected_candidates", PERSON_AMBIGUOUS_CASES)
def test_match_person_ambiguous(
    polder_index, name: str, expected_candidates: tuple[str, ...]
) -> None:
    """Cases waar code-only meerdere kandidaten vindt: rapporteer ambiguïteit."""
    from polder.resolve.matcher import match_person

    result = match_person(name, idx=polder_index)
    assert result.person_id is None
    assert "ambiguous" in result.method
    # Alle expected_candidates zitten in de uitkomst.
    for candidate in expected_candidates:
        assert candidate in result.candidates


def test_match_person_no_family(polder_index) -> None:
    from polder.resolve.matcher import match_person

    result = match_person("", idx=polder_index)
    assert result.person_id is None
    assert result.confidence == 0.0


def test_match_person_nonexistent(polder_index) -> None:
    from polder.resolve.matcher import match_person

    result = match_person("Marsmenneke Asdfqwer", idx=polder_index)
    assert result.person_id is None
    assert result.confidence == 0.0


def test_match_person_creatable_new(polder_index) -> None:
    """Onbekende family + birth_year = creatable; apply mag een nieuwe persoon maken."""
    from polder.resolve.matcher import match_person

    result = match_person("Marsmenneke Asdfqwer", idx=polder_index, birth_year=1980)
    assert result.person_id is None
    assert result.method == "creatable_new_person"
    assert result.confidence == 0.85


def test_match_person_no_creatable_without_year(polder_index) -> None:
    """Zonder birth_year géén creatable; resolver verrijkt eerst via Wikidata."""
    from polder.resolve.matcher import match_person

    result = match_person("Marsmenneke Asdfqwer", idx=polder_index)
    assert result.confidence == 0.0
    assert result.method == "no_match"
