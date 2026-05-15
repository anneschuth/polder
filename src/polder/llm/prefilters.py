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


_TWITTER_DESC_RE = re.compile(
    r'<meta\s+name="twitter:description"\s+content="([^"]*)"',
    re.IGNORECASE,
)

_CANONICAL_RE = re.compile(
    r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"',
    re.IGNORECASE,
)

# Staatscourant-URL die soms in de body staat als `<a href="...">`. We pakken
# alle KOOP-officielebekendmakingen-links zodat de skill `staatscourant_url`
# kan invullen — `html_to_text` gooit `<a href>`-attributen weg.
_STAATSCOURANT_URL_RE = re.compile(
    r'href="(https?://[^"]*officielebekendmakingen[^"]*stcrt[^"]*)"',
    re.IGNORECASE,
)

# Footer-marker waar de ABD-pagina overgaat van artikel-content naar
# site-navigatie ("Service Downloads Abonneren Vacatures ..."). Knip de body
# daar af om ~300 bytes aan boilerplate per bericht te besparen.
_ABD_FOOTER_MARKER = "Service Downloads Abonneren"


def extract_abd_payload(html: str) -> str:
    """ABD-HTML naar een compacte plain-text payload voor de LLM-call.

    Combineert canonical-URL, `<meta name="twitter:description">` (de
    gegarandeerde kern-zin van de benoeming), en de body uit `html_to_text`.
    Footer-boilerplate wordt afgekapt op de eerste site-navigatie-marker.
    Evt. Staatscourant-link uit `<a href>` wordt apart gezet (html_to_text
    gooit attributen weg). Geen Unicode-normalisatie of whitespace-collapse:
    de evidence-substring-assert moet hierop nog steeds slagen.
    """
    canonical_match = _CANONICAL_RE.search(html)
    canonical = canonical_match.group(1).strip() if canonical_match else ""

    desc_match = _TWITTER_DESC_RE.search(html)
    desc = desc_match.group(1).strip() if desc_match else ""

    stcrt_urls = sorted(set(_STAATSCOURANT_URL_RE.findall(html)))

    body = html_to_text(html)
    footer_idx = body.find(_ABD_FOOTER_MARKER)
    if footer_idx > 0:
        body = body[:footer_idx].rstrip()

    parts = [
        "CANONICAL_URL:",
        canonical,
        "",
        "TWITTER_DESCRIPTION:",
        desc,
        "",
    ]
    if stcrt_urls:
        parts += ["STAATSCOURANT_URLS:", *stcrt_urls, ""]
    parts += ["BODY:", body]
    return "\n".join(parts)


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
