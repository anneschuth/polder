"""Pre-filter heuristieken voor LLM-calls.

Doel: input dat zeker geen interessante personeels-mutatie bevat overslaan
zonder een LLM-call. Voor staatscourant scheelt dat ~70%, voor abd-nieuws
~30-50%.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

_ABD_NIEUWS_PATTERNS = re.compile(
    r"wordt benoemd"
    r"|is benoemd"
    r"|wordt per "
    r"|start als "
    r"|neemt afscheid"
    r"|afdelingshoofd"
    r"|directeur"
    r"|secretaris-generaal"
    r"|directeur-generaal"
    r"|inspecteur-generaal"
    r"|minister"
    r"|staatssecretaris"
    r"|kwartiermaker",
    re.IGNORECASE,
)

_STAATSCOURANT_TITLE_PATTERNS = re.compile(
    r"benoeming"
    r"|herbenoeming"
    r"|ontslag"
    r"|aanwijzing"
    r"|aanstelling"
    r"|eervol ontheven"
    r"|opvolging",
    re.IGNORECASE,
)

_STAATSCOURANT_ROLE_PATTERNS = re.compile(
    r"minister"
    r"|staatssecretaris"
    r"|secretaris-generaal"
    r"|directeur-generaal"
    r"|inspecteur-generaal"
    r"|lid van"
    r"|leden van"
    r"|voorzitter"
    r"|plaatsvervangend",
    re.IGNORECASE,
)


class _TextExtractor(HTMLParser):
    """Strip HTML naar plain text. Skip script/style content."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Best-effort HTML → plain text. Geen externe deps."""
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
    except Exception:
        return html
    return " ".join(extractor.parts)


def abd_nieuws_has_signal(html: str) -> bool:
    """True als een ABD-nieuwsbericht waarschijnlijk een personeels-mutatie bevat."""
    text = html_to_text(html)
    return bool(_ABD_NIEUWS_PATTERNS.search(text))


def staatscourant_has_signal(xml: str) -> bool:
    """True als een Staatscourant XML waarschijnlijk een personeels-besluit is.

    Filtert op de `<intitule>` (titel-zin van het besluit). Een besluit met
    "benoeming" plus een rol-trefwoord (minister, lid van, voorzitter, etc.)
    in de titel is een sterke kandidaat.
    """
    match = re.search(r"<intitule>([^<]+)</intitule>", xml, re.DOTALL)
    if not match:
        return False
    intitule = match.group(1)
    if not _STAATSCOURANT_TITLE_PATTERNS.search(intitule):
        return False
    return bool(_STAATSCOURANT_ROLE_PATTERNS.search(intitule))
