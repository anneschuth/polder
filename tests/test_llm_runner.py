"""Runner-laag: JSON-payload-detectie en de error-conversie voor JSON-skills
die alleen prose teruggeven.

Achtergrond: een lokale daily-run liet 9391/9393 staatscourant-resolves als
greeting-only output ("I'm ready to help...") wegschrijven, stil als "ok"
geteld en als corrupte stub gecached. `run_skill` markeert zo'n resultaat nu
als error zodat het niet gecached of weggeschreven wordt.
"""

from __future__ import annotations

from polder.llm.runner import _extract_json_payload, _has_json_payload


def test_has_json_payload_array() -> None:
    # parse-/resolve-skills leveren een array.
    assert _has_json_payload('[{"a": 1}]')
    assert _has_json_payload("Hier is het resultaat:\n```json\n[]\n```\n")
    assert _has_json_payload("Tekst vooraf [\n  1, 2\n] tekst erna")


def test_has_json_payload_empty_array_is_valid() -> None:
    # Leeg resultaat (geen proposals) is geldig, geen error.
    assert _has_json_payload("[]")
    assert _has_json_payload("  []  \n")
    assert _has_json_payload("```json\n[]\n```")


def test_has_json_payload_object_is_valid() -> None:
    # lookup-person en entity-resolution leveren per contract één JSON-
    # object. Dat als error markeren sloopt de LLM-enrichment in
    # `polder resolve` — een object moet dus geldig zijn.
    assert _has_json_payload('{"outcome": "matched", "chosen_person_id": "person:x"}')
    assert _has_json_payload('Hier:\n```json\n{"matched_id": "person:y"}\n```')


def test_has_json_payload_greeting_only() -> None:
    # Het exacte patroon uit de gedegradeerde resolve-sessies.
    assert not _has_json_payload(
        "I'm ready to help. I see you have the resolve-staging-proposals skill."
    )
    assert not _has_json_payload("I'm Claude Code, ready to help with the Polder project.")
    assert not _has_json_payload("")
    assert not _has_json_payload("Closed.")


def test_has_json_payload_greeting_with_brackets_rejected() -> None:
    # B2-regressie: een begroeting die toevallig een pad/toolnaam tussen
    # blokhaken noemt haalde de oude tekenheuristiek door als "JSON".
    # "[data/]" is geen geldige JSON -> moet als geen-payload tellen.
    assert not _has_json_payload("I'm ready to help with the skill [data/]. Let me start.")
    assert not _has_json_payload("Sure, I'll look at {the data} now.")


def test_extract_json_payload_falls_back_to_raw_on_prose() -> None:
    # Geen JSON -> originele tekst terug (zodat de fout downstream opvalt).
    text = "I'm ready to help."
    assert _extract_json_payload(text) == text
