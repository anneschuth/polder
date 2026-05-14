"""Tests voor `polder.llm.cache`.

Monkeypatch `_repo_root` zodat tests de echte `_cache/` niet aanraken.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from polder.llm import cache as cache_mod
from polder.llm.session import SkillResult


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Maakt een tmp_path repo met een fake skill, en re-routet cache_root."""
    skills_dir = tmp_path / ".claude" / "skills" / "parse-abd-nieuws"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("# parse-abd-nieuws\nv1 content\n", encoding="utf-8")

    other_skill = tmp_path / ".claude" / "skills" / "parse-organogram"
    other_skill.mkdir(parents=True)
    (other_skill / "SKILL.md").write_text("# parse-organogram\nv1\n", encoding="utf-8")

    monkeypatch.setattr(cache_mod, "_repo_root", lambda: tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# skill_content_hash
# ---------------------------------------------------------------------------


def test_skill_content_hash_is_deterministic(fake_repo: Path) -> None:
    h1 = cache_mod.skill_content_hash("parse-abd-nieuws")
    h2 = cache_mod.skill_content_hash("parse-abd-nieuws")
    assert h1 == h2
    assert len(h1) == 64  # SHA256 hex


def test_skill_content_hash_changes_when_skill_changes(fake_repo: Path) -> None:
    h1 = cache_mod.skill_content_hash("parse-abd-nieuws")
    skill_md = fake_repo / ".claude" / "skills" / "parse-abd-nieuws" / "SKILL.md"
    skill_md.write_text("# different content\n", encoding="utf-8")
    h2 = cache_mod.skill_content_hash("parse-abd-nieuws")
    assert h1 != h2


# ---------------------------------------------------------------------------
# cache_key
# ---------------------------------------------------------------------------


def test_cache_key_deterministic() -> None:
    k1 = cache_mod.cache_key("skill", "abc", "model-x", b"payload")
    k2 = cache_mod.cache_key("skill", "abc", "model-x", b"payload")
    assert k1 == k2


def test_cache_key_changes_with_skill_hash() -> None:
    k1 = cache_mod.cache_key("skill", "hash-A", "model-x", b"payload")
    k2 = cache_mod.cache_key("skill", "hash-B", "model-x", b"payload")
    assert k1 != k2


def test_cache_key_changes_with_model() -> None:
    k1 = cache_mod.cache_key("skill", "hash", "model-A", b"payload")
    k2 = cache_mod.cache_key("skill", "hash", "model-B", b"payload")
    assert k1 != k2


def test_cache_key_changes_with_input() -> None:
    k1 = cache_mod.cache_key("skill", "hash", "model", b"payload-A")
    k2 = cache_mod.cache_key("skill", "hash", "model", b"payload-B")
    assert k1 != k2


def test_cache_key_changes_with_skill_name() -> None:
    k1 = cache_mod.cache_key("skill-A", "hash", "model", b"x")
    k2 = cache_mod.cache_key("skill-B", "hash", "model", b"x")
    assert k1 != k2


# ---------------------------------------------------------------------------
# lookup / store round-trip
# ---------------------------------------------------------------------------


def test_lookup_returns_none_for_missing_entry(fake_repo: Path) -> None:
    assert cache_mod.lookup("parse-abd-nieuws", "doesnotexist") is None


def test_store_and_lookup_roundtrip(fake_repo: Path) -> None:
    result = SkillResult(
        text="hello world",
        model="claude-haiku-4-5",
        cost_usd=0.012,
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=7000,
        cache_creation_tokens=10,
        rate_limited=False,
        is_error=False,
        error_message=None,
    )
    key = "abc123"
    cache_mod.store("parse-abd-nieuws", key, result)

    loaded = cache_mod.lookup("parse-abd-nieuws", key)
    assert loaded is not None
    assert loaded.text == "hello world"
    assert loaded.model == "claude-haiku-4-5"
    assert loaded.cost_usd == pytest.approx(0.012)
    assert loaded.input_tokens == 100
    assert loaded.output_tokens == 50
    assert loaded.cache_read_tokens == 7000
    assert loaded.cache_creation_tokens == 10
    assert loaded.rate_limited is False
    assert loaded.is_error is False
    assert loaded.error_message is None


def test_store_skipped_for_rate_limited(fake_repo: Path) -> None:
    result = SkillResult(text="", model="m", rate_limited=True)
    cache_mod.store("parse-abd-nieuws", "k1", result)
    assert cache_mod.lookup("parse-abd-nieuws", "k1") is None


def test_store_skipped_for_error(fake_repo: Path) -> None:
    result = SkillResult(text="", model="m", is_error=True, error_message="boom")
    cache_mod.store("parse-abd-nieuws", "k2", result)
    assert cache_mod.lookup("parse-abd-nieuws", "k2") is None


def test_store_creates_cache_directory(fake_repo: Path) -> None:
    assert not cache_mod.cache_root().exists()
    result = SkillResult(text="x", model="m")
    cache_mod.store("parse-abd-nieuws", "k", result)
    assert cache_mod.cache_root().exists()
    assert (cache_mod.cache_root() / "parse-abd-nieuws" / "k.json").exists()


def test_cache_file_is_valid_json_with_unicode(fake_repo: Path) -> None:
    result = SkillResult(text="benoeming van een lid – àéîõü 中文", model="m")  # noqa: RUF001
    cache_mod.store("parse-abd-nieuws", "k", result)

    cache_file = cache_mod.cache_root() / "parse-abd-nieuws" / "k.json"
    raw = cache_file.read_text(encoding="utf-8")
    # Unicode moet niet als \uXXXX worden geschreven (ensure_ascii=False)
    assert "benoeming" in raw
    assert "中文" in raw
    # Valide JSON
    parsed = json.loads(raw)
    assert parsed["text"] == "benoeming van een lid – àéîõü 中文"  # noqa: RUF001


def test_lookup_handles_corrupt_cache_file(fake_repo: Path) -> None:
    cache_file = cache_mod.cache_root() / "parse-abd-nieuws" / "k.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("not valid json {{{", encoding="utf-8")
    assert cache_mod.lookup("parse-abd-nieuws", "k") is None


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


def test_clear_one_skill(fake_repo: Path) -> None:
    cache_mod.store("parse-abd-nieuws", "a", SkillResult(text="1", model="m"))
    cache_mod.store("parse-abd-nieuws", "b", SkillResult(text="2", model="m"))
    cache_mod.store("parse-organogram", "c", SkillResult(text="3", model="m"))

    removed = cache_mod.clear("parse-abd-nieuws")
    assert removed == 2
    assert cache_mod.lookup("parse-abd-nieuws", "a") is None
    # andere skill blijft
    assert cache_mod.lookup("parse-organogram", "c") is not None


def test_clear_all_skills(fake_repo: Path) -> None:
    cache_mod.store("parse-abd-nieuws", "a", SkillResult(text="1", model="m"))
    cache_mod.store("parse-organogram", "b", SkillResult(text="2", model="m"))

    removed = cache_mod.clear(None)
    assert removed == 2
    assert cache_mod.lookup("parse-abd-nieuws", "a") is None
    assert cache_mod.lookup("parse-organogram", "b") is None


def test_clear_returns_zero_for_missing_root(fake_repo: Path) -> None:
    # cache_root() bestaat nog niet
    assert cache_mod.clear() == 0
    assert cache_mod.clear("parse-abd-nieuws") == 0


def test_clear_returns_zero_for_missing_skill_dir(fake_repo: Path) -> None:
    # Maak cache_root maar geen skill-subdir
    cache_mod.cache_root().mkdir(parents=True, exist_ok=True)
    assert cache_mod.clear("never-cached-skill") == 0
