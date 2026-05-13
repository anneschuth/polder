"""Valideer YAML in `data/` tegen JSON Schemas en cross-record regels."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from jsonschema import Draft202012Validator

Severity = Literal["error", "warning"]

# Map directory under data/ -> schema filename.
SCHEMA_MAP: dict[str, str] = {
    "organisaties": "organisatie.schema.json",
    "personen": "persoon.schema.json",
    "posten": "post.schema.json",
    "mandaten": "mandaat.schema.json",
    "events": "event.schema.json",
}

# 9-digit BSN-like sequence; word boundaries to avoid catching longer numerics.
BSN_RE = re.compile(r"\b\d{9}\b")

# Source IDs that mark a future-dated record as legitimately planned.
PLANNED_SOURCE_IDS = {"planned", "voorgenomen", "aangekondigd"}


@dataclass
class ValidationIssue:
    severity: Severity
    path: Path
    field: str | None
    message: str

    def format(self, *, color: bool) -> str:
        tag = "ERROR" if self.severity == "error" else "WARN"
        if color:
            code = "31" if self.severity == "error" else "33"
            tag = f"\x1b[{code}m{tag}\x1b[0m"
        loc = f"{self.path}"
        if self.field:
            loc = f"{loc}:{self.field}"
        return f"{tag} {loc} - {self.message}"


@dataclass
class Record:
    """One YAML file plus its parsed content and category."""

    path: Path
    category: str  # e.g. "organisaties"
    data: Any
    schema_name: str


@dataclass
class Index:
    """Cross-record lookup tables."""

    org_ids: set[str] = field(default_factory=set)
    person_ids: set[str] = field(default_factory=set)
    post_ids: set[str] = field(default_factory=set)
    mandaat_ids: set[str] = field(default_factory=set)
    posts: dict[str, dict] = field(default_factory=dict)  # post_id -> post record
    # post_id -> list of (start, end-or-None, person_id, source_path)
    post_mandaten: dict[str, list[tuple[date, date | None, str, Path]]] = field(
        default_factory=dict
    )


def load_schemas(schemas_dir: Path) -> dict[str, dict]:
    """Load all schemas referenced in SCHEMA_MAP. Returns {filename: parsed}."""
    out: dict[str, dict] = {}
    for filename in set(SCHEMA_MAP.values()):
        schema_path = schemas_dir / filename
        if not schema_path.exists():
            raise FileNotFoundError(f"Schema niet gevonden: {schema_path}")
        with schema_path.open("r", encoding="utf-8") as fh:
            out[filename] = json.load(fh)
    return out


def _iter_yaml_files(data_dir: Path) -> Iterator[tuple[str, Path]]:
    """Yield (category, path) for each relevant YAML in data_dir."""
    if not data_dir.exists():
        return
    for category in SCHEMA_MAP:
        cat_dir = data_dir / category
        if not cat_dir.exists():
            continue
        for path in sorted(cat_dir.rglob("*.yaml")):
            # Skip files inside a _staging segment.
            if any(part == "_staging" for part in path.relative_to(data_dir).parts):
                continue
            if path.name == ".gitkeep":
                continue
            yield category, path


def load_records(data_dir: Path) -> tuple[list[Record], list[ValidationIssue]]:
    """Parse every YAML file under data_dir. YAML parse errors become issues."""
    records: list[Record] = []
    issues: list[ValidationIssue] = []
    for category, path in _iter_yaml_files(data_dir):
        try:
            with path.open("r", encoding="utf-8") as fh:
                parsed = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=path,
                    field=None,
                    message=f"YAML parse-fout: {exc}",
                )
            )
            continue
        if parsed is None:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=path,
                    field=None,
                    message="Leeg YAML-bestand",
                )
            )
            continue
        records.append(
            Record(
                path=path,
                category=category,
                data=parsed,
                schema_name=SCHEMA_MAP[category],
            )
        )
    return records, issues


def validate_file(path: Path, data: Any, schema: dict) -> list[ValidationIssue]:
    """Run JSON Schema validation, return all errors as issues."""
    validator = Draft202012Validator(schema)
    issues: list[ValidationIssue] = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        field_path = ".".join(str(p) for p in err.absolute_path) or None
        issues.append(
            ValidationIssue(
                severity="error",
                path=path,
                field=field_path,
                message=err.message,
            )
        )
    return issues


def build_index(records: Iterable[Record]) -> Index:
    """Walk all records, fill the cross-record index."""
    idx = Index()
    for rec in records:
        data = rec.data
        if not isinstance(data, dict):
            continue
        rid = data.get("id")
        if rec.category == "organisaties" and isinstance(rid, str):
            idx.org_ids.add(rid)
        elif rec.category == "personen" and isinstance(rid, str):
            idx.person_ids.add(rid)
            for mandaat in data.get("mandaten") or []:
                _index_mandaat(idx, mandaat, person_id=rid, source_path=rec.path)
        elif rec.category == "posten" and isinstance(rid, str):
            idx.post_ids.add(rid)
            idx.posts[rid] = data
        elif rec.category == "mandaten" and isinstance(rid, str):
            idx.mandaat_ids.add(rid)
            person_id = data.get("person_id")
            if isinstance(person_id, str):
                _index_mandaat(idx, data, person_id=person_id, source_path=rec.path)
    return idx


def _index_mandaat(
    idx: Index,
    mandaat: dict,
    *,
    person_id: str,
    source_path: Path,
) -> None:
    post_id = mandaat.get("post_id")
    start = _parse_date(mandaat.get("start_date"))
    if not isinstance(post_id, str) or start is None:
        return
    end = _parse_date(mandaat.get("end_date")) if mandaat.get("end_date") else None
    idx.post_mandaten.setdefault(post_id, []).append((start, end, person_id, source_path))


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def check_referential_integrity(
    records: Iterable[Record],
    idx: Index,
) -> list[ValidationIssue]:
    """Verify every *_id reference resolves to a known record."""
    issues: list[ValidationIssue] = []
    for rec in records:
        data = rec.data
        if not isinstance(data, dict):
            continue
        if rec.category == "organisaties":
            parent = data.get("parent_id")
            if isinstance(parent, str) and parent not in idx.org_ids:
                issues.append(
                    _ref_issue(rec.path, "parent_id", parent, "organisatie")
                )
        elif rec.category == "posten":
            org = data.get("organization_id")
            if isinstance(org, str) and org not in idx.org_ids:
                issues.append(
                    _ref_issue(rec.path, "organization_id", org, "organisatie")
                )
        elif rec.category == "mandaten":
            issues.extend(_check_mandaat_refs(rec.path, data, idx, prefix=""))
        elif rec.category == "personen":
            for i, mandaat in enumerate(data.get("mandaten") or []):
                if not isinstance(mandaat, dict):
                    continue
                issues.extend(
                    _check_mandaat_refs(
                        rec.path, mandaat, idx, prefix=f"mandaten[{i}]."
                    )
                )
        elif rec.category == "events":
            for i, org in enumerate(data.get("affected_org_ids") or []):
                if isinstance(org, str) and org not in idx.org_ids:
                    issues.append(
                        _ref_issue(
                            rec.path,
                            f"affected_org_ids[{i}]",
                            org,
                            "organisatie",
                        )
                    )
    return issues


def _check_mandaat_refs(
    path: Path, mandaat: dict, idx: Index, *, prefix: str
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    org = mandaat.get("organization_id")
    if isinstance(org, str) and org not in idx.org_ids:
        issues.append(_ref_issue(path, f"{prefix}organization_id", org, "organisatie"))
    post = mandaat.get("post_id")
    if isinstance(post, str) and post not in idx.post_ids:
        issues.append(_ref_issue(path, f"{prefix}post_id", post, "post"))
    person = mandaat.get("person_id")
    if isinstance(person, str) and person not in idx.person_ids:
        issues.append(_ref_issue(path, f"{prefix}person_id", person, "persoon"))
    return issues


def _ref_issue(path: Path, field_path: str, value: str, kind: str) -> ValidationIssue:
    return ValidationIssue(
        severity="error",
        path=path,
        field=field_path,
        message=f"Onbekende {kind}-referentie: {value!r}",
    )


def check_inline_mandaat_sources(records: Iterable[Record]) -> list[ValidationIssue]:
    """Inline mandaten in persoon.yaml moeten ook >= 1 source hebben."""
    issues: list[ValidationIssue] = []
    for rec in records:
        if rec.category != "personen" or not isinstance(rec.data, dict):
            continue
        for i, mandaat in enumerate(rec.data.get("mandaten") or []):
            if not isinstance(mandaat, dict):
                continue
            sources = mandaat.get("sources")
            if not isinstance(sources, list) or len(sources) == 0:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        path=rec.path,
                        field=f"mandaten[{i}].sources",
                        message="Inline mandaat zonder bronnen",
                    )
                )
    return issues


# Posten die per definitie multi-seat zijn (gemeenteraad, provinciale staten,
# AB-waterschap, dagelijks bestuur). Overlap is daar verwacht en geen issue.
MULTI_SEAT_CLASSIFICATIONS = {
    "raadslid",
    "statenlid",
    "kamerlid",
    "ab-waterschap",
    "db-waterschap",
    "lid-hcs",
    "rechter",
    "wethouder",
    "gedeputeerde",
}


def check_overlapping_mandaten(idx: Index) -> list[ValidationIssue]:
    """Flag overlap op single-seat posts (seat_count <= 1) als WARNING."""
    issues: list[ValidationIssue] = []
    for post_id, entries in idx.post_mandaten.items():
        post = idx.posts.get(post_id)
        seat_count = post.get("seat_count", 1) if isinstance(post, dict) else 1
        classification = post.get("classification") if isinstance(post, dict) else None
        if classification in MULTI_SEAT_CLASSIFICATIONS:
            continue
        if not isinstance(seat_count, int) or seat_count > 1:
            continue
        ordered = sorted(entries, key=lambda t: t[0])
        for i in range(len(ordered)):
            a_start, a_end, a_person, _a_path = ordered[i]
            for j in range(i + 1, len(ordered)):
                b_start, b_end, b_person, b_path = ordered[j]
                if a_person == b_person:
                    continue
                if _overlaps(a_start, a_end, b_start, b_end):
                    issues.append(
                        ValidationIssue(
                            severity="warning",
                            path=b_path,
                            field=f"post:{post_id}",
                            message=(
                                f"Overlap op single-seat post {post_id} tussen "
                                f"{a_person} en {b_person} "
                                f"({a_start}..{_fmt_date(a_end)} vs "
                                f"{b_start}..{_fmt_date(b_end)})"
                            ),
                        )
                    )
    return issues


def _overlaps(
    a_start: date, a_end: date | None, b_start: date, b_end: date | None
) -> bool:
    """True als twee mandate-perioden elkaar overlappen.

    Nederlandse staatsrechtelijke conventie: een kabinetswissel gebeurt op
    één kalenderdatum waarbij oude bewindspersoon 's ochtends nog
    functioneert en de nieuwe vanaf de middag. We modelleren dit als
    ``end_date_oud == start_date_nieuw`` en beschouwen dat NIET als
    overlap. Alles wat langer dan één dag duurt is wel een overlap.
    """
    a_end_eff = a_end or date.max
    b_end_eff = b_end or date.max
    if a_end == b_start or b_end == a_start:
        return False
    return a_start <= b_end_eff and b_start <= a_end_eff


def _fmt_date(d: date | None) -> str:
    return d.isoformat() if d else "open"


def check_future_valid_until(
    records: Iterable[Record], today: date | None = None
) -> list[ValidationIssue]:
    """`valid_until` in toekomst zonder planned-bron => warning."""
    issues: list[ValidationIssue] = []
    today = today or date.today()
    for rec in records:
        data = rec.data
        if not isinstance(data, dict):
            continue
        valid_until = _parse_date(data.get("valid_until"))
        if valid_until is None or valid_until <= today:
            continue
        if not _has_planned_source(data.get("sources")):
            issues.append(
                ValidationIssue(
                    severity="warning",
                    path=rec.path,
                    field="valid_until",
                    message=(
                        f"valid_until ({valid_until.isoformat()}) ligt in toekomst "
                        "zonder planned-bron"
                    ),
                )
            )
    return issues


def _has_planned_source(sources: Any) -> bool:
    if not isinstance(sources, list):
        return False
    for src in sources:
        if isinstance(src, dict) and src.get("id") in PLANNED_SOURCE_IDS:
            return True
    return False


def check_birth_year_only(records: Iterable[Record]) -> list[ValidationIssue]:
    """Persoon.birth mag enkel `year` bevatten. Schema dekt dit, maar dubbel-check."""
    issues: list[ValidationIssue] = []
    for rec in records:
        if rec.category != "personen" or not isinstance(rec.data, dict):
            continue
        birth = rec.data.get("birth")
        if not isinstance(birth, dict):
            continue
        extra = [k for k in birth if k != "year"]
        if extra:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=rec.path,
                    field="birth",
                    message=f"Geboortedatum bevat onverwachte velden: {sorted(extra)}",
                )
            )
    return issues


def check_bsn_patterns(records: Iterable[Record]) -> list[ValidationIssue]:
    """Scan alle string-waarden op 9-cijferige sequenties."""
    issues: list[ValidationIssue] = []
    for rec in records:
        for path_str, value in _walk_strings(rec.data, ""):
            if BSN_RE.search(value):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        path=rec.path,
                        field=path_str or None,
                        message=f"BSN-achtig patroon (9 cijfers) gevonden: {value!r}",
                    )
                )
    return issues


# ABD-rol-keywords die NOOIT bij een bewindspersoon-classification horen.
# Een raadadviseur, directeur, afdelingshoofd of secretaris-generaal is per
# definitie ambtenaar, geen minister of staatssecretaris.
_ABD_ROLE_KEYWORDS = (
    "raadadviseur",
    "afdelingshoofd",
    "directeur",
    "secretaris-generaal",
    "directeur-generaal",
    "inspecteur-generaal",
    "consultant",
    "kwartiermaker",
    "projectleider",
    "plaatsvervangend secretaris-generaal",
    "plaatsvervangend directeur-generaal",
    "waarnemend secretaris-generaal",
    "waarnemend directeur-generaal",
)


def check_role_classification_mismatch(
    records: Iterable[Record], idx: "Index"
) -> list[ValidationIssue]:
    """Een mandaat met ABD-rol mag niet wijzen naar bewindspersoon-post.

    Voorbeeld: role "raadadviseur Economie en Bedrijfsleven bij het Kabinet
    Minister-President" mapt soms per ongeluk naar post:minister-president-
    min-az (classification bewindspersoon). Dat is een skill- of resolve-
    bug; raadadviseurs zijn ambtenaren, geen bewindspersonen.
    """
    issues: list[ValidationIssue] = []
    for rec in records:
        if rec.category != "personen":
            continue
        data = rec.data
        if not isinstance(data, dict):
            continue
        for m in data.get("mandaten") or []:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "").lower()
            post_id = m.get("post_id")
            if not role or not post_id:
                continue
            post = idx.posts.get(post_id)
            if not isinstance(post, dict):
                continue
            classification = post.get("classification")
            if classification != "bewindspersoon":
                continue
            for kw in _ABD_ROLE_KEYWORDS:
                if kw in role:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            path=rec.path,
                            field=f"mandaten[].post_id={post_id}",
                            message=(
                                f"ABD-rol {kw!r} maps naar bewindspersoon-post "
                                f"{post_id}. Een raadadviseur/directeur/etc. "
                                "kan geen minister of staatssecretaris zijn."
                            ),
                        )
                    )
                    break
    return issues


def _walk_strings(value: Any, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            child = f"{path}.{k}" if path else str(k)
            yield from _walk_strings(v, child)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            child = f"{path}[{i}]"
            yield from _walk_strings(v, child)


def run_all_checks(
    data_dir: Path,
    schemas_dir: Path,
    *,
    today: date | None = None,
) -> list[ValidationIssue]:
    """Top-level driver: load, schema-validate, run cross-record checks."""
    schemas = load_schemas(schemas_dir)
    records, issues = load_records(data_dir)

    for rec in records:
        schema = schemas[rec.schema_name]
        issues.extend(validate_file(rec.path, rec.data, schema))

    idx = build_index(records)
    issues.extend(check_referential_integrity(records, idx))
    issues.extend(check_inline_mandaat_sources(records))
    issues.extend(check_overlapping_mandaten(idx))
    issues.extend(check_future_valid_until(records, today=today))
    issues.extend(check_birth_year_only(records))
    issues.extend(check_bsn_patterns(records))
    issues.extend(check_role_classification_mismatch(records, idx))

    return issues


def count_files(data_dir: Path) -> int:
    return sum(1 for _ in _iter_yaml_files(data_dir))


def print_report(
    issues: list[ValidationIssue],
    n_files: int,
    *,
    stream=None,
) -> None:
    if stream is None:
        stream = sys.stderr
    color = stream.isatty()
    by_path: dict[Path, list[ValidationIssue]] = {}
    for issue in issues:
        by_path.setdefault(issue.path, []).append(issue)

    for path in sorted(by_path):
        for issue in by_path[path]:
            print(issue.format(color=color), file=stream)

    n_errors = sum(1 for i in issues if i.severity == "error")
    n_warnings = sum(1 for i in issues if i.severity == "warning")
    print(
        f"{n_files} files, {n_errors} errors, {n_warnings} warnings",
        file=stream,
    )


def exit_code(issues: list[ValidationIssue], *, strict: bool) -> int:
    n_errors = sum(1 for i in issues if i.severity == "error")
    n_warnings = sum(1 for i in issues if i.severity == "warning")
    if n_errors > 0:
        return 1
    if n_warnings > 0 and strict:
        return 2
    if n_warnings > 0:
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="polder-validate",
        description="Valideer YAML-records in data/ tegen JSON Schema en cross-record regels.",
    )
    parser.add_argument("--data", default="data", type=Path, help="Data directory")
    parser.add_argument(
        "--schemas",
        default="schemas",
        type=Path,
        help="Schemas directory",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero ook bij alleen warnings",
    )
    args = parser.parse_args(argv)

    issues = run_all_checks(args.data, args.schemas)
    n_files = count_files(args.data)
    print_report(issues, n_files)

    n_errors = sum(1 for i in issues if i.severity == "error")
    n_warnings = sum(1 for i in issues if i.severity == "warning")
    if n_errors > 0:
        return 1
    if n_warnings > 0:
        return 2 if args.strict else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
