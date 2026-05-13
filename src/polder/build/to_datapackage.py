"""Genereer Frictionless Data Package descriptor voor `dist/`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PACKAGE_NAME = "polder"
PACKAGE_TITLE = "Polder"
PACKAGE_DESCRIPTION = (
    "Git-versioned, CC0-gelicenseerde dataset van Nederlandse overheidsorganisaties, "
    "posten, personen en mandaten."
)
PACKAGE_VERSION = "0.0.1"
PACKAGE_HOMEPAGE = "https://polder.dev/"

LICENSES = [
    {
        "name": "CC0-1.0",
        "path": "https://creativecommons.org/publicdomain/zero/1.0/",
        "title": "Creative Commons Zero v1.0 Universal",
    }
]

CONTRIBUTORS = [
    {
        "title": "Anne Schuth",
        "email": "anne.schuth@gmail.com",
        "role": "author",
    }
]

SOURCES = [
    {
        "title": "Register Overheidsorganisaties (ROO)",
        "path": "https://organisaties.overheid.nl/",
    },
    {
        "title": "Tweede Kamer OData",
        "path": "https://opendata.tweedekamer.nl/",
    },
    {
        "title": "Logius Community of Origin Register (TOOI/COR)",
        "path": "https://standaarden.overheid.nl/tooi",
    },
    {
        "title": "KOOP Officiele Bekendmakingen",
        "path": "https://zoek.officielebekendmakingen.nl/",
    },
]

KEYWORDS = ["overheid", "open-data", "nederland", "popolo", "yaml", "ambtenaren", "mandaten"]


def _organisaties_resource(csv_dir: Path) -> dict[str, Any]:
    return {
        "name": "organisaties",
        "path": str((csv_dir / "organisaties.csv").as_posix()),
        "format": "csv",
        "mediatype": "text/csv",
        "encoding": "utf-8",
        "schema": {
            "primaryKey": "id",
            "fields": [
                {"name": "id", "type": "string", "constraints": {"required": True}},
                {"name": "type", "type": "string"},
                {"name": "classification", "type": "string"},
                {"name": "parent_id", "type": "string"},
                {"name": "valid_from", "type": "date"},
                {"name": "valid_until", "type": "date"},
                {"name": "name", "type": "string"},
                {"name": "abbr", "type": "string"},
                {"name": "oin", "type": "string"},
                {"name": "tooi", "type": "string", "format": "uri"},
                {"name": "wikidata", "type": "string"},
                {"name": "roo_id", "type": "string"},
                {"name": "kvk", "type": "string"},
                {"name": "rsin", "type": "string"},
                {"name": "website", "type": "string", "format": "uri"},
                {"name": "bezoekadres", "type": "string"},
                {"name": "postadres", "type": "string"},
                {"name": "email", "type": "string", "format": "email"},
            ],
        },
    }


def _personen_resource(csv_dir: Path) -> dict[str, Any]:
    return {
        "name": "personen",
        "path": str((csv_dir / "personen.csv").as_posix()),
        "format": "csv",
        "mediatype": "text/csv",
        "encoding": "utf-8",
        "schema": {
            "primaryKey": "id",
            "fields": [
                {"name": "id", "type": "string", "constraints": {"required": True}},
                {"name": "name_full", "type": "string"},
                {"name": "name_family", "type": "string"},
                {"name": "name_given", "type": "string"},
                {"name": "name_initials", "type": "string"},
                {"name": "gender", "type": "string"},
                {"name": "birth_year", "type": "integer"},
                {"name": "wikidata", "type": "string"},
                {"name": "tk_persoon_id", "type": "string"},
                {"name": "abd_id", "type": "string"},
                {"name": "allmanak_id", "type": "string"},
            ],
        },
    }


def _posten_resource(csv_dir: Path) -> dict[str, Any]:
    return {
        "name": "posten",
        "path": str((csv_dir / "posten.csv").as_posix()),
        "format": "csv",
        "mediatype": "text/csv",
        "encoding": "utf-8",
        "schema": {
            "primaryKey": "id",
            "foreignKeys": [
                {
                    "fields": "organization_id",
                    "reference": {"resource": "organisaties", "fields": "id"},
                }
            ],
            "fields": [
                {"name": "id", "type": "string", "constraints": {"required": True}},
                {"name": "organization_id", "type": "string", "constraints": {"required": True}},
                {"name": "label", "type": "string"},
                {"name": "classification", "type": "string"},
                {"name": "seat_count", "type": "integer"},
                {"name": "valid_from", "type": "date"},
                {"name": "valid_until", "type": "date"},
            ],
        },
    }


def _mandaten_resource(csv_dir: Path) -> dict[str, Any]:
    return {
        "name": "mandaten",
        "path": str((csv_dir / "mandaten.csv").as_posix()),
        "format": "csv",
        "mediatype": "text/csv",
        "encoding": "utf-8",
        "schema": {
            "primaryKey": "id",
            "foreignKeys": [
                {
                    "fields": "person_id",
                    "reference": {"resource": "personen", "fields": "id"},
                },
                {
                    "fields": "organization_id",
                    "reference": {"resource": "organisaties", "fields": "id"},
                },
                {
                    "fields": "post_id",
                    "reference": {"resource": "posten", "fields": "id"},
                },
            ],
            "fields": [
                {"name": "id", "type": "string", "constraints": {"required": True}},
                {"name": "person_id", "type": "string", "constraints": {"required": True}},
                {"name": "organization_id", "type": "string", "constraints": {"required": True}},
                {"name": "post_id", "type": "string", "constraints": {"required": True}},
                {"name": "role", "type": "string"},
                {"name": "start_date", "type": "date"},
                {"name": "end_date", "type": "date"},
                {"name": "appointment_decision", "type": "string"},
                {"name": "appointment_staatscourant_url", "type": "string", "format": "uri"},
                {"name": "appointment_kb_nummer", "type": "string"},
                {"name": "confidence", "type": "number"},
            ],
        },
    }


def _sources_resource(csv_dir: Path) -> dict[str, Any]:
    return {
        "name": "sources",
        "path": str((csv_dir / "sources.csv").as_posix()),
        "format": "csv",
        "mediatype": "text/csv",
        "encoding": "utf-8",
        "schema": {
            "fields": [
                {"name": "record_id", "type": "string"},
                {"name": "source_id", "type": "string"},
                {"name": "url", "type": "string", "format": "uri"},
                {"name": "retrieved", "type": "date"},
                {"name": "fields", "type": "string"},
            ]
        },
    }


def build_datapackage(data_dir: Path, csv_dir: Path, out: Path) -> None:
    """Schrijf datapackage.json descriptor met paden relatief aan `out.parent`."""
    out.parent.mkdir(parents=True, exist_ok=True)
    base = out.parent

    def _rel(target: Path) -> Path:
        try:
            return Path(target.relative_to(base))
        except ValueError:
            return target

    rel_csv = _rel(csv_dir)

    descriptor: dict[str, Any] = {
        "profile": "data-package",
        "name": PACKAGE_NAME,
        "title": PACKAGE_TITLE,
        "description": PACKAGE_DESCRIPTION,
        "version": PACKAGE_VERSION,
        "homepage": PACKAGE_HOMEPAGE,
        "licenses": LICENSES,
        "contributors": CONTRIBUTORS,
        "sources": SOURCES,
        "keywords": KEYWORDS,
        "resources": [
            _organisaties_resource(rel_csv),
            _personen_resource(rel_csv),
            _posten_resource(rel_csv),
            _mandaten_resource(rel_csv),
            _sources_resource(rel_csv),
        ],
    }
    with out.open("w", encoding="utf-8") as fh:
        json.dump(descriptor, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    # data_dir parameter blijft beschikbaar voor toekomstige verrijking
    _ = data_dir
