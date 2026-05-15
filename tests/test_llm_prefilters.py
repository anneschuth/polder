"""Tests voor `polder.llm.prefilters`."""

from __future__ import annotations

from polder.llm.prefilters import (
    abd_nieuws_has_signal,
    extract_abd_payload,
    html_to_text,
    staatscourant_has_signal,
)

# ---------------------------------------------------------------------------
# html_to_text
# ---------------------------------------------------------------------------


def test_html_to_text_strips_tags() -> None:
    html = "<html><body><p>Hallo <b>wereld</b></p></body></html>"
    text = html_to_text(html)
    assert "Hallo" in text
    assert "wereld" in text
    assert "<p>" not in text
    assert "<b>" not in text


def test_html_to_text_skips_script_content() -> None:
    html = "<p>hier wat tekst</p><script>var x = 'wordt benoemd';</script>"
    text = html_to_text(html)
    assert "hier wat tekst" in text
    assert "wordt benoemd" not in text


def test_html_to_text_skips_style_content() -> None:
    html = "<p>real</p><style>.x { content: 'directeur'; }</style>"
    text = html_to_text(html)
    assert "real" in text
    assert "directeur" not in text


# ---------------------------------------------------------------------------
# abd_nieuws_has_signal
# ---------------------------------------------------------------------------


def test_abd_nieuws_signal_wordt_benoemd() -> None:
    html = "<html><body><p>Jan Jansen wordt benoemd tot directeur</p></body></html>"
    assert abd_nieuws_has_signal(html) is True


def test_abd_nieuws_no_signal_when_no_marker() -> None:
    html = "<html><body><p>Algemeen nieuws over een congres</p></body></html>"
    assert abd_nieuws_has_signal(html) is False


def test_abd_nieuws_ignores_markers_in_script_tags() -> None:
    # De marker zit alleen in een <script> blok, dat de parser overslaat
    html = (
        "<html><body><p>iets onschuldigs</p>"
        "<script>console.log('wordt benoemd');</script>"
        "</body></html>"
    )
    assert abd_nieuws_has_signal(html) is False


def test_abd_nieuws_case_insensitive() -> None:
    html = "<p>Marie WORDT BENOEMD tot SG</p>"
    assert abd_nieuws_has_signal(html) is True


def test_abd_nieuws_matches_role_keyword() -> None:
    html = "<p>De nieuwe secretaris-generaal van het ministerie</p>"
    assert abd_nieuws_has_signal(html) is True


# ---------------------------------------------------------------------------
# staatscourant_has_signal
# ---------------------------------------------------------------------------


def test_staatscourant_signal_benoeming_lid_adviescommissie() -> None:
    xml = (
        "<root><intitule>Besluit van de Minister houdende benoeming "
        "van een lid van de Adviescommissie</intitule></root>"
    )
    assert staatscourant_has_signal(xml) is True


def test_staatscourant_signal_benoeming_directeur_generaal() -> None:
    # Let op: het role-pattern in de module matcht alleen "directeur-generaal",
    # niet plain "directeur". Een titel met alleen "directeur" zou dus niet
    # triggeren — zie test_staatscourant_no_signal_plain_directeur.
    xml = "<root><intitule>benoeming van een directeur-generaal</intitule></root>"
    assert staatscourant_has_signal(xml) is True


def test_staatscourant_no_signal_plain_directeur() -> None:
    # "directeur" alleen (zonder -generaal, zonder andere rol-keyword) matcht
    # het role-pattern niet. Documenteert het huidige module-gedrag.
    xml = "<root><intitule>benoeming van een directeur</intitule></root>"
    assert staatscourant_has_signal(xml) is False


def test_staatscourant_no_signal_for_subsidie_wijziging() -> None:
    xml = "<root><intitule>Besluit houdende wijziging van de subsidie X</intitule></root>"
    assert staatscourant_has_signal(xml) is False


def test_staatscourant_no_signal_without_intitule() -> None:
    xml = "<root><body>geen intitule element hier</body></root>"
    assert staatscourant_has_signal(xml) is False


def test_staatscourant_no_signal_without_role_keyword() -> None:
    # "benoeming" wel, rol-trefwoord niet
    xml = "<root><intitule>benoeming hoofdkussen</intitule></root>"
    assert staatscourant_has_signal(xml) is False


def test_staatscourant_garbage_xml_returns_false() -> None:
    assert staatscourant_has_signal("not xml at all <<<>>>") is False
    assert staatscourant_has_signal("") is False


def test_staatscourant_ontslag_with_role() -> None:
    xml = "<root><intitule>Besluit houdende ontslag van een lid van de Raad</intitule></root>"
    assert staatscourant_has_signal(xml) is True


def test_staatscourant_herbenoeming_voorzitter() -> None:
    xml = "<root><intitule>Herbenoeming van de voorzitter</intitule></root>"
    assert staatscourant_has_signal(xml) is True


# ---------------------------------------------------------------------------
# extract_abd_payload
# ---------------------------------------------------------------------------


def test_extract_abd_payload_includes_canonical_and_twitter_desc() -> None:
    html = (
        "<html><head>"
        '<link rel="canonical" href="https://example.nl/nieuws/2024/01/15/jan-jansen"/>'
        '<meta name="twitter:description" content="Jan Jansen wordt directeur."/>'
        "</head><body><article>Jan Jansen wordt per 1 februari 2024 directeur "
        "bij het ministerie.</article></body></html>"
    )
    payload = extract_abd_payload(html)
    assert "CANONICAL_URL:" in payload
    assert "https://example.nl/nieuws/2024/01/15/jan-jansen" in payload
    assert "TWITTER_DESCRIPTION:" in payload
    assert "Jan Jansen wordt directeur." in payload
    assert "BODY:" in payload
    assert "1 februari 2024" in payload


def test_extract_abd_payload_strips_scripts_and_styles() -> None:
    html = (
        '<html><head><meta name="twitter:description" content="kern"/></head>'
        '<body><script>tracker("benoemd")</script>'
        "<style>.x{color:red}</style>"
        "<article>artikel-tekst</article></body></html>"
    )
    payload = extract_abd_payload(html)
    assert "tracker(" not in payload
    assert "color:red" not in payload
    assert "artikel-tekst" in payload


def test_extract_abd_payload_truncates_at_footer_marker() -> None:
    html = (
        '<html><head><meta name="twitter:description" content="kern"/></head>'
        "<body><article>belangrijke artikel-tekst</article>"
        "<footer>Service Downloads Abonneren Vacatures Contact</footer>"
        "</body></html>"
    )
    payload = extract_abd_payload(html)
    assert "belangrijke artikel-tekst" in payload
    assert "Vacatures" not in payload
    assert "Contact" not in payload


def test_extract_abd_payload_includes_staatscourant_url_when_present() -> None:
    html = (
        '<html><head><meta name="twitter:description" content="kern"/></head>'
        '<body><a href="https://zoek.officielebekendmakingen.nl/stcrt-2024-12345.html">KB</a>'
        "</body></html>"
    )
    payload = extract_abd_payload(html)
    assert "STAATSCOURANT_URLS:" in payload
    assert "stcrt-2024-12345" in payload


def test_extract_abd_payload_omits_staatscourant_section_when_none() -> None:
    html = (
        '<html><head><meta name="twitter:description" content="kern"/></head>'
        "<body><article>geen staatscourant link hier</article></body></html>"
    )
    payload = extract_abd_payload(html)
    assert "STAATSCOURANT_URLS:" not in payload


def test_extract_abd_payload_handles_missing_meta_gracefully() -> None:
    html = "<html><body><article>alleen body, geen meta tags</article></body></html>"
    payload = extract_abd_payload(html)
    assert "CANONICAL_URL:" in payload
    assert "TWITTER_DESCRIPTION:" in payload
    assert "alleen body" in payload


def test_extract_abd_payload_evidence_substring_invariant() -> None:
    html = (
        '<html><head><meta name="twitter:description" '
        'content="Marie de Vries wordt directeur Wonen bij VRO."/></head>'
        "<body><article>Marie de Vries wordt per 1 maart 2024 directeur Wonen "
        "bij het ministerie van VRO. De benoeming gaat in op 1 maart 2024."
        "</article></body></html>"
    )
    payload = extract_abd_payload(html)
    # Evidence-snippets die de skill zou kunnen kiezen, moeten als letterlijke
    # substring in de payload staan zodat de quote-or-die assert slaagt.
    assert "Marie de Vries wordt per 1 maart 2024 directeur Wonen" in payload
    assert "ministerie van VRO" in payload
    assert "De benoeming gaat in op 1 maart 2024" in payload
