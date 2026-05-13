"""Schrijf CSV-files naar `dist/csv/` volgens RFC 4180."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

ORG_FIELDS = [
    "id",
    "type",
    "classification",
    "parent_id",
    "valid_from",
    "valid_until",
    "name",
    "abbr",
    "oin",
    "tooi",
    "wikidata",
    "roo_id",
    "kvk",
    "rsin",
    "website",
    "bezoekadres",
    "postadres",
    "email",
]

PERSOON_FIELDS = [
    "id",
    "name_full",
    "name_family",
    "name_given",
    "name_initials",
    "gender",
    "birth_year",
    "wikidata",
    "tk_persoon_id",
    "abd_id",
    "allmanak_id",
]

POST_FIELDS = [
    "id",
    "organization_id",
    "label",
    "classification",
    "seat_count",
    "valid_from",
    "valid_until",
]

MANDAAT_FIELDS = [
    "id",
    "person_id",
    "organization_id",
    "post_id",
    "role",
    "start_date",
    "end_date",
    "appointment_decision",
    "appointment_staatscourant_url",
    "appointment_kb_nummer",
    "confidence",
]

SOURCE_FIELDS = ["record_id", "source_id", "url", "retrieved", "fields"]


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


def _open_csv(path: Path, fieldnames: list[str]) -> tuple[Any, csv.DictWriter]:
    fh = path.open("w", encoding="utf-8", newline="")
    writer = csv.DictWriter(fh, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()
    return fh, writer


def _stringify(value: Any) -> Any:
    if value is None:
        return ""
    return value


def _row(fieldnames: list[str], data: dict[str, Any]) -> dict[str, Any]:
    return {k: _stringify(data.get(k)) for k in fieldnames}


def _write_organisaties(root: Path, out: Path, sources_writer: csv.DictWriter) -> None:
    fh, writer = _open_csv(out, ORG_FIELDS)
    try:
        for rec in _load_records(root):
            ids = rec.get("identifiers") or {}
            contact = rec.get("contact") or {}
            names = rec.get("names") or []
            primary_name: dict[str, Any] = names[0] if names else {}
            row = {
                "id": rec.get("id"),
                "type": rec.get("type"),
                "classification": rec.get("classification"),
                "parent_id": rec.get("parent_id"),
                "valid_from": rec.get("valid_from"),
                "valid_until": rec.get("valid_until"),
                "name": primary_name.get("value"),
                "abbr": primary_name.get("abbr"),
                "oin": ids.get("oin"),
                "tooi": ids.get("tooi"),
                "wikidata": ids.get("wikidata"),
                "roo_id": ids.get("roo_id"),
                "kvk": ids.get("kvk"),
                "rsin": ids.get("rsin"),
                "website": contact.get("website"),
                "bezoekadres": contact.get("bezoekadres"),
                "postadres": contact.get("postadres"),
                "email": contact.get("email"),
            }
            writer.writerow(_row(ORG_FIELDS, row))
            _write_sources(sources_writer, rec.get("id", ""), rec.get("sources"))
    finally:
        fh.close()


def _write_personen(root: Path, out: Path, sources_writer: csv.DictWriter) -> None:
    fh, writer = _open_csv(out, PERSOON_FIELDS)
    try:
        for rec in _load_records(root):
            name = rec.get("name") or {}
            birth = rec.get("birth") or {}
            ids = rec.get("identifiers") or {}
            row = {
                "id": rec.get("id"),
                "name_full": name.get("full"),
                "name_family": name.get("family"),
                "name_given": name.get("given"),
                "name_initials": name.get("initials"),
                "gender": rec.get("gender"),
                "birth_year": birth.get("year"),
                "wikidata": ids.get("wikidata"),
                "tk_persoon_id": ids.get("tk_persoon_id"),
                "abd_id": ids.get("abd_id"),
                "allmanak_id": ids.get("allmanak_id"),
            }
            writer.writerow(_row(PERSOON_FIELDS, row))
            _write_sources(sources_writer, rec.get("id", ""), rec.get("sources"))
    finally:
        fh.close()


def _write_posten(root: Path, out: Path) -> None:
    fh, writer = _open_csv(out, POST_FIELDS)
    try:
        for rec in _load_records(root):
            writer.writerow(_row(POST_FIELDS, rec))
    finally:
        fh.close()


def _write_mandaten(personen_root: Path, out: Path, sources_writer: csv.DictWriter) -> None:
    fh, writer = _open_csv(out, MANDAAT_FIELDS)
    try:
        for person in _load_records(personen_root):
            person_id = person.get("id")
            for mandaat in person.get("mandaten") or []:
                if not isinstance(mandaat, dict):
                    continue
                appointment = mandaat.get("appointment") or {}
                row = {
                    "id": mandaat.get("id"),
                    "person_id": person_id,
                    "organization_id": mandaat.get("organization_id"),
                    "post_id": mandaat.get("post_id"),
                    "role": mandaat.get("role"),
                    "start_date": mandaat.get("start_date"),
                    "end_date": mandaat.get("end_date"),
                    "appointment_decision": appointment.get("decision"),
                    "appointment_staatscourant_url": appointment.get("staatscourant_url"),
                    "appointment_kb_nummer": appointment.get("kb_nummer"),
                    "confidence": mandaat.get("confidence"),
                }
                writer.writerow(_row(MANDAAT_FIELDS, row))
                _write_sources(sources_writer, mandaat.get("id", ""), mandaat.get("sources"))
    finally:
        fh.close()


def _write_sources(
    writer: csv.DictWriter, record_id: str, sources: list[dict[str, Any]] | None
) -> None:
    if not sources:
        return
    for src in sources:
        if not isinstance(src, dict):
            continue
        writer.writerow(
            {
                "record_id": _stringify(record_id),
                "source_id": _stringify(src.get("id")),
                "url": _stringify(src.get("url")),
                "retrieved": _stringify(src.get("retrieved")),
                "fields": ",".join(src.get("fields") or []),
            }
        )


def build_csv(data_dir: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    sources_path = out_dir / "sources.csv"
    src_fh = sources_path.open("w", encoding="utf-8", newline="")
    sources_writer = csv.DictWriter(src_fh, fieldnames=SOURCE_FIELDS, quoting=csv.QUOTE_MINIMAL)
    sources_writer.writeheader()
    try:
        _write_organisaties(data_dir / "organisaties", out_dir / "organisaties.csv", sources_writer)
        _write_personen(data_dir / "personen", out_dir / "personen.csv", sources_writer)
        _write_posten(data_dir / "posten", out_dir / "posten.csv")
        _write_mandaten(data_dir / "personen", out_dir / "mandaten.csv", sources_writer)
    finally:
        src_fh.close()
