"""Pure naam-parsing voor staging-proposals.

Decompose een proposal-name-string als `'A.I. (Abigail) Norville MSc'` naar
(family, given, initials) zodat we daarna deterministische lookups kunnen
doen tegen `data/personen/`.

Bouwt voort op `polder.lib.initials` voor de initials-normalisatie en op
het ORI-patroon `'<initials> (<nickname>) <tussenvoegsel> <family> [<post-honorifics>]'`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from polder.lib.initials import compact_initials, compact_initials_loose


# Honorific-tokens die als prefix vóór de eigenlijke naam staan ("drs.", "mr.").
# Lowercase, met optionele trailing punt.
HONORIFIC_PREFIXES = {
    "drs",
    "ir",
    "mr",
    "dr",
    "prof",
    "ing",
    "bc",
    "ds",
    "ds.",
}

# Honorific-tokens die als suffix achter de naam staan. Pure hoofdletters
# (zoals "RE", "RA", "RC") of bekende graden met gemengde casing ("MSc", "MBA",
# "BSc", "PhD").
HONORIFIC_SUFFIX_MAX_LEN = 4
HONORIFIC_SUFFIX_GRADES = {
    "msc", "mba", "bsc", "phd", "ma", "ba", "llm", "llb",
    "mscba", "msca", "emba",
}


@dataclass(frozen=True)
class ParsedName:
    """Resultaat van `parse_person_name`. Alle velden lowercased ASCII."""

    family: str
    given: str | None
    initials: str | None  # compact-vorm zonder punten, bv. "mp"
    initials_loose: str | None = None  # digraph-collapsed ("S.Th.M." -> "stm")


def _to_ascii_lower(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return decomposed.encode("ascii", "ignore").decode("ascii").lower()


def _is_initials_token(token: str) -> bool:
    """`M.P.` of `M.` is een initialen-token."""
    return bool(token) and token.endswith(".") and all(
        c.isalpha() or c == "." for c in token
    )


def _strip_honorific_prefixes(tokens: list[str]) -> list[str]:
    out = list(tokens)
    while out:
        head = out[0].rstrip(".").lower()
        if head in {h.rstrip(".") for h in HONORIFIC_PREFIXES}:
            out.pop(0)
        else:
            break
    return out


def _strip_honorific_suffixes(tokens: list[str]) -> list[str]:
    out = list(tokens)
    while out:
        tail = out[-1]
        tail_clean = tail.rstrip(".").lower()
        is_all_caps = tail.isupper() and 1 <= len(tail) <= HONORIFIC_SUFFIX_MAX_LEN
        is_known_grade = tail_clean in HONORIFIC_SUFFIX_GRADES
        if is_all_caps or is_known_grade:
            out.pop()
        else:
            break
    return out


# Tussenvoegsels herkennen we structureel niet op een vaste lijst, maar via een
# heuristiek: een token dat helemaal lowercase begint hoort niet bij een
# voornaam (Nederlandse voornamen beginnen met hoofdletter) en niet bij de
# family (die staat ervoor). Eventuele apostrof-vormen ("'t") tellen ook mee.
def _looks_like_tussenvoegsel(token: str) -> bool:
    if not token:
        return False
    first = token[0]
    return first.islower() or first in ("'", "‘")


_PARENS_RX = re.compile(r"\(([^)]+)\)")


def parse_person_name(name: str) -> ParsedName:
    """Decompose een proposal-name in (family, given, initials).

    Strategie:
    1. Strip honorific-prefixes (drs., mr., prof.).
    2. Pak nickname uit `(...)` als aanwezig.
    3. Strip honorific-suffixes (MSc, RE, RA).
    4. Token-walk: initialen-tokens (eindigen op `.`) → initials.
       Tussenvoegsel-tokens (lowercase-prefix) overslaan voor family-pos.
       Laatste niet-lowercase-token = family.
       Eerste niet-initialen-token vóór family = voluit-given (als geen
       nickname uit parens).
    """
    if not name or not name.strip():
        return ParsedName("", None, None, None)

    raw = name.strip()

    # 1. Pak nickname uit parens (eerste match wint).
    nickname: str | None = None
    nick_match = _PARENS_RX.search(raw)
    if nick_match:
        nickname = nick_match.group(1).strip()
        raw = (raw[: nick_match.start()] + " " + raw[nick_match.end() :]).strip()

    # Splits op spaties, behoudt punten in tokens.
    tokens = [t for t in raw.split() if t]
    tokens = _strip_honorific_prefixes(tokens)
    tokens = _strip_honorific_suffixes(tokens)

    if not tokens:
        family = ""
        given = _to_ascii_lower(nickname) if nickname else None
        return ParsedName(family, given, None, None)

    # 2. Initialen-tokens vooraan verzamelen.
    initials_tokens: list[str] = []
    while tokens and _is_initials_token(tokens[0]):
        initials_tokens.append(tokens.pop(0))
    initials_raw = "".join(initials_tokens)
    initials_compact = compact_initials(initials_raw) or None
    initials_loose = compact_initials_loose(initials_raw) or None

    if not tokens:
        # Alleen initialen, geen family in input
        given = _to_ascii_lower(nickname) if nickname else None
        return ParsedName("", given, initials_compact, initials_loose)

    # 3. Laatste token is family (na suffix-stripping). Tussenvoegsel-tokens
    # tussen given en family overslaan voor family-positie.
    family = _to_ascii_lower(tokens[-1])
    rest = tokens[:-1]

    # 4. Given: nickname als die uit parens kwam, anders het eerste
    # niet-tussenvoegsel-token vóór family (als die geen initiaal was).
    given: str | None
    if nickname:
        given = _to_ascii_lower(nickname)
    else:
        # Pak alle "echte" tokens (niet-tussenvoegsel) uit `rest`.
        given_tokens = [t for t in rest if not _looks_like_tussenvoegsel(t)]
        if given_tokens:
            given = " ".join(_to_ascii_lower(t) for t in given_tokens)
        else:
            given = None

    return ParsedName(family, given, initials_compact, initials_loose)
