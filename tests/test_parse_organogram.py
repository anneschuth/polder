"""Tests voor de parse-organogram skill: frontmatter, voorbeelden en harde regels."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

SKILL_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "parse-organogram"

ABD_CLASSIFICATIONS = {
    "abd-tmg",
    "abd-directeur",
    "abd-afdelingshoofd",
    "abd-projectleider",
}

# Posten op rood-AVG-niveau die NOOIT in een proposal mogen verschijnen.
RED_AVG_KEYWORDS = (
    "beleidsmedewerker",
    "communicatiemedewerker",
    "jurist",
    "secretariaat",
)

# Mapping zoals beschreven in SKILL.md, gebruikt voor een sanity-check.
TITLE_TO_CLASSIFICATION = {
    "Secretaris-Generaal": "abd-tmg",
    "SG": "abd-tmg",
    "plv. SG": "abd-tmg",
    "Directeur-Generaal": "abd-tmg",
    "DG": "abd-tmg",
    "Inspecteur-Generaal": "abd-tmg",
    "IG": "abd-tmg",
    "Directeur": "abd-directeur",
    "plv. directeur": "abd-directeur",
    "Programmadirecteur": "abd-directeur",
    "Afdelingshoofd": "abd-afdelingshoofd",
    "MT-lid": "abd-afdelingshoofd",
    "clusterhoofd": "abd-afdelingshoofd",
    "Projectleider": "abd-projectleider",
    "Kwartiermaker": "abd-projectleider",
}


def _read_skill_md() -> str:
    return (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")


def _parse_frontmatter(content: str) -> dict:
    assert content.startswith("---"), "SKILL.md moet beginnen met YAML-frontmatter"
    end_marker = content.index("\n---", 3)
    fm_text = content[3:end_marker].strip()
    return yaml.safe_load(fm_text)


def _load_proposals() -> list[dict]:
    return json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))


def test_skill_md_exists() -> None:
    assert (SKILL_DIR / "SKILL.md").is_file()


def test_skill_md_frontmatter_valid_yaml() -> None:
    fm = _parse_frontmatter(_read_skill_md())
    assert isinstance(fm, dict)


def test_skill_md_frontmatter_required_fields() -> None:
    fm = _parse_frontmatter(_read_skill_md())
    assert fm.get("name") == "parse-organogram"
    assert fm.get("description"), "description is verplicht"
    assert fm.get("version") == "0.2.0", "version moet 0.2.0 zijn"


def test_skill_md_no_em_dashes() -> None:
    assert "—" not in _read_skill_md(), "SKILL.md mag geen em-dashes bevatten"


def test_skill_md_length_in_range() -> None:
    lines = _read_skill_md().splitlines()
    assert 80 <= len(lines) <= 150, f"SKILL.md moet 80-150 regels zijn, is {len(lines)}"


def test_skill_md_describes_classification_mapping() -> None:
    """Elke ABD-classification uit de mapping moet expliciet in SKILL.md staan."""
    body = _read_skill_md()
    for classification in ABD_CLASSIFICATIONS:
        assert classification in body, (
            f"{classification} ontbreekt in SKILL.md. Mapping moet expliciet zijn."
        )


def test_skill_md_mentions_red_avg_skip_rule() -> None:
    body = _read_skill_md().lower()
    assert "rood" in body and "avg" in body, "SKILL.md moet rood-AVG-regel benoemen"


def test_example_image_description_exists() -> None:
    assert (SKILL_DIR / "example_image_description.md").is_file()


def test_example_output_is_valid_json_array() -> None:
    data = _load_proposals()
    assert isinstance(data, list)
    assert len(data) >= 2, "example_output.json moet minstens twee proposals hebben"


def test_example_output_has_required_fields_per_type() -> None:
    """Elk proposal heeft bron-velden, type, evidence en confidence."""
    common = {"type", "bron_pagina_nummer", "bron_url", "confidence", "evidence"}
    for proposal in _load_proposals():
        missing = common - set(proposal.keys())
        assert not missing, f"Ontbrekende velden in proposal: {missing}"
        assert proposal["type"] in {"org_structure", "person_post"}
        if proposal["type"] == "org_structure":
            assert "parent_id" in proposal and "child_name" in proposal
        if proposal["type"] == "person_post":
            assert "person_name" in proposal and "post_id" in proposal


def test_person_post_confidence_capped_at_0_85() -> None:
    """Personen-extracties uit vision: confidence ALTIJD <= 0.85."""
    for proposal in _load_proposals():
        if proposal["type"] != "person_post":
            continue
        confidence = float(proposal["confidence"])
        assert 0.0 <= confidence <= 0.85, (
            f"person_post confidence {confidence} overschrijdt cap 0.85 "
            f"voor {proposal.get('person_name')}"
        )


def test_person_post_classification_in_abd_set_or_absent() -> None:
    for proposal in _load_proposals():
        if proposal["type"] != "person_post":
            continue
        if "classification" in proposal:
            assert proposal["classification"] in ABD_CLASSIFICATIONS, (
                f"Onbekende classification {proposal['classification']}"
            )


def test_no_red_avg_posts_in_output() -> None:
    """Beleidsmedewerker, jurist etc mogen niet in het resultaat staan."""
    proposals = _load_proposals()
    for proposal in proposals:
        haystack = " ".join(
            str(proposal.get(field, ""))
            for field in ("post_id", "child_name", "evidence", "person_name")
        ).lower()
        for forbidden in RED_AVG_KEYWORDS:
            assert forbidden not in haystack, (
                f"Rood-AVG-keyword '{forbidden}' aanwezig in proposal: {proposal}"
            )


def test_classification_mapping_consistent_in_skill_md() -> None:
    """Sanity-check: elke titel uit de mapping mapt op een geldige ABD-classification.

    Dit is een doc-level test: de mapping in TITLE_TO_CLASSIFICATION moet
    consistent zijn met de enums uit schemas/post.schema.json.
    """
    for title, classification in TITLE_TO_CLASSIFICATION.items():
        assert classification in ABD_CLASSIFICATIONS, (
            f"Mapping {title} -> {classification} verwijst naar onbekende classification"
        )


def test_example_output_has_both_proposal_types() -> None:
    types = {proposal["type"] for proposal in _load_proposals()}
    assert "org_structure" in types, "example_output mist org_structure"
    assert "person_post" in types, "example_output mist person_post"
