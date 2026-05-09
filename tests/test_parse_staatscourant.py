"""Tests voor de parse-staatscourant skill: frontmatter, voorbeeldbestanden en substring-check."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from lxml import etree

SKILL_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "parse-staatscourant"

REQUIRED_FIELDS = {
    "person_name",
    "existing_person_id",
    "organization_id",
    "post_id",
    "role",
    "start_date",
    "end_date",
    "decision_reference",
    "staatscourant_url",
    "confidence",
    "confidence_reasoning",
    "evidence_snippet",
}


def _read_skill_md() -> str:
    return (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")


def _parse_frontmatter(content: str) -> dict:
    """Splits ``---``-frontmatter aan het begin van het bestand en parsed als YAML."""
    assert content.startswith("---"), "SKILL.md moet beginnen met YAML-frontmatter"
    end_marker = content.index("\n---", 3)
    fm_text = content[3:end_marker].strip()
    return yaml.safe_load(fm_text)


def test_skill_md_exists() -> None:
    assert (SKILL_DIR / "SKILL.md").is_file()


def test_skill_md_frontmatter_valid_yaml() -> None:
    fm = _parse_frontmatter(_read_skill_md())
    assert isinstance(fm, dict)


def test_skill_md_frontmatter_required_fields() -> None:
    fm = _parse_frontmatter(_read_skill_md())
    assert fm.get("name") == "parse-staatscourant"
    assert fm.get("description"), "description is verplicht"
    assert fm.get("version"), "version is verplicht"


def test_skill_md_version_is_0_2_0() -> None:
    fm = _parse_frontmatter(_read_skill_md())
    assert fm["version"] == "0.2.0", "SKILL.md moet versie 0.2.0 zijn"


def test_skill_md_no_em_dashes() -> None:
    content = _read_skill_md()
    assert "—" not in content, "SKILL.md mag geen em-dashes bevatten"


def test_skill_md_length_in_range() -> None:
    lines = _read_skill_md().splitlines()
    assert 80 <= len(lines) <= 150, f"SKILL.md moet 80-150 regels zijn, is {len(lines)}"


def test_example_input_xml_parseable() -> None:
    raw = (SKILL_DIR / "example_input.xml").read_bytes()
    tree = etree.fromstring(raw)
    assert tree is not None
    assert tree.tag.endswith("staatscourant")


def test_example_output_is_valid_json_array() -> None:
    data = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) >= 1, "example_output.json moet minstens één proposal hebben"


def test_example_output_has_required_fields() -> None:
    data = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    proposal = data[0]
    missing = REQUIRED_FIELDS - set(proposal.keys())
    assert not missing, f"Ontbrekende velden in proposal: {missing}"


def test_example_output_confidence_in_range() -> None:
    data = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    confidence = data[0]["confidence"]
    assert isinstance(confidence, (int, float))
    assert 0.0 <= float(confidence) <= 1.0


def test_evidence_snippet_is_substring_of_input_xml() -> None:
    """De kern-regel: evidence_snippet MOET letterlijk in de bron-XML voorkomen."""
    raw_xml = (SKILL_DIR / "example_input.xml").read_text(encoding="utf-8")
    data = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    snippet = data[0]["evidence_snippet"]
    assert snippet, "evidence_snippet mag niet leeg zijn"
    assert snippet in raw_xml, (
        "evidence_snippet is geen letterlijke substring van example_input.xml. "
        "Quote-or-die regel geschonden."
    )
