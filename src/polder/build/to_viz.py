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


def _normalize_dates(rec: dict[str, Any]) -> None:
    """Swap omgekeerde valid_from/valid_until paren in-place.

    Polder-data bevat een paar tientallen records waar valid_from > valid_until.
    Die zijn ergens fout ingelezen of dubbel ge-fetched. Tot upstream gefixt is
    swappen we ze hier zodat de viewer klopt.
    """
    vf = rec.get("valid_from")
    vu = rec.get("valid_until")
    if vf and vu and vf > vu:
        rec["valid_from"], rec["valid_until"] = vu, vf


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
    posten_by_org: dict[str, list[dict[str, Any]]] | None = None,
    mandaten_by_post: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any] | None:
    """Recursief opbouwen van geneste tree-struct voor d3.hierarchy."""
    rec = orgs_by_id.get(root_id)
    if rec is None:
        return None
    children: list[dict[str, Any]] = []
    for child_id in adjacency.get(root_id, []):
        child = _render_org_tree(child_id, adjacency, orgs_by_id, posten_by_org, mandaten_by_post)
        if child is not None:
            children.append(child)
    posten = _render_posten(rec["id"], posten_by_org, mandaten_by_post)
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
        "posten": posten,
        "children": children,
    }


def _render_posten(
    org_id: str,
    posten_by_org: dict[str, list[dict[str, Any]]] | None,
    mandaten_by_post: dict[str, list[dict[str, Any]]] | None,
) -> list[dict[str, Any]]:
    if posten_by_org is None or mandaten_by_post is None:
        return []
    out: list[dict[str, Any]] = []
    for post in posten_by_org.get(org_id, []):
        mandaten = mandaten_by_post.get(post["id"], [])
        out.append(
            {
                "id": post["id"],
                "kind": "post",
                "label": post.get("label") or post["id"],
                "classification": post.get("classification"),
                "valid_from": post.get("valid_from"),
                "valid_until": post.get("valid_until"),
                "mandaten": [_render_mandaat(m) for m in mandaten],
            }
        )
    return out


def _render_mandaat(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entry.get("mandaat_id"),
        "person_id": entry["person_id"],
        "person_label": entry["person_label"],
        "person_birth_year": entry.get("person_birth_year"),
        "role": entry.get("role"),
        "start_date": entry.get("start_date"),
        "end_date": entry.get("end_date"),
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
    posten_by_org: dict[str, list[dict[str, Any]]] | None = None,
    mandaten_by_post: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any] | None:
    """Schrijf org/<slug>.json en geef tegel-metadata terug."""
    tree = _render_org_tree(org["id"], adjacency, orgs_by_id, posten_by_org, mandaten_by_post)
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


def _index_posten_and_personen(
    data_dir: Path,
    data_out: Path,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """Bouw posten-per-org en mandaten-per-post indexen, en schrijf person-bundles.

    Mandaten met `post_id` zonder bijhorende post-file krijgen een gesynthetiseerde
    post-record met `classification = 'overig'` zodat hun mandaten in de boom
    blijven verschijnen.
    """
    posten_records = list(_load_records(data_dir / "posten"))
    posten_by_id: dict[str, dict[str, Any]] = {p["id"]: p for p in posten_records if p.get("id")}
    posten_by_org: dict[str, list[dict[str, Any]]] = {}
    for post in posten_records:
        org_id = post.get("organization_id")
        if org_id:
            posten_by_org.setdefault(org_id, []).append(post)

    mandaten_by_post: dict[str, list[dict[str, Any]]] = {}
    for persoon in _load_records(data_dir / "personen"):
        pid = persoon.get("id")
        name = persoon.get("name") or {}
        person_label = name.get("full") or pid or ""
        birth = (persoon.get("birth") or {}).get("year")
        mandaten = persoon.get("mandaten") or []
        if not mandaten or not pid:
            continue

        _write_json(
            data_out / "person" / f"{_slug_from_id(pid)}.json",
            {
                "id": pid,
                "name": name,
                "birth_year": birth,
                "gender": persoon.get("gender"),
                "identifiers": persoon.get("identifiers") or {},
                "mandaten": mandaten,
            },
        )

        for mandaat in mandaten:
            if not isinstance(mandaat, dict):
                continue
            post_id = mandaat.get("post_id")
            if not post_id:
                continue
            if post_id not in posten_by_id:
                posten_by_id[post_id] = {
                    "id": post_id,
                    "organization_id": mandaat.get("organization_id"),
                    "label": mandaat.get("role") or post_id,
                    "classification": "overig",
                    "valid_from": None,
                    "valid_until": None,
                }
                org_id = mandaat.get("organization_id")
                if org_id:
                    posten_by_org.setdefault(org_id, []).append(posten_by_id[post_id])
            mandaten_by_post.setdefault(post_id, []).append(
                {
                    "mandaat_id": mandaat.get("id"),
                    "person_id": pid,
                    "person_label": person_label,
                    "person_birth_year": birth,
                    "role": mandaat.get("role"),
                    "start_date": mandaat.get("start_date"),
                    "end_date": mandaat.get("end_date"),
                }
            )

    return posten_by_org, mandaten_by_post


def build_viz(data_dir: Path, out_dir: Path) -> None:
    """Genereer JSON-bundels naar `out_dir/data/`."""
    data_out = out_dir / "data"
    data_out.mkdir(parents=True, exist_ok=True)

    orgs = list(_load_records(data_dir / "organisaties"))
    for org in orgs:
        _normalize_dates(org)
    orgs_by_id: dict[str, dict[str, Any]] = {o["id"]: o for o in orgs if o.get("id")}
    adjacency = _build_adjacency(orgs)
    orgs_by_type: dict[str, list[dict[str, Any]]] = {}
    for org in orgs:
        orgs_by_type.setdefault(org.get("type") or "", []).append(org)

    posten_by_org, mandaten_by_post = _index_posten_and_personen(data_dir, data_out)

    tiles: list[dict[str, Any]] = []

    for ministerie in sorted(orgs_by_type.get("ministerie", []), key=_short_label):
        tile = _tree_tile(
            ministerie,
            adjacency,
            orgs_by_id,
            data_out,
            "ministerie",
            posten_by_org,
            mandaten_by_post,
        )
        if tile is not None:
            tiles.append(tile)

    for org_type, cat_id, cat_label in CATEGORY_TREE_TYPES:
        members: list[dict[str, Any]] = []
        for org in sorted(orgs_by_type.get(org_type, []), key=_short_label):
            tile = _tree_tile(
                org,
                adjacency,
                orgs_by_id,
                data_out,
                "category-member",
                posten_by_org,
                mandaten_by_post,
            )
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
