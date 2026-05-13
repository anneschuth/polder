"""Bouw `dist/polder.db` SQLite uit YAML in `data/`."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
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


def create_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS organisaties (
            id           TEXT PRIMARY KEY,
            type         TEXT NOT NULL,
            classification TEXT,
            parent_id    TEXT,
            valid_from   TEXT,
            valid_until  TEXT,
            identifiers  TEXT,
            names        TEXT,
            contact      TEXT
        );

        CREATE TABLE IF NOT EXISTS personen (
            id           TEXT PRIMARY KEY,
            name_full    TEXT,
            name_family  TEXT,
            name_given   TEXT,
            gender       TEXT,
            birth_year   INTEGER,
            identifiers  TEXT
        );

        CREATE TABLE IF NOT EXISTS posten (
            id              TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            label           TEXT,
            classification  TEXT,
            seat_count      INTEGER,
            valid_from      TEXT,
            valid_until     TEXT,
            FOREIGN KEY(organization_id) REFERENCES organisaties(id)
        );

        CREATE TABLE IF NOT EXISTS mandaten (
            id              TEXT PRIMARY KEY,
            person_id       TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            post_id         TEXT NOT NULL,
            role            TEXT,
            start_date      TEXT,
            end_date        TEXT,
            FOREIGN KEY(person_id) REFERENCES personen(id),
            FOREIGN KEY(organization_id) REFERENCES organisaties(id),
            FOREIGN KEY(post_id) REFERENCES posten(id)
        );

        CREATE TABLE IF NOT EXISTS sources (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id   TEXT NOT NULL,
            source_id   TEXT,
            url         TEXT,
            retrieved   TEXT
        );

        CREATE INDEX IF NOT EXISTS ix_posten_org ON posten(organization_id);
        CREATE INDEX IF NOT EXISTS ix_posten_valid_from ON posten(valid_from);
        CREATE INDEX IF NOT EXISTS ix_posten_valid_until ON posten(valid_until);
        CREATE INDEX IF NOT EXISTS ix_mandaten_person ON mandaten(person_id);
        CREATE INDEX IF NOT EXISTS ix_mandaten_post ON mandaten(post_id);
        CREATE INDEX IF NOT EXISTS ix_mandaten_org ON mandaten(organization_id);
        CREATE INDEX IF NOT EXISTS ix_mandaten_start ON mandaten(start_date);
        CREATE INDEX IF NOT EXISTS ix_mandaten_end ON mandaten(end_date);
        CREATE INDEX IF NOT EXISTS ix_organisaties_valid_from ON organisaties(valid_from);
        CREATE INDEX IF NOT EXISTS ix_organisaties_valid_until ON organisaties(valid_until);
        CREATE INDEX IF NOT EXISTS ix_sources_record ON sources(record_id);
        """
    )
    conn.commit()


def _insert_sources(
    cur: sqlite3.Cursor, record_id: str, sources: Iterable[dict[str, Any]] | None
) -> None:
    if not sources:
        return
    rows = [
        (record_id, s.get("id"), s.get("url"), s.get("retrieved"))
        for s in sources
        if isinstance(s, dict)
    ]
    if rows:
        cur.executemany(
            "INSERT INTO sources (record_id, source_id, url, retrieved) VALUES (?, ?, ?, ?)",
            rows,
        )


def populate_organisaties(conn: sqlite3.Connection, root: Path) -> int:
    count = 0
    cur = conn.cursor()
    for rec in _load_records(root):
        cur.execute(
            """
            INSERT OR REPLACE INTO organisaties
            (id, type, classification, parent_id, valid_from, valid_until,
             identifiers, names, contact)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec.get("id"),
                rec.get("type"),
                rec.get("classification"),
                rec.get("parent_id"),
                rec.get("valid_from"),
                rec.get("valid_until"),
                json.dumps(rec.get("identifiers"), ensure_ascii=False)
                if rec.get("identifiers") is not None
                else None,
                json.dumps(rec.get("names"), ensure_ascii=False, default=str)
                if rec.get("names") is not None
                else None,
                json.dumps(rec.get("contact"), ensure_ascii=False)
                if rec.get("contact") is not None
                else None,
            ),
        )
        _insert_sources(cur, rec.get("id", ""), rec.get("sources"))
        count += 1
    return count


def populate_personen(conn: sqlite3.Connection, root: Path) -> int:
    count = 0
    cur = conn.cursor()
    for rec in _load_records(root):
        name = rec.get("name") or {}
        birth = rec.get("birth") or {}
        cur.execute(
            """
            INSERT OR REPLACE INTO personen
            (id, name_full, name_family, name_given, gender, birth_year, identifiers)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec.get("id"),
                name.get("full"),
                name.get("family"),
                name.get("given"),
                rec.get("gender"),
                birth.get("year"),
                json.dumps(rec.get("identifiers"), ensure_ascii=False)
                if rec.get("identifiers") is not None
                else None,
            ),
        )
        _insert_sources(cur, rec.get("id", ""), rec.get("sources"))
        count += 1
    return count


def populate_posten(conn: sqlite3.Connection, root: Path) -> int:
    count = 0
    cur = conn.cursor()
    for rec in _load_records(root):
        cur.execute(
            """
            INSERT OR REPLACE INTO posten
            (id, organization_id, label, classification, seat_count, valid_from, valid_until)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec.get("id"),
                rec.get("organization_id"),
                rec.get("label"),
                rec.get("classification"),
                rec.get("seat_count"),
                rec.get("valid_from"),
                rec.get("valid_until"),
            ),
        )
        count += 1
    return count


def populate_mandaten(conn: sqlite3.Connection, personen_root: Path) -> int:
    """Mandaten staan inline in persoon-records."""
    count = 0
    cur = conn.cursor()
    for person in _load_records(personen_root):
        person_id = person.get("id")
        for mandaat in person.get("mandaten") or []:
            if not isinstance(mandaat, dict):
                continue
            cur.execute(
                """
                INSERT OR REPLACE INTO mandaten
                (id, person_id, organization_id, post_id, role, start_date, end_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mandaat.get("id"),
                    person_id,
                    mandaat.get("organization_id"),
                    mandaat.get("post_id"),
                    mandaat.get("role"),
                    mandaat.get("start_date"),
                    mandaat.get("end_date"),
                ),
            )
            _insert_sources(cur, mandaat.get("id", ""), mandaat.get("sources"))
            count += 1
    return count


def build_sqlite(data_dir: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    conn = sqlite3.connect(out)
    try:
        create_schema(conn)
        populate_organisaties(conn, data_dir / "organisaties")
        populate_personen(conn, data_dir / "personen")
        populate_posten(conn, data_dir / "posten")
        populate_mandaten(conn, data_dir / "personen")
        conn.commit()
    finally:
        conn.close()
