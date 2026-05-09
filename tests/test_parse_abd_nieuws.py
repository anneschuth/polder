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
    assert fm.get("version") == "0.2.0"
    assert isinstance(fm.get("triggers"), list)
    assert len(fm["triggers"]) >= 3


def test_skill_md_no_em_dashes() -> None:
    content = _read_skill_md()
    assert "—" not in content, "SKILL.md mag geen em-dashes bevatten"


def test_skill_md_length_in_range() -> None:
    lines = _read_skill_md().splitlines()
    assert 70 <= len(lines) <= 200, f"SKILL.md moet 70-200 regels zijn, is {len(lines)}"


def test_skill_md_describes_two_source_rule() -> None:
    content = _read_skill_md().lower()
    assert "two-source" in content or "tweede bron" in content
    # Confidence-cap is een hard signaal in de skill.
    assert "0.85" in content


def test_example_input_md_exists_and_non_empty() -> None:
    text = (SKILL_DIR / "example_input.md").read_text(encoding="utf-8")
    assert len(text) > 200
    assert "Esther Pijs" in text
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
    proposal = data[0]
    confidence = float(proposal["confidence"])
    assert 0.0 <= confidence <= 1.0
    # Standalone ABD-nieuws zonder KB-link: cap 0.85.
    if not proposal.get("staatscourant_url"):
        assert confidence <= 0.85, (
            "Two-source rule: zonder staatscourant_url moet confidence <= 0.85 zijn."
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
    snippet = data[0]["evidence_snippet"]
    assert snippet, "evidence_snippet mag niet leeg zijn"
    assert snippet in raw, (
        "evidence_snippet is geen letterlijke substring van example_input.md. "
        "Quote-or-die regel geschonden."
    )
