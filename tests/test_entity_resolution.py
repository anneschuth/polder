"""Tests voor de entity-resolution skill: frontmatter, voorbeelden en slug-conventie."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

SKILL_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "entity-resolution"

SLUG_RE = re.compile(r"^person:[a-z][a-z0-9-]*-[a-z0-9]+-(\d{4}|unknown)$")
PROPOSED_SLUG_BODY_RE = re.compile(r"^[a-z][a-z0-9-]*-[a-z0-9]+-(\d{4}|unknown)$")


def _read_skill_md() -> str:
    return (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")


def _parse_frontmatter(content: str) -> dict:
    assert content.startswith("---"), "SKILL.md moet beginnen met YAML-frontmatter"
    end_marker = content.index("\n---", 3)
    fm_text = content[3:end_marker].strip()
    return yaml.safe_load(fm_text)


def _read_input() -> dict:
    return json.loads((SKILL_DIR / "example_input.json").read_text(encoding="utf-8"))


def _read_output() -> dict:
    return json.loads((SKILL_DIR / "example_output.json").read_text(encoding="utf-8"))


def test_skill_md_exists() -> None:
    assert (SKILL_DIR / "SKILL.md").is_file()


def test_skill_md_frontmatter_valid_yaml() -> None:
    fm = _parse_frontmatter(_read_skill_md())
    assert isinstance(fm, dict)


def test_skill_md_frontmatter_required_fields() -> None:
    fm = _parse_frontmatter(_read_skill_md())
    assert fm.get("name") == "entity-resolution"
    assert fm.get("description"), "description is verplicht"
    assert fm.get("version"), "version is verplicht"


def test_skill_md_version_is_0_2_0() -> None:
    fm = _parse_frontmatter(_read_skill_md())
    assert fm["version"] == "0.2.0", "SKILL.md moet versie 0.2.0 zijn"


def test_skill_md_no_em_dashes() -> None:
    assert "—" not in _read_skill_md(), "SKILL.md mag geen em-dashes bevatten"


def test_skill_md_no_banned_phrases() -> None:
    content = _read_skill_md().lower()
    banned = [
        "het is belangrijk om op te merken",
        "in het huidige landschap",
        "deep dive",
        "game-changer",
        "baanbrekend",
        "kortom,",
        "laten we eerlijk zijn",
    ]
    hits = [phrase for phrase in banned if phrase in content]
    assert not hits, f"SKILL.md bevat banned phrases: {hits}"


def test_example_input_is_valid_json() -> None:
    data = _read_input()
    assert isinstance(data, dict)
    assert "name" in data
    assert "candidates" in data
    assert isinstance(data["candidates"], list)
    assert len(data["candidates"]) >= 1


def test_example_output_is_valid_json() -> None:
    data = _read_output()
    assert isinstance(data, dict)


def test_example_output_has_required_fields() -> None:
    data = _read_output()
    required = {"matched_id", "proposed_id", "confidence", "reasoning", "alternative_candidates"}
    missing = required - set(data.keys())
    assert not missing, f"Ontbrekende velden: {missing}"


def test_example_output_confidence_in_range() -> None:
    data = _read_output()
    confidence = data["confidence"]
    assert isinstance(confidence, (int, float))
    assert 0.0 <= float(confidence) <= 1.0


def test_example_output_matched_or_proposed() -> None:
    """Precies één van matched_id of proposed_id is gevuld."""
    data = _read_output()
    matched = data["matched_id"]
    proposed = data["proposed_id"]
    assert (matched is None) != (
        proposed is None
    ), "Precies één van matched_id of proposed_id moet gevuld zijn."


def test_matched_id_follows_slug_convention() -> None:
    data = _read_output()
    matched = data["matched_id"]
    if matched is not None:
        assert SLUG_RE.match(matched), f"matched_id volgt slug-conventie niet: {matched}"


def test_proposed_id_follows_slug_convention() -> None:
    data = _read_output()
    proposed = data["proposed_id"]
    if proposed is not None:
        assert proposed.startswith("person:"), f"proposed_id moet met 'person:' starten: {proposed}"
        body = proposed.split(":", 1)[1]
        assert PROPOSED_SLUG_BODY_RE.match(
            body
        ), f"proposed_id-body voldoet niet aan <family>-<initials>-<year>: {proposed}"


def test_alternative_candidates_present_when_below_high_confidence() -> None:
    data = _read_output()
    if float(data["confidence"]) < 0.95:
        alts = data.get("alternative_candidates")
        assert (
            isinstance(alts, list) and len(alts) >= 1
        ), "alternative_candidates moet aanwezig zijn als confidence < 0.95"
        for alt in alts:
            assert "id" in alt
            assert "score" in alt
            assert "reason" in alt
            assert 0.0 <= float(alt["score"]) <= 1.0


def test_candidate_ids_in_output_exist_in_input() -> None:
    """matched_id en alternatives moeten uit candidates[] komen (diff-only mode)."""
    inp = _read_input()
    out = _read_output()
    candidate_ids = {c["id"] for c in inp["candidates"]}
    if out["matched_id"] is not None:
        assert out["matched_id"] in candidate_ids, "matched_id moet uit candidates komen"
    for alt in out.get("alternative_candidates") or []:
        assert alt["id"] in candidate_ids, f"alternative {alt['id']} niet in candidates"
