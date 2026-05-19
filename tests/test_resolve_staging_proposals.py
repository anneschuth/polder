"""Tests voor de resolve-staging-proposals skill: frontmatter, voorbeelden en velden."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

SKILL_DIR = (
    Path(__file__).resolve().parent.parent / ".claude" / "skills" / "resolve-staging-proposals"
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
    for inp_entry, out_entry in zip(inp, out, strict=False):
        for key, value in inp_entry.items():
            assert key in out_entry, f"input-veld {key} ontbreekt in output"
            assert out_entry[key] == value, f"input-veld {key} is gewijzigd"


def test_example_output_no_em_dashes() -> None:
    raw = (SKILL_DIR / "example_output.json").read_text(encoding="utf-8")
    assert "—" not in raw


def test_resolve_staging_runner_is_importable() -> None:
    """De resolve-staging skill draait via polder.llm.runner.run_skill."""
    from polder.llm.runner import run_skill

    assert callable(run_skill)


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
    assert "resolve-staging-proposals" in content


# ---------------------------------------------------------------------------
# Directe match_person-tests (E2 tussenvoegsel, E3 >2 initialen)
#
# Het hoogste-risico-onderdeel van de skill — de heuristische naam-parser —
# had geen enkele directe unit-test. Deze laden main.py per pad en testen
# match_person rechtstreeks.
# ---------------------------------------------------------------------------


def _load_skill_main():
    import importlib.util

    spec = importlib.util.spec_from_file_location("rsp_main", SKILL_DIR / "main.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _persons(*entries: tuple[str, str, str, int | None]) -> dict:
    """entries: (person_id, family, initials, year) -> all_persons-mapping."""
    out: dict = {}
    for pid, family, initials, year in entries:
        data = {"name": {"family": family, "initials": initials}}
        if year is not None:
            data["birth"] = {"year": year}
        out[pid] = (Path(f"/tmp/{pid}.yaml"), data)
    return out


def test_match_person_tussenvoegsel_family() -> None:
    """E2: 'de Boer' moet matchen, niet alleen 'Boer'."""
    m = _load_skill_main()
    persons = _persons(("person:de-boer-ng-1970", "de Boer", "N.G.", 1970))
    pid, notes, conf = m.match_person("Drs. N.G. de Boer", persons)
    assert pid == "person:de-boer-ng-1970", notes
    assert conf >= 0.85


def test_match_person_meervoudig_tussenvoegsel() -> None:
    """E2: 'van der Zee' (twee tussenvoegsels)."""
    m = _load_skill_main()
    persons = _persons(("person:van-der-zee-ki-1966", "van der Zee", "K.I.", 1966))
    pid, notes, _ = m.match_person("mr. K.I. van der Zee", persons)
    assert pid == "person:van-der-zee-ki-1966", notes


def test_match_person_long_initials() -> None:
    """E3: 'J.W.H.M.' (>2 initialen) moet als initialen herkend worden."""
    m = _load_skill_main()
    persons = _persons(
        ("person:a-jwhm-1971", "Jansen", "J.W.H.M.", 1971),
        ("person:a-j-1980", "Jansen", "J.", 1980),
    )
    pid, notes, conf = m.match_person("J.W.H.M. Jansen", persons)
    # Initialen disambigueren tussen de twee Jansens.
    assert pid == "person:a-jwhm-1971", notes
    assert conf >= 0.85


def test_match_person_no_tussenvoegsel_simple_name() -> None:
    """Reguliere naam zonder tussenvoegsel blijft werken."""
    m = _load_skill_main()
    persons = _persons(("person:rutte-m-1967", "Rutte", "M.", 1967))
    pid, notes, _ = m.match_person("Mark Rutte", persons)
    assert pid == "person:rutte-m-1967", notes


def test_match_person_real_birth_year_not_rejected() -> None:
    """E1: een persoon met echt geboortejaar mag niet weggefilterd worden."""
    m = _load_skill_main()
    persons = _persons(("person:bos-a-1970", "Bos", "A.", 1970))
    # birth_year matcht -> moet vinden; mismatch -> moet afwijzen.
    pid_ok, _, _ = m.match_person("A. Bos", persons, birth_year=1970)
    assert pid_ok == "person:bos-a-1970"
    pid_no, _, _ = m.match_person("A. Bos", persons, birth_year=1999)
    assert pid_no is None
