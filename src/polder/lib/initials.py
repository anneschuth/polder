"""Centrale helpers voor het normaliseren van persoonsinitialen.

Twee normalisaties, voor twee doelen:

- ``format_initials(s) -> "M.P."``: voor schrijven naar YAML. Resultaat
  matcht het schema-pattern `^([A-Z]\\.)+$`. Voorbeelden:
  ``"mp"`` → ``"M.P."``, ``"M P"`` → ``"M.P."``, ``"M.P"`` → ``"M.P."``.

- ``compact_initials(s) -> "mp"``: voor matching-keys (audit-dedup,
  fetcher-merge, lookup-tabellen). Alleen letters, lowercase, geen punten.
  Voorbeelden: ``"M.P."`` → ``"mp"``, ``"W.B."`` → ``"wb"``.

Deze module vervangt de losse `_normalize_initials` / `_initials_slug` /
`_initials_from_given` helpers die in elke fetcher apart bestonden.
"""

from __future__ import annotations

import re
import unicodedata


def _to_ascii(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")


def format_initials(value: str | None) -> str | None:
    """Geef YAML-formaat: ``"M.P."``. None als input leeg.

    Strips alle niet-letters, maakt letters uppercase, plakt punt achter elke.
    Matcht het schema-pattern `^([A-Z]\\.)+$`.
    """
    if not value:
        return None
    cleaned = _to_ascii(value)
    letters = re.findall(r"[A-Za-z]", cleaned)
    if not letters:
        return None
    return "".join(f"{ch.upper()}." for ch in letters)


def compact_initials(value: str | None) -> str:
    """Geef key-formaat: ``"mp"``. Lege string als input leeg.

    Strip alle niet-letters, maak letters lowercase.
    """
    if not value:
        return ""
    cleaned = _to_ascii(value)
    letters = re.findall(r"[A-Za-z]", cleaned)
    return "".join(letters).lower()


def compact_initials_loose(value: str | None) -> str:
    """Compact-vorm waarbij Nederlandse digraph-initialen tot één letter
    worden ingekort.

    Nederlandse naam-conventie laat "Th" of "Ph" als één klank tellen:
    "Theo" wordt soms als "Th." geschreven, soms als "T.". Onze data en
    KB-bronnen wisselen daarin. Voor matching gebruiken we deze vorm als
    secundaire key:

      compact_initials("S.Th.M.")        -> "sthm"   (strict)
      compact_initials_loose("S.Th.M.")  -> "stm"    (digraph-collapsed)
      compact_initials("S.T.M.")         -> "stm"    (al stm)
      compact_initials_loose("S.T.M.")   -> "stm"    (geen digraph, gelijk)

    Match-strategieen kunnen beide keys proberen om data-of-bron-drift af
    te vangen zonder valse positives op compleet andere initialen.
    """
    if not value:
        return ""
    cleaned = _to_ascii(value)
    # Knip "h" direct na een hoofdletter weg ("Th" -> "T", "Ph" -> "P", "Ch"
    # -> "C", "Jh" -> "J"). Doe dit voor letter-extractie zodat zowel
    # "S.Th.M." als "STh M" naar "stm" collapsen.
    collapsed = re.sub(r"([A-Z])h", r"\1", cleaned)
    letters = re.findall(r"[A-Za-z]", collapsed)
    return "".join(letters).lower()


def merge_initials(a: str | None, b: str | None) -> str | None:
    """Combineer twee initialen-strings, kies de meest informatieve.

    Bij merge-conflicten (bv. ORI levert ``"W."`` en TK OData levert ``"W.B."``)
    wint de langste compact-vorm. Als geen van beide langer is dan de andere
    en de korte is een prefix van de lange, retourneer de lange. Anders het
    eerste niet-lege argument.
    """
    ca = compact_initials(a)
    cb = compact_initials(b)
    if not ca:
        return format_initials(b) if cb else None
    if not cb:
        return format_initials(a)
    if ca == cb:
        return format_initials(a)
    if ca.startswith(cb):
        return format_initials(a)
    if cb.startswith(ca):
        return format_initials(b)
    # Beide niet prefix van elkaar: behoud het eerste argument (caller-keuze).
    return format_initials(a)
