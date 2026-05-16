"""Extractor voor ROO `<functies>` + `<medewerkers>`-blokken.

ROO bevat ~4.500 functies met ~16.500 medewerkers. We extraheren ze als
staging-proposals (geen auto-merge) zodat `polder roo resolve` ze kan
matchen tegen polder-posten en -personen, met field-aware precedence:

- Person↔post binding (current holder, dates): Staatscourant > ABD-nieuws > ROO.
- Administratieve metadata (functie bestaat, naam, org-membership): ROO canoniek.

Output-format: JSON-array onder `data/_staging/roo-functies-<date>.json`.
Elk record bevat zowel functie-meta als medewerker-list met evidence_snippet
(exact `<naam>`-string) zodat de quote-or-die regel uit project-CLAUDE.md gehaald
wordt.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from lxml import etree

from polder.fetchers.roo import (
    PRIMARY_URL,
    _attr_systeemid,
    _direct_child,
    _direct_children,
    _direct_text,
    _enclosing_organisatie,
    _extract_addresses,
    _extract_contact_block,
    _localname,
    _resolve_type,
    _text,
    build_id,
    download_export,
    roo_type_to_internal,
    slugify,
)

logger = logging.getLogger("polder.fetchers.roo_functies")

SOURCE_ID = "roo"


# ---------------------------------------------------------------------------
# Parent-org resolution voor functies
# ---------------------------------------------------------------------------


def _functie_parent_org_id(functie_node: etree._Element) -> str | None:
    """Bereken `org:<slug>` van de organisatie waaronder de functie hangt.

    `<functie>` zit in een `<functies>`-container die een direct child is
    van `<organisatie>`. We klimmen één `<organisatie>`-niveau omhoog en
    leiden de slug af op exact dezelfde manier als `parse_organisatie`.
    """
    org_node = _enclosing_organisatie(functie_node)
    if org_node is None:
        return None
    raw_type = _resolve_type(org_node)
    mapping = roo_type_to_internal(raw_type)
    if mapping is None:
        return None
    _internal, _sub_folder, prefix = mapping
    name = _direct_text(org_node, "naam") or _direct_text(org_node, "officielenaam")
    if not name:
        return None
    abbr = _direct_text(org_node, "afkorting")
    slug = slugify(abbr) if abbr and len(abbr) <= 12 else slugify(name)
    return build_id(prefix, slug)


def _functie_parent_roo_id(functie_node: etree._Element) -> str | None:
    org_node = _enclosing_organisatie(functie_node)
    if org_node is None:
        return None
    return _attr_systeemid(org_node)


# ---------------------------------------------------------------------------
# Medewerker → proposal-dict
# ---------------------------------------------------------------------------


def _extract_medewerker(med_node: etree._Element) -> dict[str, Any] | None:
    naam = _direct_text(med_node, "naam")
    if not naam:
        return None
    out: dict[str, Any] = {
        "roo_medewerker_id": _attr_systeemid(med_node),
        "naam": naam,
        # Quote-or-die: de letterlijke <naam>-tekst uit ROO.
        "evidence_snippet": naam,
    }
    for tag, key in [
        ("startDatum", "start_date"),
        ("eindDatum", "end_date"),
        ("partijLidmaatschap", "partij_lidmaatschap"),
        ("standplaats", "standplaats"),
        ("installatie", "installatie"),
        ("vergoeding", "vergoeding"),
        ("partijFunctie", "partij_functie"),
    ]:
        v = _direct_text(med_node, tag)
        if v:
            out[key] = v
    rollen_node = _direct_child(med_node, "rollen")
    if rollen_node is not None:
        rollen = [_text(r) for r in _direct_children(rollen_node, "rol") if _text(r)]
        if rollen:
            out["rollen"] = [r for r in rollen if r]
    addresses = _extract_addresses(med_node)
    if addresses:
        out["addresses"] = addresses
    contact = _extract_contact_block(med_node)
    if contact:
        out["contact"] = contact
    return out


def _iter_functie_nodes(root: etree._Element) -> Iterator[etree._Element]:
    """Yield alle `<functie>`-nodes onder root."""
    for elem in root.iter():
        if _localname(elem.tag).lower() == "functie":
            # Skip `<functie>` die NIET binnen een organisatie zitten —
            # ROO heeft soms losse <functie>-records onder GR-bestuursorganen
            # die we nog niet modelleren. parent moet een <functies>-container
            # zijn, en die zit in <organisatie>.
            parent = elem.getparent()
            if parent is None or _localname(parent.tag).lower() != "functies":
                continue
            yield elem


def extract_functies(xml_path: Path) -> list[dict[str, Any]]:
    """Lees ROO-XML en geef proposals terug. Eén proposal per `<functie>`,
    met geneste `medewerkers[]`."""
    with xml_path.open("rb") as fh:
        tree = etree.parse(fh)
    root = tree.getroot()

    proposals: list[dict[str, Any]] = []
    for f in _iter_functie_nodes(root):
        functie_naam = _direct_text(f, "naam")
        if not functie_naam:
            continue

        functie_type = _direct_text(f, "type")
        functie_subtype = _direct_text(f, "subtype")
        parent_org_id = _functie_parent_org_id(f)
        parent_roo_id = _functie_parent_roo_id(f)

        # Kandidaat-post-slug volgens polder-conventie:
        # `post:<role-slug>-<org-suffix-zonder-prefix>`. `polder roo resolve`
        # beslist of die post bestaat (find_post_for_functie).
        org_suffix = ""
        if parent_org_id and parent_org_id.startswith("org:"):
            org_suffix = parent_org_id[len("org:") :]
        post_slug = f"{slugify(functie_naam)}-{org_suffix}".strip("-")
        suggested_post_id = f"post:{post_slug}" if post_slug else None

        medewerkers: list[dict[str, Any]] = []
        meds_container = _direct_child(f, "medewerkers")
        if meds_container is not None:
            for m in _direct_children(meds_container, "medewerker"):
                entry = _extract_medewerker(m)
                if entry:
                    medewerkers.append(entry)

        proposal: dict[str, Any] = {
            "roo_functie_id": _attr_systeemid(f),
            "roo_functie_naam": functie_naam,
            # Quote-or-die: letterlijke `<naam>` van de functie.
            "evidence_snippet": functie_naam,
            "parent_org_id": parent_org_id,
            "parent_roo_id": parent_roo_id,
            "suggested_post_id": suggested_post_id,
            "medewerkers": medewerkers,
            # Field-aware precedence is een *consumer*-regel; deze proposal
            # is informationeel. `polder roo resolve` leest dit en past de
            # regel toe.
            "precedence_note": (
                "Person↔post binding: Staatscourant > ABD > ROO. "
                "Administratieve metadata (functie bestaat, naam): ROO canoniek."
            ),
            # Confidence: ROO is administratief, geen primaire bron voor
            # benoemingen. We vragen menselijke review.
            "confidence": 0.7,
            "confidence_reasoning": (
                "ROO is administratieve current-state, geen benoemingsbron. "
                "Vereist Staatscourant- of ABD-bevestiging voordat een mandaat "
                "auto-gemerged mag worden (two-source rule)."
            ),
        }
        if functie_type:
            proposal["functie_type"] = functie_type
        if functie_subtype:
            proposal["functie_subtype"] = functie_subtype
        proposals.append(proposal)

    return proposals


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def _today() -> str:
    return date.today().isoformat()


def write_staging(proposals: list[dict[str, Any]], out_dir: Path) -> Path:
    """Schrijf proposals naar `data/_staging/roo-functies-<date>.json`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"roo-functies-{_today()}.json"
    payload = {
        "source_id": SOURCE_ID,
        "source_url": PRIMARY_URL,
        "retrieved": _today(),
        "n_functies": len(proposals),
        "n_medewerkers": sum(len(p.get("medewerkers") or []) for p in proposals),
        "proposals": proposals,
    }
    with target.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    return target


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polder roo functies",
        description="Extract ROO functies + medewerkers naar staging-proposals.",
    )
    parser.add_argument("--cache", type=Path, default=Path("_cache"))
    parser.add_argument("--out", type=Path, default=Path("data/_staging"))
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    cache_path = download_export(args.cache)
    proposals = extract_functies(cache_path)
    target = write_staging(proposals, args.out)
    n_med = sum(len(p.get("medewerkers") or []) for p in proposals)
    print(
        f"Wrote {len(proposals)} functie-proposals ({n_med} medewerkers) to {target}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
