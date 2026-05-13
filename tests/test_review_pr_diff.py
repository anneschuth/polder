"""Tests voor de review-pr-diff skill: parsebaarheid van skill-instructies."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

SKILL_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "review-pr-diff"


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
    assert fm.get("name") == "review-pr-diff"
    assert fm.get("description"), "description is verplicht"
    assert fm.get("version"), "version is verplicht"


def test_skill_md_version_bumped() -> None:
    fm = _parse_frontmatter(_read_skill_md())
    assert fm["version"] != "0.1.0", "version moet zijn opgehoogd t.o.v. de stub"


def test_skill_md_no_em_dashes() -> None:
    content = _read_skill_md()
    assert "—" not in content, "SKILL.md mag geen em-dashes bevatten"


def test_example_diff_is_valid_json() -> None:
    data = json.loads((SKILL_DIR / "example_diff.json").read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) >= 3, "example_diff.json moet meerdere realistische entries hebben"


def test_example_diff_has_expected_shape() -> None:
    data = json.loads((SKILL_DIR / "example_diff.json").read_text(encoding="utf-8"))
    types = {entry.get("type") for entry in data}
    assert "modified" in types
    assert "new" in types
    assert any(
        entry.get("high_stakes") for entry in data
    ), "minstens een high-stakes entry verwacht"


def test_example_diff_paths_under_organisaties() -> None:
    data = json.loads((SKILL_DIR / "example_diff.json").read_text(encoding="utf-8"))
    for entry in data:
        assert entry["path"].startswith("data/organisaties/"), f"onverwacht pad: {entry['path']}"


def test_example_output_no_em_dashes() -> None:
    content = (SKILL_DIR / "example_output.md").read_text(encoding="utf-8")
    assert "—" not in content, "example_output.md mag geen em-dashes bevatten"


def test_example_output_has_required_sections() -> None:
    content = (SKILL_DIR / "example_output.md").read_text(encoding="utf-8")
    for header in (
        "# Dagelijkse update",
        "## High-stakes wijzigingen",
        "## Wijzigingen per organisatie",
        "## Nieuwe records",
        "## Vlaggen voor menselijke review",
    ):
        assert header in content, f"missende sectie: {header}"


def test_skill_md_no_banned_phrases() -> None:
    content = _read_skill_md().lower()
    banned = [
        "deep dive",
        "delve",
        "tapestry",
        "groundbreaking",
        "game-changer",
        "paradigm shift",
        "het is belangrijk om op te merken",
        "in het huidige landschap",
        "laten we eerlijk zijn",
        "kortom,",
    ]
    hits = [phrase for phrase in banned if phrase in content]
    assert not hits, f"banned phrases gevonden: {hits}"


def test_script_md_exists() -> None:
    assert (SKILL_DIR / "SCRIPT.md").is_file()
