"""Snel een enkel record laden zonder de hele dataset te scannen.

`Polder.local(root)` plus `repo.get(id)` leest 5000+ YAMLs en valideert
ze allemaal alleen om er één terug te geven. Voor scripts en CLI-calls
die maar één entity nodig hebben is dat een paar seconden te duur.

Strategie per type:

- `person:<slug>` → `data/personen/<slug>.yaml`. Filename = slug,
  geen mismatches in de dataset.
- `org:<slug>`   → ripgrep op `id: org:<slug>` binnen `data/organisaties/`.
  Filename ≠ slug voor ministeries en andere gevallen, dus we matchen
  op het `id`-veld in de YAML zelf.
- `post:<slug>`  → ripgrep op `id: post:<slug>` binnen `data/posten/`.
  2000+ posts hebben een filename die afwijkt van de slug.

Geen Pydantic-import bovenaan om de import-tijd niet te belasten;
laden gebeurt lazy in `load_by_id`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


def _rg_find_id(search_dir: Path, id: str) -> Path | None:
    """Vind de YAML-file die `id: <id>` op top-niveau heeft, via ripgrep.

    ripgrep is ~50x sneller dan rglob+yaml.safe_load over 5000 files.
    Fallback op pure Python als rg ontbreekt.
    """
    if not search_dir.exists():
        return None
    rg = shutil.which("rg")
    pattern = f"^id: {id}$"
    if rg is not None:
        try:
            out = subprocess.run(
                [rg, "--files-with-matches", "--max-count", "1", pattern, str(search_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
            if out.returncode == 0 and out.stdout.strip():
                first = out.stdout.splitlines()[0]
                return Path(first)
        except (OSError, subprocess.SubprocessError):
            pass
    # Pure-Python fallback.
    target = f"id: {id}"
    for path in search_dir.rglob("*.yaml"):
        try:
            with path.open("r", encoding="utf-8") as fh:
                for _ in range(10):
                    line = fh.readline()
                    if not line:
                        break
                    if line.strip() == target:
                        return path
        except OSError:
            continue
    return None


def path_for_id(data_dir: Path, id: str) -> Path | None:
    """Geef het YAML-pad voor een id, of None als niet gevonden."""
    if id.startswith("person:"):
        slug = id[len("person:") :]
        candidate = data_dir / "personen" / f"{slug}.yaml"
        if candidate.exists():
            return candidate
        return _rg_find_id(data_dir / "personen", id)

    if id.startswith("post:"):
        slug = id[len("post:") :]
        candidate = data_dir / "posten" / f"{slug}.yaml"
        if candidate.exists():
            return candidate
        return _rg_find_id(data_dir / "posten", id)

    if id.startswith("org:"):
        return _rg_find_id(data_dir / "organisaties", id)

    return None


def load_by_id(data_dir: Path, id: str) -> Any | None:
    """Laad één entity direct van disk. Returnt het model of None.

    Imports gebeuren binnen de functie zodat callers die quick_lookup
    niet aanraken geen Pydantic-tax betalen.
    """
    path = path_for_id(data_dir, id)
    if path is None:
        return None

    from polder.lib.models import Organisatie, Persoon, Post

    if id.startswith("person:"):
        return Persoon.from_yaml(path)
    if id.startswith("post:"):
        return Post.from_yaml(path)
    if id.startswith("org:"):
        return Organisatie.from_yaml(path)
    return None


__all__ = ["load_by_id", "path_for_id"]
