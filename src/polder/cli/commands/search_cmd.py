"""`polder search <query>` command.

Vrije-tekst zoek over alle entity-types in `data/`. Backend is ripgrep (`rg`)
voor snelheid (~50x sneller dan pure Python op 4724+ files). Valt automatisch
terug op een Python-implementatie als rg ontbreekt.

Voor eenvoudige queries (substring of regex) is dit een grep-with-context.
Voor cross-field of structured queries: gebruik `polder serve` (datasette).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from polder.lib import Polder


def _resolve_root(data: Path | None) -> Path:
    if data is not None:
        return data.resolve()
    cwd = Path.cwd()
    if (cwd / "data").exists():
        return cwd
    raise typer.BadParameter(
        f"Geen data/ in {cwd}. Gebruik --data om een polder-root op te geven."
    )


def _entity_label_from_path(rel_path: str) -> str:
    """Map relatieve pad in data/ naar entity-label."""
    if rel_path.startswith("organisaties/"):
        return "org"
    if rel_path.startswith("personen/"):
        return "person"
    if rel_path.startswith("posten/"):
        return "post"
    return "?"


def _entity_dir_for_type(type_: str | None) -> list[str]:
    """Welke data/-subfolders moet rg doorzoeken voor de gevraagde --type?"""
    if type_ == "org":
        return ["organisaties"]
    if type_ == "person":
        return ["personen"]
    if type_ == "post":
        return ["posten"]
    return ["organisaties", "personen", "posten"]


def _build_rg_pattern(query: str, regex: bool, field: str | None) -> str:
    """Bouw een rg-regex voor (optioneel) field-restricted zoeken.

    Voor `--field name.family` matchen we yaml-regels waar de leaf-key
    de gevraagde naam heeft EN de waarde de query bevat (substring, niet
    prefix). Voorbeeld: query="Dijk" + field="family" matched ook
    "van Dijk" en "Akkie Wegman - van Dijk".

    Zonder `--field`: matchen we de query **in de waarde-positie** van een
    yaml-regel. Dat wil zeggen, na `key: `. Daardoor werkt `^org:min-`
    met `--regex` zoals een gebruiker verwacht: het anchort op de waarde
    direct na `key:`, niet op het regel-begin (dat zou nooit matchen want
    yaml-keys hebben altijd indentatie).
    """
    body = query if regex else re.escape(query)
    if regex and body.startswith("^"):
        # Regex met `^` bedoelt "start van de waarde", niet "start van de
        # regel". Strip de gebruiker's `^` en plak onze value-anchor erop.
        return rf"(?m)^\s*\S+:\s+['\"]?{body[1:]}"
    if field is None:
        # Substring of regex zonder ^-anchor: match ergens in de waarde
        # van een yaml-regel. `(?m)^\s*\S+:\s.*body` matched dus `key: ...body...`.
        return rf"(?m)^\s*\S+:\s.*{body}"
    leaf = field.rsplit(".", 1)[-1]
    leaf_escaped = re.escape(leaf)
    return rf"(?m)^\s*{leaf_escaped}:\s.*{body}"


def _id_from_yaml_file(path: Path) -> str | None:
    """Pluk `id:` uit de eerste paar regels van een YAML-bestand.

    Vermijdt het laden van het hele bestand via Pydantic.
    """
    try:
        with path.open("r", encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if i > 20:
                    break
                stripped = line.lstrip()
                if stripped.startswith("id:"):
                    return stripped[3:].strip().strip("'\"")
    except OSError:
        return None
    return None


def _matched_field_path(match_line: str) -> str | None:
    """Best-effort: uit een rg-match-line de yaml-key halen."""
    m = re.match(r"\s*([a-zA-Z_][a-zA-Z0-9_]*):", match_line)
    return m.group(1) if m else None


def _truncate(text: str, max_len: int = 80) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _search_with_rg(
    root: Path,
    pattern: str,
    type_: str | None,
    case_sensitive: bool,
    limit: int,
) -> list[tuple[str, str, str, str]]:
    """Run rg over data/, retourneer lijst van (entity_type, id, field, value).

    Resultaten worden per-file gegroepeerd: één rij per file, met het eerste
    veld dat gematcht heeft. Aantal extra velden komt in een vervolgrij in
    de aanroeper.
    """
    rg = shutil.which("rg")
    if rg is None:
        return []

    subdirs = _entity_dir_for_type(type_)
    rg_paths = [str(root / "data" / sd) for sd in subdirs if (root / "data" / sd).exists()]
    if not rg_paths:
        return []

    cmd = [rg, "--json", "-t", "yaml"]
    if not case_sensitive:
        cmd.append("-i")
    cmd.extend(["--max-count", str(max(limit * 3, 50))])
    cmd.append(pattern)
    cmd.extend(rg_paths)

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode not in (0, 1):
        # 1 = no matches, normaal. Anders is iets stuk.
        raise RuntimeError(f"rg-fout (exit={proc.returncode}): {proc.stderr[:200]}")

    # Groepeer per file
    by_file: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        path = (event.get("data", {}).get("path") or {}).get("text", "")
        match_line = (event.get("data", {}).get("lines") or {}).get("text", "")
        if path:
            by_file.setdefault(path, []).append(match_line.rstrip("\n"))

    results: list[tuple[str, str, str, str]] = []
    for path_str, lines in by_file.items():
        path = Path(path_str)
        try:
            rel = path.relative_to(root / "data")
        except ValueError:
            rel = path
        entity = _entity_label_from_path(str(rel))
        record_id = _id_from_yaml_file(path) or path.name
        first_line = lines[0]
        field = _matched_field_path(first_line) or "?"
        # Knip `key: ` prefix uit value
        value = first_line.split(":", 1)[1].strip().strip("'\"") if ":" in first_line else first_line
        results.append((entity, record_id, field, value))
        if len(lines) > 1:
            results.append(("", "", f"+{len(lines) - 1} velden", ""))
        if sum(1 for r in results if r[1]) >= limit:
            break
    return results


# ---------------------------------------------------------------------------
# Python-fallback (gebruikt als rg ontbreekt)
# ---------------------------------------------------------------------------


def _flatten(value: Any, prefix: str = "") -> Iterable[tuple[str, str]]:
    if value is None:
        return
    if isinstance(value, str):
        yield prefix, value
        return
    if isinstance(value, (int, float, bool)):
        yield prefix, str(value)
        return
    if isinstance(value, dict):
        for k, v in value.items():
            sub = f"{prefix}.{k}" if prefix else str(k)
            yield from _flatten(v, sub)
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            sub = f"{prefix}[{i}]"
            yield from _flatten(item, sub)


def _record_to_dict(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", exclude_none=True)
    if isinstance(obj, dict):
        return obj
    return {}


def _matches(
    data: dict[str, Any],
    matcher: re.Pattern[str],
    field_filter: str | None,
) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for field, value in _flatten(data):
        if field_filter is not None and not field.startswith(field_filter):
            continue
        if matcher.search(value):
            hits.append((field, value))
    return hits


def _search_with_python(
    root: Path,
    matcher: re.Pattern[str],
    type_: str | None,
    field: str | None,
    limit: int,
) -> list[tuple[str, str, str, str]]:
    polder = Polder.local(root)
    sources: list[tuple[str, Iterable[Any]]] = []
    if type_ in (None, "org"):
        sources.append(("org", polder.organisaties.all()))
    if type_ in (None, "person"):
        sources.append(("person", polder.personen.all()))
    if type_ in (None, "post"):
        sources.append(("post", polder.posten.all()))

    results: list[tuple[str, str, str, str]] = []
    n = 0
    for entity_label, records in sources:
        for obj in records:
            if n >= limit:
                break
            d = _record_to_dict(obj)
            hits = _matches(d, matcher, field)
            if not hits:
                continue
            obj_id = d.get("id", "?")
            first_field, first_value = hits[0]
            results.append((entity_label, obj_id, first_field, _truncate(first_value)))
            extra = len(hits) - 1
            if extra > 0:
                results.append(("", "", f"+{extra} velden", ""))
            n += 1
        if n >= limit:
            break
    return results


def search(
    query: Annotated[str, typer.Argument(help="Zoekterm (substring of regex met --regex).")],
    type_: Annotated[
        str | None,
        typer.Option(
            "--type",
            "-t",
            help="Beperk tot entity-type: org | person | post.",
        ),
    ] = None,
    field: Annotated[
        str | None,
        typer.Option(
            "--field",
            "-f",
            help="Beperk tot één veld (yaml-leaf-key, bv. `name.family` of `family`).",
        ),
    ] = None,
    regex: Annotated[
        bool,
        typer.Option("--regex", help="Behandel query als regex i.p.v. substring."),
    ] = False,
    case_sensitive: Annotated[
        bool,
        typer.Option("--case-sensitive", help="Case-sensitive match."),
    ] = False,
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Max aantal resultaten."),
    ] = 50,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Structured JSON output ipv tabel (voor scripting/skills)."),
    ] = False,
    backend: Annotated[
        str,
        typer.Option("--backend", help="Search backend: 'auto' (default), 'rg', of 'python'."),
    ] = "auto",
    data: Annotated[Path | None, typer.Option("--data", help="Polder root.")] = None,
) -> None:
    """Vrije-tekst zoek over alle entity-types in `data/`.

    Default backend is ripgrep (~50x sneller dan Python). Met --backend python
    forceer je de oude Pydantic-loop (langzaam, maar volledig structureel).

    Voorbeelden:

      polder search rutte
      polder search Dijk -t person -f name.family
      polder search "^org:min-" --regex
      polder search Klaverblad --json | jq '.results[0].id'
    """
    if type_ is not None and type_ not in ("org", "person", "post"):
        raise typer.BadParameter("--type moet 'org', 'person' of 'post' zijn.")
    if backend not in ("auto", "rg", "python"):
        raise typer.BadParameter("--backend moet 'auto', 'rg' of 'python' zijn.")

    root = _resolve_root(data)

    # Backend-keuze
    use_rg = backend == "rg" or (backend == "auto" and shutil.which("rg") is not None)

    try:
        if use_rg:
            pattern = _build_rg_pattern(query, regex, field)
            results = _search_with_rg(root, pattern, type_, case_sensitive, limit)
        else:
            pattern_text = query if regex else re.escape(query)
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                matcher = re.compile(pattern_text, flags)
            except re.error as exc:
                raise typer.BadParameter(f"Ongeldige regex: {exc}") from exc
            results = _search_with_python(root, matcher, type_, field, limit)
    except RuntimeError as exc:
        typer.echo(f"Search-fout: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    if json_output:
        payload = {
            "query": query,
            "type": type_,
            "field": field,
            "regex": regex,
            "backend": "rg" if use_rg else "python",
            "results": [
                {"entity_type": e, "id": i, "field": f, "value": v}
                for e, i, f, v in results
                if i  # filter out "+N velden" continuation rows
            ],
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        if not payload["results"]:
            raise typer.Exit(code=1)
        return

    if not results:
        typer.echo(f"Geen resultaten voor: {query}")
        raise typer.Exit(code=1)

    console = Console()
    table = Table(show_header=True, header_style="bold")
    table.add_column("type")
    table.add_column("id")
    table.add_column("veld")
    table.add_column("waarde")
    for entity_label, record_id, field_name, value in results:
        table.add_row(entity_label, record_id, field_name, _truncate(value))

    console.print(table)
    n_records = sum(1 for r in results if r[1])
    if n_records >= limit:
        console.print(f"[dim](limiet bereikt op {limit}; gebruik --limit voor meer)[/dim]")
