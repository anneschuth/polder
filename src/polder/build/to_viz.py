"""Bouw JSON-bundels voor de statische org chart in `dist/site/data/`.

M1 scope: produceert `index.json` met ministerie-tegels en per ministerie een
`org/<slug>.json` met de organisatie-boom (organisatie + organisatieonderdelen
via `parent_id`-traversal). Geen posten, personen, of category-tegels — die
landen in latere milestones (M3/M4).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml


def _walk_yaml(root: Path) -> Iterator[Path]:
    if not root.exists():
        return
    for path in sorted(root.rglob("*.yaml")):
        yield path
    for path in sorted(root.rglob("*.yml")):
        yield path


def _load_records(root: Path) -> Iterator[dict[str, Any]]:
    for path in _walk_yaml(root):
        with path.open("r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        if doc is None:
            continue
        if isinstance(doc, list):
            for entry in doc:
                if isinstance(entry, dict):
                    yield entry
        elif isinstance(doc, dict):
            yield doc


def _slug_from_id(org_id: str) -> str:
    """`org:min-bzk` -> `min-bzk`."""
    return org_id.split(":", 1)[-1]


def _short_label(rec: dict[str, Any]) -> str:
    """Pak een korte label uit names[]: voorkeur voor `abbr`, anders `value`."""
    names = rec.get("names") or []
    for name in names:
        if isinstance(name, dict) and name.get("abbr"):
            return str(name["abbr"])
    for name in names:
        if isinstance(name, dict) and name.get("value"):
            return str(name["value"])
    return _slug_from_id(rec.get("id", ""))


def _full_label(rec: dict[str, Any]) -> str:
    names = rec.get("names") or []
    for name in names:
        if isinstance(name, dict) and name.get("value"):
            return str(name["value"])
    return _slug_from_id(rec.get("id", ""))


def _build_adjacency(orgs: list[dict[str, Any]]) -> dict[str, list[str]]:
    """parent_id -> [child_id]."""
    adjacency: dict[str, list[str]] = {}
    for org in orgs:
        parent_id = org.get("parent_id")
        if parent_id:
            adjacency.setdefault(parent_id, []).append(org["id"])
    return adjacency


def _render_org_tree(
    root_id: str,
    adjacency: dict[str, list[str]],
    orgs_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Recursief opbouwen van geneste tree-struct voor d3.hierarchy."""
    rec = orgs_by_id.get(root_id)
    if rec is None:
        return None
    children: list[dict[str, Any]] = []
    for child_id in adjacency.get(root_id, []):
        child = _render_org_tree(child_id, adjacency, orgs_by_id)
        if child is not None:
            children.append(child)
    return {
        "id": rec["id"],
        "kind": "org",
        "type": rec.get("type"),
        "classification": rec.get("classification"),
        "label": _short_label(rec),
        "label_full": _full_label(rec),
        "names": rec.get("names") or [],
        "valid_from": rec.get("valid_from"),
        "valid_until": rec.get("valid_until"),
        "children": children,
    }


def _count_descendants(node: dict[str, Any]) -> int:
    """Aantal nakomelingen onder een tree-node (exclusief node zelf)."""
    total = 0
    for child in node.get("children") or []:
        total += 1 + _count_descendants(child)
    return total


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))


def build_viz(data_dir: Path, out_dir: Path) -> None:
    """Genereer JSON-bundels naar `out_dir/data/`."""
    data_out = out_dir / "data"
    data_out.mkdir(parents=True, exist_ok=True)

    orgs = list(_load_records(data_dir / "organisaties"))
    orgs_by_id: dict[str, dict[str, Any]] = {o["id"]: o for o in orgs if o.get("id")}
    adjacency = _build_adjacency(orgs)

    ministeries = sorted(
        (o for o in orgs if o.get("type") == "ministerie"),
        key=lambda o: _short_label(o),
    )

    tiles: list[dict[str, Any]] = []
    for ministerie in ministeries:
        tree = _render_org_tree(ministerie["id"], adjacency, orgs_by_id)
        if tree is None:
            continue
        slug = _slug_from_id(ministerie["id"])
        bundle_path = data_out / "org" / f"{slug}.json"
        _write_json(bundle_path, tree)

        tiles.append(
            {
                "kind": "ministerie",
                "id": ministerie["id"],
                "label": _short_label(ministerie),
                "label_full": _full_label(ministerie),
                "names": ministerie.get("names") or [],
                "valid_from": ministerie.get("valid_from"),
                "valid_until": ministerie.get("valid_until"),
                "descendant_org_count": _count_descendants(tree),
                "bundle": f"org/{slug}.json",
            }
        )

    index = {
        "schema_version": 1,
        "tiles": tiles,
    }
    _write_json(data_out / "index.json", index)
