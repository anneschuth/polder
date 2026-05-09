"""Tests voor de parse-abd-nieuws skill: frontmatter, voorbeeldbestanden en substring-check."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

SKILL_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "parse-abd-nieuws"

REQUIRED_FIELDS = {
    "person_name",
    "existing_person_id",
    "organization_id",
    "organization_chain",
    "post_id",
    "role",
    "start_date",
    "end_date",
    "decision_reference",
    "staatscourant_url",
    "abd_nieuws_url",
    "event_type",
    "confidence",
    "confidence_reasoning",
    "evidence_snippet",
}

ALLOWED_EVENT_TYPES = {"benoeming", "ontslag", "verlenging", "aankondiging", "overig"}
ALLOWED_LEVELS = {"ministerie", "directoraat-generaal", "directie", "afdeling"}


def _read_skill_md() -> str:
    return (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")


def _parse_frontmatter(content: str) -> dict:
    assert content.startswith("---"), "SKILL.md moet beginnen met YAML-frontmatter"
    end_marker = content.index("\n---", 3)
    fm_text = content[3:end_marker].strip()
    return yaml.safe_load(fm_text)


def test_skill_md_exists() -> None:
    assert (SKILL_DIR / "SKILL.md").is_file()


def test_skill_md_frontmatter_required_fields() -> None:
    fm = _parse_frontmatter(_read_skill_md())
    assert fm.get("name") == "parse-abd-nieuws"
    assert fm.get("description")
    assert fm.get("version") == "0.4.0"
    assert isinstance(fm.get("triggers"), list)
    assert len(fm["triggers"]) >= 3


def test_skill_md_no_em_dashes() -> None:
    content = _read_skill_md()
    assert "—" not in content, "SKILL.md mag geen em-dashes bevatten"


def test_skill_md_length_in_range() -> None:
    lines = _read_skill_md().splitlines()
    assert 70 <= len(lines) <= 250, f"SKILL.md moet 70-250 regels zijn, is {len(lines)}"


def test_skill_md_describes_organization_chain() -> None:
    content = _read_skill_md().lower()
    assert "organization_chain" in content
    # Niveaus expliciet genoemd in de skill.
    for level in ("ministerie", "directoraat-generaal", "directie", "afdeling"):
        assert level in content, f"Niveau {level} moet in SKILL.md staan"


def test_skill_md_describes_two_source_rule() -> None:
    content = _read_skill_md().lower()
    assert "two-source" in content or "tweede bron" in content
    # Floor en cap zijn beide harde signalen in v0.4.0.
    assert "0.85" in content
    assert "0.94" in content


def test_skill_md_describes_voorlopig_factor() -> None:
    content = _read_skill_md().lower()
    # "voorlopig" is een expliciet genoemde verzwarende factor in v0.4.0.
    assert "voorlopig" in content
    assert "verzwarend" in content or "factor" in content


def test_skill_md_describes_floor_and_cap() -> None:
    content = _read_skill_md()
    # Sectie expliciet aanwezig.
    assert "Confidence-bepaling" in content
    # Floor 0.85 en cap 0.94 staan expliciet beschreven.
    assert "0.85" in content
    assert "0.94" in content
    # Vier kernfeiten genoemd.
    lower = content.lower()
    assert "familienaam" in lower
    assert "functie" in lower
    assert "organisatie" in lower
    assert "datum" in lower


def test_example_input_md_exists_and_non_empty() -> None:
    text = (SKILL_DIR / "example_input.md").read_text(encoding="utf-8")
    assert len(text) > 200
    assert "Marleen Heijster" in text
    assert "/actueel/nieuws/" in text


def test_example_output_is_valid_json_array() -> None:
    data = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) >= 1


def test_example_output_has_required_fields() -> None:
    data = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    proposal = data[0]
    missing = REQUIRED_FIELDS - set(proposal.keys())
    assert not missing, f"Ontbrekende velden in proposal: {missing}"


def test_example_output_event_type_in_allowed_set() -> None:
    data = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    for proposal in data:
        assert proposal["event_type"] in ALLOWED_EVENT_TYPES


def test_example_output_confidence_in_range_and_capped() -> None:
    data = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    for proposal in data:
        confidence = float(proposal["confidence"])
        assert 0.0 <= confidence <= 1.0
        # v0.4.0 two-source rule: zonder staatscourant_url cap 0.94.
        if not proposal.get("staatscourant_url"):
            assert confidence <= 0.94, (
                "Two-source rule v0.4.0: zonder staatscourant_url moet confidence <= 0.94 zijn."
            )


def test_example_output_floor_when_four_facts_explicit() -> None:
    """Bij vier expliciete kernfeiten moet de confidence ten minste 0.85 zijn."""
    data = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    for proposal in data:
        if proposal.get("event_type") not in {"benoeming", "ontslag", "verlenging"}:
            continue
        has_name = bool(proposal.get("person_name"))
        has_role = bool(proposal.get("role"))
        chain = proposal.get("organization_chain") or []
        has_org = bool(chain)
        has_date = bool(proposal.get("start_date") or proposal.get("end_date"))
        if has_name and has_role and has_org and has_date:
            confidence = float(proposal["confidence"])
            assert confidence >= 0.85, (
                f"Floor 0.85 geschonden voor {proposal.get('person_name')!r}: "
                f"vier kernfeiten expliciet maar confidence={confidence}."
            )


def test_example_output_abd_nieuws_url_is_canonical() -> None:
    data = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    url = data[0]["abd_nieuws_url"]
    assert url.startswith("https://www.algemenebestuursdienst.nl/actueel/nieuws/")
    # `/YYYY/MM/DD/<slug>` pad.
    assert url.count("/") >= 8


def test_evidence_snippet_is_substring_of_input_md() -> None:
    raw = (SKILL_DIR / "example_input.md").read_text(encoding="utf-8")
    data = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    for proposal in data:
        snippet = proposal["evidence_snippet"]
        assert snippet, "evidence_snippet mag niet leeg zijn"
        assert snippet in raw, (
            f"evidence_snippet voor {proposal.get('person_name')!r} is geen letterlijke "
            "substring van example_input.md. Quote-or-die regel geschonden."
        )


def test_example_output_has_organization_chain() -> None:
    data = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    proposal = data[0]
    chain = proposal.get("organization_chain")
    assert isinstance(chain, list), "organization_chain moet een list zijn"
    assert len(chain) >= 2, "voorbeeld bevat minstens twee niveaus"
    for entry in chain:
        assert set(entry.keys()) >= {"level", "name", "slug_proposal"}
        assert entry["level"] in ALLOWED_LEVELS


def test_example_output_chain_ends_at_afdeling() -> None:
    data = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    chain = data[0]["organization_chain"]
    # Voorbeeld is een afdelingsbenoeming: laatste entry is afdeling.
    assert chain[-1]["level"] == "afdeling"
    # En `organization_id` matcht de diepste slug.
    assert data[0]["organization_id"] == chain[-1]["slug_proposal"]


def test_example_output_chain_starts_at_ministerie() -> None:
    data = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    chain = data[0]["organization_chain"]
    assert chain[0]["level"] == "ministerie"
    assert chain[0]["slug_proposal"].startswith("org:min-")
