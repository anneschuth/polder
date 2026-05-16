"""ROO round-trip reconstructie-test.

Bewijst dat polder een strict superset van ROO is: elk leaf-veld dat ROO in
zijn XML exporteert moet ergens in de bijbehorende YAML aanwezig zijn.

We doen geen YAML→XML renderen en byte-diffen. Dat is fragile (whitespace,
attribute-ordering, namespace-prefixes) en test de verkeerde richting. In
plaats daarvan: voor elk leaf-element in ROO-XML checken we of zijn waarde
ergens in de YAML te vinden is. Dat test de superset-richting direct.

Allow-list voor velden die polder bewust niet bijhoudt staat in
`_ALLOWED_MISSING_PATHS`. Alles buiten die lijst dat niet matcht = bug.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from lxml import etree

from polder.fetchers.roo import (
    _attr_systeemid,
    _localname,
    _resolve_type,
    build_id,
    roo_type_to_internal,
    slugify,
)

# Element-paden die we bewust niet bijhouden. Format: list of substrings die
# moeten voorkomen in het XML-pad om geskipt te worden. Pad-formaat:
# "organisatie/<localname>/<localname>/...".
_ALLOWED_MISSING_PATHS: tuple[str, ...] = (
    # `<personeel/>` is in 99% van de records een lege self-closing tag;
    # in de overige paar records is de inhoud niet gestandaardiseerd genoeg
    # om in de schema te modelleren. Phase 3.
    "/personeel",
    # `<functies>` en `<medewerkers>` worden door Phase 3 (roo_functies) als
    # aparte staging-proposals afgehandeld, niet als organisatie-velden.
    "/functies",
    # `<types>` is een container; we bewaren de inhoud in `type` (string).
    "/types",
    # `<organisaties>` onder een org zijn nested organisatieonderdelen; die
    # krijgen hun eigen YAML-record en worden separate gevalideerd.
    "/organisaties/",
    # `<identificatiecodes>/<resourceIdentifier @naam>` — het `@naam`-attribute
    # benoemt het type identifier (OIN, rsin, KVK-nummer, …); we gebruiken
    # dat als YAML-key onder `identifiers.*`, niet als string-waarde. De
    # identifier-waarde zelf wordt wel gedekt door /resourceIdentifier (text).
    "/identificatiecodes/resourceIdentifier@naam",
    # `<beleidsterrein @resourceIdentifier>` — de TOOI-URI naar de
    # beleidsterrein-thesaurus; we slaan die op onder `policy_areas[].tooi`,
    # maar de `@resourceIdentifier`-attribute-naam is niet dezelfde naam als
    # die we elders voor TOOI gebruiken. Allow als de leaf-text (naam) wel
    # aanwezig is.
    "/beleidsterreinen/beleidsterrein@resourceIdentifier",
)


@dataclass
class FieldCoverage:
    """Coverage per ROO-element. `seen` is # records waar het element voorkomt;
    `matched` is # records waar de waarde terug te vinden was in de YAML."""

    seen: int = 0
    matched: int = 0

    @property
    def ratio(self) -> float:
        return self.matched / self.seen if self.seen else 1.0


@dataclass
class CoverageReport:
    fields: dict[str, FieldCoverage] = field(default_factory=lambda: defaultdict(FieldCoverage))
    missing_records: list[str] = field(default_factory=list)
    unmatched_examples: list[tuple[str, str, str]] = field(default_factory=list)

    def record_seen(self, path: str) -> None:
        self.fields[path].seen += 1

    def record_matched(self, path: str) -> None:
        self.fields[path].matched += 1


# ---------------------------------------------------------------------------
# YAML-side: bouw een index van alle stringwaarden in een record
# ---------------------------------------------------------------------------


def _flatten_to_strings(obj: Any) -> Iterator[str]:
    """Yield alle string-leaves uit een nested dict/list-structuur."""
    if obj is None:
        return
    if isinstance(obj, str):
        if obj:
            yield obj
        return
    if isinstance(obj, int | float | bool):
        yield str(obj)
        return
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _flatten_to_strings(v)
        return
    if isinstance(obj, list):
        for v in obj:
            yield from _flatten_to_strings(v)
        return


def _normalize_value(s: str) -> str:
    """Normaliseer een string voor matching: trim, collapse whitespace,
    lowercase. Niet aggressief diakritiek-strippen — dat zou false matches
    kunnen geven tussen 'á' en 'a'."""
    return re.sub(r"\s+", " ", s).strip().lower()


def _build_yaml_value_set(record: dict[str, Any]) -> set[str]:
    """Bouw een verzameling van alle genormaliseerde stringwaarden in een
    YAML-record. Gebruikt voor "is dit ROO-leaf-element ergens in de YAML?"."""
    out: set[str] = set()
    for s in _flatten_to_strings(record):
        n = _normalize_value(s)
        if n:
            out.add(n)
    return out


# ---------------------------------------------------------------------------
# XML-side: walk leaf-elementen + attributes
# ---------------------------------------------------------------------------


def _is_allowed_missing(xml_path: str) -> bool:
    return any(allowed in xml_path for allowed in _ALLOWED_MISSING_PATHS)


def _iter_leaves(elem: etree._Element, prefix: str = "") -> Iterator[tuple[str, str]]:
    """Yield (xml_path, value) voor elke leaf-tekst en attribuut in elem.

    Recursief over children. Pad-formaat: '/organisatie/naam' of
    '/organisatie/identificatiecodes/resourceIdentifier@naam'.
    """
    local = _localname(elem.tag)
    cur_path = f"{prefix}/{local}"

    # Attributen — alleen die met een betekenisvolle naam
    for k, v in elem.attrib.items():
        attr_local = _localname(k)
        if not v or not v.strip():
            continue
        # Skip namespace-declaraties en xsi-instance-attributen
        if attr_local.startswith("xmlns") or attr_local.startswith("xsi"):
            continue
        yield (f"{cur_path}@{attr_local}", v.strip())

    children = list(elem)
    if not children:
        text = (elem.text or "").strip()
        if text:
            yield (cur_path, text)
        return

    for child in children:
        yield from _iter_leaves(child, cur_path)


# ---------------------------------------------------------------------------
# Main: per-org compare
# ---------------------------------------------------------------------------


def _xml_org_id(org_node: etree._Element) -> str | None:
    """Bereken `org:<slug>`-id uit een ROO XML-node, identiek aan parse_organisatie."""
    raw_type = _resolve_type(org_node)
    mapping = roo_type_to_internal(raw_type)
    if mapping is None:
        return None
    _internal, _sub_folder, prefix = mapping

    # naam direct child
    name = None
    for c in org_node:
        if _localname(c.tag).lower() in ("naam", "officielenaam") and (c.text or "").strip():
            name = c.text.strip()
            break
    if not name:
        return None
    abbr = None
    for c in org_node:
        if _localname(c.tag).lower() == "afkorting" and (c.text or "").strip():
            abbr = c.text.strip()
            break
    slug = slugify(abbr) if abbr and len(abbr) <= 12 else slugify(name)
    return build_id(prefix, slug)


def _load_yaml_index(data_dir: Path) -> dict[str, Path]:
    """Bouw index `org:<slug>` → YAML-pad. Ook indexed by roo_id.

    Verwacht `data_dir` te zijn `data/organisaties/`. Voor backwards-compat
    bij eerdere callers met `data/` als arg, blijven we tolerant — we
    parsen alle yamls onder de tree maar ignoren niet-org-records.
    """
    out: dict[str, Path] = {}
    for path in data_dir.rglob("*.yaml"):
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            import logging

            logging.getLogger(__name__).warning("Kan yaml niet parsen: %s (%s)", path, exc)
            continue
        if not isinstance(data, dict):
            continue
        org_id = data.get("id")
        if isinstance(org_id, str):
            out[org_id] = path
        ids = data.get("identifiers") or {}
        roo_id = ids.get("roo_id")
        if isinstance(roo_id, str):
            out[f"roo:{roo_id}"] = path
    return out


def _strip_namespaces(root: etree._Element) -> None:
    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]


def compare_org(
    org_node: etree._Element,
    yaml_record: dict[str, Any],
    report: CoverageReport,
) -> None:
    """Vergelijk één XML-organisatie met de bijbehorende YAML."""
    yaml_values = _build_yaml_value_set(yaml_record)

    for xml_path, value in _iter_leaves(org_node):
        # Strip de leading `/organisatie` of `/regeling` zodat het pad
        # vergelijkbaar is over types.
        normalized_path = re.sub(r"^/(?:organisatie|regeling)", "", xml_path)
        if _is_allowed_missing(normalized_path):
            continue
        report.record_seen(normalized_path)
        if _normalize_value(value) in yaml_values:
            report.record_matched(normalized_path)
        elif len(report.unmatched_examples) < 50:
            org_id = yaml_record.get("id", "?")
            report.unmatched_examples.append((org_id, normalized_path, value[:100]))


def run_roundtrip(
    xml_path: Path,
    data_dir: Path,
) -> CoverageReport:
    """Vergelijk élke ROO-XML organisatie met zijn polder-YAML."""
    report = CoverageReport()
    yaml_index = _load_yaml_index(data_dir)

    with xml_path.open("rb") as fh:
        tree = etree.parse(fh)
    root = tree.getroot()
    _strip_namespaces(root)

    for org_node in root.iter("organisatie"):
        # Alleen top-level organisaties; nested organisatieonderdelen krijgen
        # hun eigen pass via parse_organisatie's recursie en hebben hun eigen
        # YAML-record.
        org_id = _xml_org_id(org_node)
        sysid = _attr_systeemid(org_node)

        candidate = None
        if org_id and org_id in yaml_index:
            candidate = yaml_index[org_id]
        elif sysid and f"roo:{sysid}" in yaml_index:
            candidate = yaml_index[f"roo:{sysid}"]

        if candidate is None:
            naam = ""
            for c in org_node:
                if _localname(c.tag).lower() == "naam" and (c.text or "").strip():
                    naam = c.text.strip()
                    break
            report.missing_records.append(f"{org_id or sysid}: {naam}")
            continue

        with candidate.open("r", encoding="utf-8") as fh:
            yaml_record = yaml.safe_load(fh) or {}

        compare_org(org_node, yaml_record, report)

    # GR-records analoog
    for reg_node in root.iter("regeling"):
        sysid = _attr_systeemid(reg_node)
        candidate = None
        if sysid and f"roo:{sysid}" in yaml_index:
            candidate = yaml_index[f"roo:{sysid}"]
        if candidate is None:
            titel = ""
            for c in reg_node:
                if _localname(c.tag).lower() == "titel" and (c.text or "").strip():
                    titel = c.text.strip()
                    break
            report.missing_records.append(f"GR roo:{sysid}: {titel}")
            continue
        with candidate.open("r", encoding="utf-8") as fh:
            yaml_record = yaml.safe_load(fh) or {}
        compare_org(reg_node, yaml_record, report)

    return report


def format_report(report: CoverageReport, *, top_n: int = 30) -> str:
    """Format report als leesbare string."""
    lines: list[str] = []

    total_seen = sum(f.seen for f in report.fields.values())
    total_matched = sum(f.matched for f in report.fields.values())
    overall = total_matched / total_seen if total_seen else 1.0

    lines.append("=== ROO round-trip report ===")
    lines.append(
        f"Overall: {total_matched:>8d} / {total_seen:>8d} leaves matched ({overall * 100:.2f}%)"
    )
    lines.append(f"Records with no YAML match: {len(report.missing_records)}")
    lines.append("")
    lines.append(f"Per-field coverage (sorted by # missing, top {top_n}):")
    lines.append(f"  {'coverage':>10s}  {'matched/seen':>15s}  field")
    sorted_fields = sorted(
        report.fields.items(),
        key=lambda kv: (kv[1].seen - kv[1].matched, -kv[1].seen),
        reverse=True,
    )
    for path, cov in sorted_fields[:top_n]:
        missing = cov.seen - cov.matched
        if missing == 0:
            continue
        lines.append(
            f"  {cov.ratio * 100:>9.2f}%  {cov.matched:>7d}/{cov.seen:<7d}  "
            f"{path}  ({missing} missing)"
        )

    if report.missing_records:
        lines.append("")
        lines.append(f"Sample missing records (top 10 of {len(report.missing_records)}):")
        for r in report.missing_records[:10]:
            lines.append(f"  - {r}")

    if report.unmatched_examples:
        lines.append("")
        lines.append(f"Sample unmatched leaves (first {len(report.unmatched_examples)}):")
        for org_id, path, value in report.unmatched_examples[:20]:
            lines.append(f"  {org_id}  {path}  {value!r}")

    return "\n".join(lines)


def emit_field_map(report: CoverageReport) -> str:
    """Genereer markdown-tabel: ROO XML-pad → coverage-percentage.

    Bedoeld voor `docs/roo_field_map.md`. Per veld: pad, # records waar het
    voorkomt, # records waar polder het terug kan vinden, percentage. Sortering
    op coverage descending zodat 100%-velden bovenaan staan en de gaten
    zichtbaar onderaan.
    """
    lines = [
        "# ROO field-map",
        "",
        "Per ROO XML-leaf-element: hoe vaak komt het voor in de export, en in",
        "hoeveel procent van die gevallen kan polder de waarde terugleveren.",
        "Gegenereerd door `polder roo roundtrip --emit-field-map`.",
        "",
        f"Totaal velden: **{len(report.fields)}**.",
        "",
        "| Coverage | Matched / Seen | XML-pad |",
        "| ---: | ---: | :--- |",
    ]
    sorted_fields = sorted(
        report.fields.items(),
        key=lambda kv: (-kv[1].ratio, -kv[1].seen),
    )
    for path, cov in sorted_fields:
        lines.append(f"| {cov.ratio * 100:.2f}% | {cov.matched} / {cov.seen} | `{path}` |")
    return "\n".join(lines) + "\n"
