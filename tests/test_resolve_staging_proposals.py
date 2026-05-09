"""Tests voor de resolve-staging-proposals skill: frontmatter, voorbeelden en velden."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

SKILL_DIR = (
    Path(__file__).resolve().parent.parent
    / ".claude"
    / "skills"
    / "resolve-staging-proposals"
)

REQUIRED_RESOLVED_FIELDS = {
    "resolved_organization_id",
    "resolved_organization_level",
    "resolved_post_id",
    "resolved_person_id",
    "resolution_confidence",
    "resolution_notes",
    "merge_recommendation",
}

ALLOWED_LEVELS = {"afdeling", "directie", "directoraat-generaal", "ministerie", None}
ALLOWED_RECOMMENDATIONS = {"auto-merge", "needs-review", "skip"}


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
    assert fm.get("name") == "resolve-staging-proposals"
    assert fm.get("description")
    assert fm.get("version") == "0.1.0"
    assert isinstance(fm.get("triggers"), list)
    assert len(fm["triggers"]) >= 3


def test_skill_md_no_em_dashes() -> None:
    content = _read_skill_md()
    assert "—" not in content, "SKILL.md mag geen em-dashes bevatten"


def test_skill_md_length_in_range() -> None:
    lines = _read_skill_md().splitlines()
    assert 80 <= len(lines) <= 150, f"SKILL.md moet 80-150 regels zijn, is {len(lines)}"


def test_skill_md_describes_diff_only_mode() -> None:
    content = _read_skill_md().lower()
    assert "diff-only" in content or "schrijft alleen naar `data/_staging/" in content
    assert "data/_staging/" in content


def test_skill_md_describes_per_field_confidence() -> None:
    content = _read_skill_md().lower()
    assert "resolution_confidence" in content
    assert "per veld" in content or "per-veld" in content


def test_example_input_is_valid_json_array() -> None:
    data = json.loads((SKILL_DIR / "example_input.json").read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) >= 1


def test_example_output_is_valid_json_array() -> None:
    data = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) >= 1


def test_example_output_has_required_resolved_fields() -> None:
    data = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    for entry in data:
        missing = REQUIRED_RESOLVED_FIELDS - set(entry.keys())
        assert not missing, f"Ontbrekende resolved-velden: {missing}"


def test_example_output_levels_allowed() -> None:
    data = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    for entry in data:
        assert entry["resolved_organization_level"] in ALLOWED_LEVELS


def test_example_output_recommendations_allowed() -> None:
    data = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    for entry in data:
        assert entry["merge_recommendation"] in ALLOWED_RECOMMENDATIONS


def test_example_output_per_field_confidence_structure() -> None:
    data = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    for entry in data:
        confidence = entry["resolution_confidence"]
        assert isinstance(confidence, dict)
        assert set(confidence.keys()) >= {"organization", "post", "person"}
        for value in confidence.values():
            assert 0.0 <= float(value) <= 1.0


def test_example_output_preserves_input_fields() -> None:
    """Resolved-output moet de oorspronkelijke proposal-velden behouden."""
    inp = json.loads((SKILL_DIR / "example_input.json").read_text(encoding="utf-8"))
    out = json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))
    assert len(inp) == len(out)
    for inp_entry, out_entry in zip(inp, out):
        for key, value in inp_entry.items():
            assert key in out_entry, f"input-veld {key} ontbreekt in output"
            assert out_entry[key] == value, f"input-veld {key} is gewijzigd"


def test_example_output_no_em_dashes() -> None:
    raw = (SKILL_DIR / "example_output.json").read_text(encoding="utf-8")
    assert "—" not in raw


def test_resolve_staging_script_exists_and_executable() -> None:
    script = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "resolve_staging_local.sh"
    )
    assert script.is_file()
    import os

    assert os.access(script, os.X_OK), "resolve_staging_local.sh moet executable zijn"


def test_cli_has_resolve_staging_subcommand() -> None:
    cli_file = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "polder"
        / "cli"
        / "commands"
        / "skill_cmd.py"
    )
    content = cli_file.read_text(encoding="utf-8")
    assert '"resolve-staging"' in content
    assert "resolve_staging_local.sh" in content
