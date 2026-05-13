"""Bouw JSON-bundels voor de statische org chart in `dist/site/data/`.

Produceert:
- `index.json` met top-level tegels: ministeries, category-tree (agentschappen,
  provincies, hoge-colleges, GR's, politie-om, inspecties, rechterlijke-macht,
  caribisch-nederland) en category-flat (gemeenten, waterschappen, zbo, rwt,
  adviescolleges).
- `org/<slug>.json` per ministerie en per category-tree member: geneste
  organisatie-boom via `parent_id`-traversal.
- `cat/<slug>.json` per category-flat: platte lijst van organisaties.

Geen posten of personen — die landen in M4.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SITE_SOURCE_DIR = REPO_ROOT / "site"


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


CATEGORY_TREE_TYPES: tuple[tuple[str, str, str], ...] = (
    ("agentschap", "cat:agentschappen", "Agentschappen"),
    ("hoge-college", "cat:hoge-colleges", "Hoge Colleges van Staat"),
    ("provincie", "cat:provincies", "Provincies"),
    ("gemeenschappelijke-regeling", "cat:gr", "Gemeenschappelijke regelingen"),
    ("politie", "cat:politie", "Politie en OM"),
    ("inspectie", "cat:inspecties", "Inspecties"),
    ("rechterlijke-instantie", "cat:rechterlijke-macht", "Rechterlijke macht"),
    ("caribisch-openbaar-lichaam", "cat:bes", "Caribisch Nederland"),
)

CATEGORY_FLAT_TYPES: tuple[tuple[str, str, str], ...] = (
    ("gemeente", "cat:gemeenten", "Gemeenten"),
    ("waterschap", "cat:waterschappen", "Waterschappen"),
    ("zbo", "cat:zbo", "ZBO's"),
    ("rwt", "cat:rwt", "RWT's"),
    ("adviescollege", "cat:adviescolleges", "Adviescolleges"),
)

BESTUURSLAGEN: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "layer:rijk",
        "Rijk",
        ("ministerie", "agentschap", "hoge-college", "inspectie"),
    ),
    (
        "layer:decentraal",
        "Decentraal",
        ("provincie", "gemeente", "waterschap", "gemeenschappelijke-regeling"),
    ),
    (
        "layer:toezicht-rechtspraak",
        "Toezicht en Rechtspraak",
        ("rechterlijke-instantie", "politie"),
    ),
)


def _tree_tile(
    org: dict[str, Any],
    adjacency: dict[str, list[str]],
    orgs_by_id: dict[str, dict[str, Any]],
    data_out: Path,
    kind: str,
) -> dict[str, Any] | None:
    """Schrijf org/<slug>.json en geef tegel-metadata terug."""
    tree = _render_org_tree(org["id"], adjacency, orgs_by_id)
    if tree is None:
        return None
    slug = _slug_from_id(org["id"])
    _write_json(data_out / "org" / f"{slug}.json", tree)
    return {
        "kind": kind,
        "id": org["id"],
        "org_type": org.get("type"),
        "label": _short_label(org),
        "label_full": _full_label(org),
        "names": org.get("names") or [],
        "valid_from": org.get("valid_from"),
        "valid_until": org.get("valid_until"),
        "descendant_org_count": _count_descendants(tree),
        "bundle": f"org/{slug}.json",
    }


def _flat_item(org: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": org["id"],
        "label": _short_label(org),
        "label_full": _full_label(org),
        "names": org.get("names") or [],
        "valid_from": org.get("valid_from"),
        "valid_until": org.get("valid_until"),
        "identifiers": org.get("identifiers") or {},
    }


def _group_by_bestuurslaag(tiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Groepeer tegels per bestuurslaag; ongegroepeerd verschijnt onder 'Overig'."""
    by_type: dict[str, list[str]] = {}
    for tile in tiles:
        ot = tile.get("org_type")
        if ot:
            by_type.setdefault(ot, []).append(tile["id"])

    assigned: set[str] = set()
    layers: list[dict[str, Any]] = []
    for layer_id, layer_label, layer_types in BESTUURSLAGEN:
        tile_ids: list[str] = []
        for org_type in layer_types:
            tile_ids.extend(by_type.get(org_type, []))
        if not tile_ids:
            continue
        assigned.update(tile_ids)
        layers.append({"id": layer_id, "label": layer_label, "tile_ids": tile_ids})

    leftover = [t["id"] for t in tiles if t["id"] not in assigned]
    if leftover:
        layers.append({"id": "layer:overig", "label": "Overig", "tile_ids": leftover})
    return layers


def build_viz(data_dir: Path, out_dir: Path) -> None:
    """Genereer JSON-bundels naar `out_dir/data/`."""
    data_out = out_dir / "data"
    data_out.mkdir(parents=True, exist_ok=True)

    orgs = list(_load_records(data_dir / "organisaties"))
    orgs_by_id: dict[str, dict[str, Any]] = {o["id"]: o for o in orgs if o.get("id")}
    adjacency = _build_adjacency(orgs)
    orgs_by_type: dict[str, list[dict[str, Any]]] = {}
    for org in orgs:
        orgs_by_type.setdefault(org.get("type") or "", []).append(org)

    tiles: list[dict[str, Any]] = []

    for ministerie in sorted(orgs_by_type.get("ministerie", []), key=_short_label):
        tile = _tree_tile(ministerie, adjacency, orgs_by_id, data_out, "ministerie")
        if tile is not None:
            tiles.append(tile)

    for org_type, cat_id, cat_label in CATEGORY_TREE_TYPES:
        members: list[dict[str, Any]] = []
        for org in sorted(orgs_by_type.get(org_type, []), key=_short_label):
            tile = _tree_tile(org, adjacency, orgs_by_id, data_out, "category-member")
            if tile is not None:
                members.append(tile)
        if not members:
            continue
        tiles.append(
            {
                "kind": "category-tree",
                "id": cat_id,
                "org_type": org_type,
                "label": cat_label,
                "members": members,
                "count": len(members),
            }
        )

    for org_type, cat_id, cat_label in CATEGORY_FLAT_TYPES:
        items = sorted(orgs_by_type.get(org_type, []), key=_short_label)
        if not items:
            continue
        slug = cat_id.split(":", 1)[-1]
        bundle_path = data_out / "cat" / f"{slug}.json"
        _write_json(
            bundle_path,
            {
                "id": cat_id,
                "label": cat_label,
                "count": len(items),
                "items": [_flat_item(o) for o in items],
            },
        )
        tiles.append(
            {
                "kind": "category-flat",
                "id": cat_id,
                "org_type": org_type,
                "label": cat_label,
                "count": len(items),
                "bundle": f"cat/{slug}.json",
            }
        )

    layers = _group_by_bestuurslaag(tiles)

    index = {
        "schema_version": 1,
        "tiles": tiles,
        "layers": layers,
    }
    _write_json(data_out / "index.json", index)

    _copy_site_assets(out_dir)


def _copy_site_assets(out_dir: Path) -> None:
    """Kopieer site/ source-bestanden naar out_dir voor self-contained publish."""
    if not SITE_SOURCE_DIR.exists():
        return
    for entry in ("index.html", "src", "styles", "vendor"):
        src = SITE_SOURCE_DIR / entry
        if not src.exists():
            continue
        dest = out_dir / entry
        if src.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
