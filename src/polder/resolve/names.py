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
    "em",  # emeritus, bv. "Em. prof. dr. André van der Zande"
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
    "msc",
    "mba",
    "bsc",
    "phd",
    "ma",
    "ba",
    "llm",
    "llb",
    "mscba",
    "msca",
    "emba",
}


@dataclass(frozen=True)
class ParsedName:
    """Resultaat van `parse_person_name`. Alle velden lowercased ASCII."""

    family: str
    given: str | None
    initials: str | None  # compact-vorm zonder punten, bv. "mp"
    initials_loose: str | None = None  # digraph-collapsed ("S.Th.M." -> "stm")
    family_full: str | None = None
    """Volledige familienaam inclusief tussenvoegsels, spaties als '-'
    (bv. 'van-der-zande', 'van-oudenhoven-van-der-zee'). `family` blijft
    het matching-segment (Wikidata-conventie); `family_full` is bedoeld
    voor leesbare slug-generatie. None als gelijk aan `family`."""


def _to_ascii_lower(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return decomposed.encode("ascii", "ignore").decode("ascii").lower()


def _is_initials_token(token: str) -> bool:
    """`M.P.` of `M.` is een initialen-token."""
    return bool(token) and token.endswith(".") and all(c.isalpha() or c == "." for c in token)


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
    return first.islower() or first in ("'", "‘")  # noqa: RUF001


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
    #
    # Compound-namen met streepje en tussenvoegsels in het midden ("van
    # Veldhoven-van der Meer", "Joosse - de Visser") splitsen op spaties
    # niet correct: een naive tokens[-1] pakt alleen "Meer" of "Visser".
    # Voor matching naar onze data willen we de meest-gebruikelijke vorm
    # (Wikipedia/Wikidata-conventie), wat doorgaans het EERSTE segment
    # is: "Veldhoven" of "Joosse". Als het laatste segment vóór de streep
    # met een hoofdletter begint én ergens later een streep-vorm met
    # tussenvoegsel volgt, pak dan dat eerste segment.
    family = _to_ascii_lower(tokens[-1])
    rest = tokens[:-1]

    # Compound met streepje EN tussenvoegsels detecteren: "Veldhoven-van"
    # met daarna "der Meer" is een meertraps achternaam (van Veldhoven-van
    # der Meer). Pak in dat geval het eerste segment voor de streep als
    # family. Streepjes tussen twee hoofdletters ("Gooiker-Loeffen") zijn
    # gewoon compound family en blijven intact via tokens[-1].
    for tok in tokens:
        # Token vorm "Xxxx-yyy" waar yyy met kleine letter begint (tussen-
        # voegsel-binnen-streep), gevolgd door extra tussenvoegsels: pak
        # eerste segment.
        if "-" in tok and tok[0].isupper():
            head, _, tail = tok.partition("-")
            if tail and tail[0].islower():
                family = _to_ascii_lower(head)
                rest = [t for t in tokens if t is not tok and not _looks_like_tussenvoegsel(t)]
                break

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

    # 5. family_full: volledige familienaam incl. tussenvoegsels, bedoeld
    # voor leesbare slug-generatie. De familie begint bij het eerste
    # tussenvoegsel ná de given-tokens en loopt door tot het eind. Streepjes
    # en spaties worden '-' (slug-vorm). None als gelijk aan `family`.
    family_full: str | None = None

    def _looks_like_initials(tok: str) -> bool:
        # "B.J.L", "B.J.L." of losse "A." — bevat een punt of is een korte
        # all-caps reeks. Mag niet in family_full lekken.
        if "." in tok:
            return True
        return tok.isupper() and len(tok) <= 4

    given_set = set(given_tokens) if not nickname else set()
    fam_tokens: list[str] = []
    for tok in tokens:
        if _looks_like_initials(tok):
            continue
        if tok in given_set and not fam_tokens:
            continue
        if not fam_tokens and not _looks_like_tussenvoegsel(tok) and tok is not tokens[-1]:
            # Nog in given-zone (echte voornaam-token, geen tussenvoegsel).
            if not nickname:
                continue
        fam_tokens.append(tok)
    if fam_tokens:
        joined = "-".join(_to_ascii_lower(t) for t in fam_tokens)
        joined = re.sub(r"-{2,}", "-", joined).strip("-")
        if joined and joined != family:
            family_full = joined

    return ParsedName(family, given, initials_compact, initials_loose, family_full)


# Honorific-prefix-tokens als regex-alternatie, voor leesbare-naam-strip.
_HONORIFIC_PREFIX_RX = re.compile(
    r"^((?:" + "|".join(sorted(h.rstrip(".") for h in HONORIFIC_PREFIXES)) + r")\.?\s+)+",
    re.IGNORECASE,
)


def readable_name_parts(name_full: str) -> dict[str, str]:
    """Bouw een leesbare persoon.name-dict uit een volledige naam-string.

    Gebruikt dezelfde token-structuur als `parse_person_name` (één parser
    voor de hele codebase), maar behoudt de originele casing en
    tussenvoegsels zodat het record leesbaar is: family 'van der Zande',
    niet 'zande' of 'van-der-zande'.

    Retourneert minstens `{full, family}`, plus `given` en `initials`
    indien afleidbaar.
    """
    full = name_full.strip()
    parsed = parse_person_name(name_full)

    # Nickname uit parens: dat is de leesbare given ('Lilian', 'Karen').
    nick_match = _PARENS_RX.search(full)
    nickname = nick_match.group(1).strip() if nick_match else None

    # Strip honorific-prefixes + parenthese-nickname uit de originele
    # string, casing intact.
    stripped = _HONORIFIC_PREFIX_RX.sub("", full)
    stripped = re.sub(r"\([^)]*\)", "", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    tokens = [t for t in stripped.split(" ") if t]
    if not tokens:
        return {"full": full, "family": full}

    # Post-honorifics (MPA/MSc/RA) van het eind af.
    post_rx = re.compile(r"^[A-Z]{1,4}$|^(?:MSc|MBA|BSc|PhD|LLM|LLB|EMBA)$", re.I)
    while len(tokens) > 1 and post_rx.fullmatch(tokens[-1]):
        tokens.pop()

    # Leading initialen-tokens ('K.I.', 'B.J.L', 'A.') afsplitsen — die
    # zijn geen given en geen family. Herken ook puntloze all-caps reeksen.
    def _is_initial_tok(t: str) -> bool:
        return _is_initials_token(t) or bool(re.fullmatch(r"[A-Z]{1,5}", t))

    while tokens and _is_initial_tok(tokens[0]):
        tokens.pop(0)

    record: dict[str, str] = {"full": full}
    if not tokens:
        record["family"] = parsed.family or full
    else:
        # Family begint bij het eerste tussenvoegsel-token, anders het
        # laatste token. Alles ervoor (en niet-initiaal) is given.
        fam_start = len(tokens) - 1
        for i, tok in enumerate(tokens):
            if _looks_like_tussenvoegsel(tok):
                fam_start = i
                break
        # Compound-namen ('van Oudenhoven-van der Zee') hebben meer
        # family-segmenten dan de eerste-tussenvoegsel-heuristiek pakt.
        # parsed.family_full telt het juiste aantal; pak dan dat aantal
        # tokens van achteren zodat de hele samengestelde naam meekomt.
        if parsed.family_full:
            seg_count = len(parsed.family_full.split("-"))
            if seg_count > len(tokens) - fam_start:
                fam_start = max(0, len(tokens) - seg_count)
        family = " ".join(tokens[fam_start:])
        given_words = [t for t in tokens[:fam_start] if not _is_initial_tok(t)]
        record["family"] = family
        if nickname:
            record["given"] = nickname
        elif given_words:
            record["given"] = " ".join(given_words)

    if parsed.initials:
        record["initials"] = "".join(f"{c.upper()}." for c in parsed.initials)
    return record
