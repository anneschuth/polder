"""Response-cache voor LLM-calls.

Stapelt boven op Anthropic's prompt-cache binnen één sessie. Bij re-runs
(schema-tweaks, debug-sessies) zijn alle cache-hits gratis (geen `claude -p`
aanroep). Cache-key bevat de SHA256 van de SKILL.md-content, zodat een
skill-tweak automatisch alleen de relevante entries invalideert.

Storage: `_cache/llm-responses/<skill_name>/<sha256>.json`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from pathlib import Path

from polder.llm.session import SkillResult

logger = logging.getLogger("polder.llm.cache")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def cache_root() -> Path:
    """Pad naar de cache-directory (niet aangemaakt)."""
    return _repo_root() / "_cache" / "llm-responses"


def skill_content_hash(skill_name: str) -> str:
    """SHA256 van de SKILL.md-inhoud, hex-encoded."""
    skill_path = _repo_root() / ".claude" / "skills" / skill_name / "SKILL.md"
    content = skill_path.read_bytes()
    return hashlib.sha256(content).hexdigest()


def cache_key(skill_name: str, skill_hash: str, model: str, input_bytes: bytes) -> str:
    """Bouw cache-key uit skill-content, model, en input."""
    h = hashlib.sha256()
    h.update(skill_name.encode("utf-8"))
    h.update(b"\x00")
    h.update(skill_hash.encode("utf-8"))
    h.update(b"\x00")
    h.update(model.encode("utf-8"))
    h.update(b"\x00")
    h.update(input_bytes)
    return h.hexdigest()


def _cache_path(skill_name: str, key: str) -> Path:
    return cache_root() / skill_name / f"{key}.json"


def lookup(skill_name: str, key: str) -> SkillResult | None:
    """Geef een eerder gecachte SkillResult terug, of None."""
    path = _cache_path(skill_name, key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Cache file %s onleesbaar: %s; overslaan", path, e)
        return None
    return SkillResult(**data)


def store(skill_name: str, key: str, result: SkillResult) -> None:
    """Schrijf SkillResult naar disk. Rate-limited results worden niet gecached."""
    if result.rate_limited or result.is_error:
        return
    path = _cache_path(skill_name, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear(skill_name: str | None = None) -> int:
    """Verwijder cache-entries; retourneert aantal weggegooide files."""
    root = cache_root()
    if not root.exists():
        return 0
    target = root if skill_name is None else root / skill_name
    if not target.exists():
        return 0
    count = 0
    for path in target.rglob("*.json"):
        path.unlink()
        count += 1
    return count
