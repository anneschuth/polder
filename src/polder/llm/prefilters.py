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


_STCRT_FILENAME_RE = re.compile(r"(stcrt-\d{4}-\d+)", re.IGNORECASE)


def extract_staatscourant_payload(xml: str, source_filename: str | None = None) -> str:
    """Staatscourant-XML naar een compacte plain-text payload voor de LLM-call.

    KOOP-XML bevat metadata-headers, namespace-declaraties, schema-locaties en
    XML-tabel-attributen (frame, colspec, morerows, ...) die de LLM niet nodig
    heeft. We strippen alle tags op `<staatscourant>` (of root als die mist)
    en lijnen de tekst van `<al>`, `<row>`, `<entry>` en `<li>`-elementen
    netjes uit zodat de structuur leesbaar blijft.

    Levert:

    ```
    KB_REFERENCE:
    stcrt-2024-7691  (afgeleid uit filename als meegegeven)

    STAATSCOURANT_URL:
    https://zoek.officielebekendmakingen.nl/stcrt-2024-7691.html

    INTITULE:
    Besluit van de Minister voor Rechtsbescherming ... benoeming ...

    BODY:
    De Minister voor Rechtsbescherming,
    Gelet op artikel 12 ...
    Besluit:
    1. De heer R. Kok op eigen verzoek per 16 maart 2024 te ontslaan ...
    2. Mevrouw V. Jeurissen-Kohn te benoemen ...
    Ondertekening: F.M. Weerwind, De Minister voor Rechtsbescherming
    ```

    Geen Unicode-normalisatie of whitespace-collapse: de evidence-substring-
    assert moet hierop nog steeds slagen.
    """
    from lxml import etree

    kb_ref = ""
    if source_filename:
        m = _STCRT_FILENAME_RE.search(source_filename)
        if m:
            kb_ref = m.group(1).lower()
    stcrt_url = f"https://zoek.officielebekendmakingen.nl/{kb_ref}.html" if kb_ref else ""

    intitule = ""
    body_lines: list[str] = []
    try:
        # KOOP-XML heeft soms entities + xsi-namespace; lxml parst dat zonder DTD.
        root = etree.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    except etree.XMLSyntaxError:
        # Best-effort: lever wat we hebben plus een ruwe-tekst-fallback.
        body_lines = [xml.strip()[:5000]]
    else:
        intitule_el = root.find(".//intitule")
        if intitule_el is not None and intitule_el.text:
            intitule = intitule_el.text.strip()

        # Itereer over de besluit-tekst-elementen op volgorde, één regel per
        # blok-element zodat tabellen leesbaar blijven als rij-per-rij.
        stcrt_el = root.find(".//staatscourant")
        body_root = stcrt_el if stcrt_el is not None else root
        for el in body_root.iter():
            tag = etree.QName(el).localname
            if tag == "intitule":
                continue  # al apart
            if tag in ("al", "wie", "li.nr", "functie"):
                text = "".join(el.itertext()).strip()
                if text:
                    body_lines.append(text)
            elif tag in ("voornaam", "achternaam"):
                text = (el.text or "").strip()
                if text:
                    # Voeg samen tot één naam-regel als ze achter elkaar komen.
                    if body_lines and body_lines[-1].startswith("NAAM:"):
                        body_lines[-1] += " " + text
                    else:
                        body_lines.append(f"NAAM: {text}")

    parts = []
    if kb_ref:
        parts += ["KB_REFERENCE:", kb_ref, ""]
    if stcrt_url:
        parts += ["STAATSCOURANT_URL:", stcrt_url, ""]
    if intitule:
        parts += ["INTITULE:", intitule, ""]
    parts += ["BODY:", "\n".join(body_lines)]
    return "\n".join(parts)


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
