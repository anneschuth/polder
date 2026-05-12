"""Wikidata-enricher voor `resolve_proposal`.

Strenge wrapper rond `lookup_person_by_name`. We accepteren alleen een
birth_year als (a) er precies één kandidaat in de plausibele leeftijdsrange
zit, en (b) de naam-overeenkomst sterk genoeg is om verkeerde personen uit
te sluiten. Dit voorkomt dat een homoniem (zelfde naam, andere persoon) een
nepmatch oplevert.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date
from functools import lru_cache
from pathlib import Path

from polder.fetchers.wikidata_sparql import lookup_person_by_name
from polder.resolve.names import parse_person_name

logger = logging.getLogger("polder.resolve.wikidata_enricher")

# Een ambtenaar/bewindspersoon is plausibel tussen de 18 en 80 jaar oud op
# het moment dat we 'm in de polder zien. Buiten die range negeren we de
# kandidaat — meestal wijst dat op een andere persoon met dezelfde naam
# (historisch figuur, naamgenoot, kind).
_MIN_AGE = 18
_MAX_AGE = 80


def _ascii_lower(value: str | None) -> str:
    if not value:
        return ""
    s = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return s.strip().lower()


def _label_matches(parsed_family: str, parsed_given: str | None, label: str | None) -> bool:
    """Eerlijke naam-match: lower-ASCII, family + (initiaal of given) komen voor."""
    label_norm = _ascii_lower(label)
    if not label_norm or not parsed_family:
        return False
    if parsed_family not in label_norm:
        return False
    if parsed_given:
        first_token = parsed_given.split()[0]
        if first_token and first_token not in label_norm:
            # Sta initiaal-vorm toe: "E. Geurtsen" matched "Evelyn"-given via 'e'.
            initial = first_token[0]
            if not re.search(rf"\b{re.escape(initial)}\.?", label_norm):
                return False
    return True


def _is_plausible_age(year: int, *, today: date | None = None) -> bool:
    today = today or date.today()
    age = today.year - year
    return _MIN_AGE <= age <= _MAX_AGE


def make_wikidata_enricher(*, cache_dir: Path | None = None):
    """Bouw een enricher-callable voor `resolve_proposal`.

    Retourneert een `(name, existing_birth_hint) -> int | None` callable die
    pas een birth_year teruggeeft als er precies één plausibele kandidaat
    overblijft (naam-match + leeftijd 18-100).
    """
    if cache_dir is None:
        cache_dir = Path("_cache") / "wikidata-reconciliation"
    cache_dir.mkdir(parents=True, exist_ok=True)

    @lru_cache(maxsize=4096)
    def _lookup(name_norm: str) -> int | None:
        parsed = parse_person_name(name_norm)
        if not parsed.family:
            return None
        try:
            candidates = lookup_person_by_name(
                parsed.family,
                initials=parsed.initials,
                given=parsed.given,
                cache_dir=cache_dir,
            )
        except Exception as exc:
            logger.debug("Wikidata-lookup faalde voor %r: %s", name_norm, exc)
            return None

        plausible: list[int] = []
        for c in candidates:
            year = c.get("birth_year")
            if not isinstance(year, int):
                continue
            if not _is_plausible_age(year):
                continue
            if not _label_matches(parsed.family, parsed.given, c.get("label")):
                continue
            plausible.append(year)
        # Strict: alleen accepteren bij precies één kandidaat. Twee plausibele
        # personen met dezelfde naam zijn niet auto-disambigueerbaar.
        if len(plausible) == 1:
            return plausible[0]
        return None

    def enricher(name: str, existing_birth_hint: int | None) -> int | None:
        if existing_birth_hint is not None:
            return existing_birth_hint
        if not name:
            return None
        return _lookup(name.strip().lower())

    return enricher
