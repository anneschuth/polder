"""Tests voor naam-parsing in polder.resolve.

Ground-truth-cases uit echte staging-files. Elk paar `(input, expected)` komt
uit een resolved-file waar we de match-output kennen.
"""

from __future__ import annotations

import pytest


# Cases uit echte resolved-staging-files. Format:
#   (proposal_name, expected_family, expected_given_or_nickname, expected_initials_compact)
#
# initials_compact is de lowercased letters zonder punten ("M.P." -> "mp").
# given is de roepnaam (uit parens of als voluit-voornaam beschikbaar).
NAME_CASES = [
    # Klassiek: voornaam achternaam
    ("Mark Rutte", "rutte", "mark", None),
    # Klassiek met tussenvoegsel
    ("Jaimi van Essen", "essen", "jaimi", None),
    ("E. van Marum", "marum", None, "e"),
    # Initialen + family
    ("P.A. Dijkstra", "dijkstra", None, "pa"),
    ("G.E.W. van Leeuwen", "leeuwen", None, "gew"),
    ("T.H.D. Struycken", "struycken", None, "thd"),
    # Initialen + (roepnaam) + family
    ("A.I. (Abigail) Norville MSc", "norville", "abigail", "ai"),
    ("E.E.M. (Esther) Deursen", "deursen", "esther", "eem"),
    ("E.J. (Erik Jan) van Kempen", "kempen", "erik jan", "ej"),
    # Honorific-prefix
    ("drs. H.W.M. Schoof", "schoof", None, "hwm"),
    ("drs. F. Zsolt Szabó", "szabo", "zsolt", "f"),
    # Honorific-suffix
    ("Adrie Kerkvliet RE RA", "kerkvliet", "adrie", None),
]


@pytest.mark.parametrize("name,expected_family,expected_given,expected_initials", NAME_CASES)
def test_parse_person_name_ground_truth(
    name: str,
    expected_family: str,
    expected_given: str | None,
    expected_initials: str | None,
) -> None:
    from polder.resolve.names import parse_person_name

    parsed = parse_person_name(name)
    assert parsed.family == expected_family, f"family mismatch voor {name!r}: {parsed!r}"
    assert parsed.given == expected_given, f"given mismatch voor {name!r}: {parsed!r}"
    assert parsed.initials == expected_initials, f"initials mismatch voor {name!r}: {parsed!r}"


def test_parse_person_name_empty() -> None:
    from polder.resolve.names import parse_person_name

    parsed = parse_person_name("")
    assert parsed.family == ""
    assert parsed.given is None
    assert parsed.initials is None


def test_parse_person_name_only_family() -> None:
    from polder.resolve.names import parse_person_name

    parsed = parse_person_name("Schoof")
    assert parsed.family == "schoof"
    assert parsed.given is None
    assert parsed.initials is None
