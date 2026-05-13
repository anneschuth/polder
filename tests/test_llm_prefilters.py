"""Tests voor `polder.llm.prefilters`."""

from __future__ import annotations

from polder.llm.prefilters import (
    abd_nieuws_has_signal,
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
