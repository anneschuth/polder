"""`polder merge` command: merge een duplicate org/post/person naar canonical.

Use case: de resolver of apply-staging maakte twee records voor dezelfde
entiteit (bv `org:onderdeel-aivd-min-bzk` en `org:onderdeel-aivd`).
Een merge:
  1. Vervangt alle references naar het duplicate-id door het canonical-id
     in personen/posten/organisaties (string-replace op YAML-tekst).
  2. Voor org-merge: mandaten in personen krijgen de canonical organization_id.
  3. Voor person-merge: mandaten + sources van het duplicate worden
     samengevoegd in het canonical record. Geen verlies van data.
  4. Verwijdert het duplicate-record-file.

Default is dry-run; pas met `--apply` echt aan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import yaml

app = typer.Typer(help="Merge een duplicate org/post/person naar canonical.")


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _find_record_file(data_dir: Path, kind: str, record_id: str) -> Path | None:
    """Zoek het YAML-bestand met `id: <record_id>` in de juiste subdir.

    Geen path-conventie aanname — we scannen omdat slugs willekeurig kunnen
    zijn (`org:foo` kan in `agentschappen/foo.yaml` of `organisatieonderdelen/`
    staan).
    """
    if kind == "org":
        root = data_dir / "organisaties"
    elif kind == "post":
        root = data_dir / "posten"
    elif kind == "person":
        root = data_dir / "personen"
    else:
        raise ValueError(f"Onbekende kind: {kind!r}")

    if not root.exists():
        return None

    for path in root.rglob("*.yaml"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # Snelle pre-filter voor we YAML parsen.
        if record_id not in text:
            continue
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            continue
        if isinstance(data, dict) and data.get("id") == record_id:
            return path
    return None


# ---------------------------------------------------------------------------
# Reference scanning + remap
# ---------------------------------------------------------------------------


def _scan_references(data_dir: Path, record_id: str) -> dict[str, list[Path]]:
    """Vind alle YAML-files waarin record_id als string voorkomt.

    Groepeert per subdir (organisaties/personen/posten) zodat de caller
    weet waar de impact zit.
    """
    refs: dict[str, list[Path]] = {"organisaties": [], "personen": [], "posten": []}
    for subdir in refs:
        root = data_dir / subdir
        if not root.exists():
            continue
        for path in root.rglob("*.yaml"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if record_id in text:
                refs[subdir].append(path)
    return refs


def _apply_string_remap(paths: list[Path], old: str, new: str) -> int:
    """Vervang alle voorkomens van `old` door `new` in de gegeven paden.

    Returns het aantal bestanden dat daadwerkelijk is gewijzigd.
    """
    changed = 0
    for path in paths:
        text = path.read_text(encoding="utf-8")
        new_text = text.replace(old, new)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            changed += 1
    return changed


# ---------------------------------------------------------------------------
# Person merge: combineer mandaten + sources
# ---------------------------------------------------------------------------


def _merge_org_records(dup: dict, canonical: dict) -> dict:
    """Consolideer identifiers + sources van `dup` in `canonical`.

    Een dup-org draagt soms de stabiele externe identifier (ROO-import:
    `roo_id`, `tooi`, `oin`, `organisatiecode`) terwijl het canonical-record
    die mist, of andersom. Bij een blinde delete van het dup-file zou die
    identifier verloren gaan en kan een latere ROO-fetch het record niet
    meer terugkoppelen. We vullen alleen ontbrekende identifier-keys aan
    (canonical wint bij conflict, want dat is per definitie het record dat
    we behouden) en voegen nieuwe bron-URLs toe.
    """
    dup_idents = dup.get("identifiers") or {}
    if dup_idents:
        merged_idents = dict(canonical.get("identifiers") or {})
        for key, value in dup_idents.items():
            merged_idents.setdefault(key, value)
        canonical["identifiers"] = merged_idents

    sources = list(canonical.get("sources") or [])
    existing_source_urls = {s.get("url") for s in sources if isinstance(s, dict) and s.get("url")}
    for s in dup.get("sources") or []:
        if isinstance(s, dict) and s.get("url") not in existing_source_urls:
            sources.append(s)
            existing_source_urls.add(s.get("url"))
    if sources:
        canonical["sources"] = sources
    return canonical


def _merge_person_records(dup: dict, canonical: dict) -> dict:
    """Voeg mandaten en sources van `dup` toe aan `canonical` zonder duplicaten."""
    mandaten = list(canonical.get("mandaten") or [])
    existing_mandate_ids = {m.get("id") for m in mandaten if isinstance(m, dict) and m.get("id")}
    for m in dup.get("mandaten") or []:
        if isinstance(m, dict) and m.get("id") not in existing_mandate_ids:
            mandaten.append(m)
    canonical["mandaten"] = mandaten

    sources = list(canonical.get("sources") or [])
    existing_source_urls = {s.get("url") for s in sources if isinstance(s, dict) and s.get("url")}
    for s in dup.get("sources") or []:
        if isinstance(s, dict) and s.get("url") not in existing_source_urls:
            sources.append(s)
    canonical["sources"] = sources
    return canonical


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def _merge_generic(
    *,
    kind: str,
    dup_id: str,
    canonical_id: str,
    data_dir: Path,
    apply: bool,
) -> None:
    """Generieke merge-flow voor alle drie de entity-types.

    Voor person doet de caller de YAML-merge na de string-remap (omdat we
    mandaten/sources willen samenvoegen, niet alleen het ID-veld).
    """
    if dup_id == canonical_id:
        typer.echo(f"merge: dup-id en canonical-id zijn gelijk ({dup_id})", err=True)
        raise typer.Exit(code=2)

    dup_path = _find_record_file(data_dir, kind, dup_id)
    canonical_path = _find_record_file(data_dir, kind, canonical_id)

    if dup_path is None:
        typer.echo(f"merge: dup-record {dup_id!r} niet gevonden in data/{kind}/", err=True)
        raise typer.Exit(code=2)
    if canonical_path is None:
        typer.echo(
            f"merge: canonical-record {canonical_id!r} niet gevonden in data/{kind}/", err=True
        )
        raise typer.Exit(code=2)

    refs = _scan_references(data_dir, dup_id)
    total_refs = sum(len(v) for v in refs.values())

    typer.echo(f"Merge plan: {dup_id} -> {canonical_id}")
    typer.echo(f"  dup-file        : {dup_path.relative_to(data_dir)}")
    typer.echo(f"  canonical-file  : {canonical_path.relative_to(data_dir)}")
    typer.echo(f"  references      : {total_refs} files")
    for subdir, paths in refs.items():
        if paths:
            typer.echo(f"    {subdir}: {len(paths)}")

    if not apply:
        typer.echo("\nDry-run. Run met --apply om de merge uit te voeren.")
        return

    # 1. Voor person/org: consolideer waardevolle velden van het dup-record
    #    in het canonical record voordat we het dup-file deleten.
    if kind in ("person", "org"):
        dup_data = yaml.safe_load(dup_path.read_text(encoding="utf-8"))
        canonical_data = yaml.safe_load(canonical_path.read_text(encoding="utf-8"))
        if kind == "person":
            merged = _merge_person_records(dup_data, canonical_data)
        else:
            merged = _merge_org_records(dup_data, canonical_data)
        canonical_path.write_text(
            yaml.safe_dump(merged, allow_unicode=True, sort_keys=False, width=1000),
            encoding="utf-8",
        )

    # 2. String-remap in alle referencing files (exclusief het dup-file zelf).
    remap_paths: list[Path] = []
    for paths in refs.values():
        for p in paths:
            if p.resolve() != dup_path.resolve():
                remap_paths.append(p)
    changed = _apply_string_remap(remap_paths, dup_id, canonical_id)
    typer.echo(f"  remap'd in {changed} files")

    # 3. Verwijder het dup-file.
    dup_path.unlink()
    typer.echo(f"  deleted: {dup_path.relative_to(data_dir)}")


@app.command("org")
def merge_org(
    dup_id: Annotated[
        str, typer.Argument(help="Duplicate org-id (bv 'org:onderdeel-aivd-min-bzk').")
    ],
    canonical_id: Annotated[
        str, typer.Argument(help="Canonical org-id (bv 'org:onderdeel-aivd').")
    ],
    apply: Annotated[
        bool, typer.Option("--apply", help="Schrijf echt. Default is dry-run.")
    ] = False,
    data: Annotated[Path, typer.Option("--data", help="Polder data root.")] = Path("data"),
) -> None:
    """Merge een duplicate organisatie naar canonical.

    Alle mandaten/posten die `dup_id` als organization_id of parent_id
    hadden krijgen `canonical_id`. Het dup-file wordt verwijderd.
    """
    if not dup_id.startswith("org:") or not canonical_id.startswith("org:"):
        typer.echo("merge org: beide ids moeten beginnen met 'org:'", err=True)
        raise typer.Exit(code=2)
    _merge_generic(kind="org", dup_id=dup_id, canonical_id=canonical_id, data_dir=data, apply=apply)


@app.command("post")
def merge_post(
    dup_id: Annotated[str, typer.Argument(help="Duplicate post-id.")],
    canonical_id: Annotated[str, typer.Argument(help="Canonical post-id.")],
    apply: Annotated[
        bool, typer.Option("--apply", help="Schrijf echt. Default is dry-run.")
    ] = False,
    data: Annotated[Path, typer.Option("--data", help="Polder data root.")] = Path("data"),
) -> None:
    """Merge een duplicate post naar canonical.

    Alle mandaten die `dup_id` als post_id hadden krijgen `canonical_id`.
    Het dup-file wordt verwijderd.
    """
    if not dup_id.startswith("post:") or not canonical_id.startswith("post:"):
        typer.echo("merge post: beide ids moeten beginnen met 'post:'", err=True)
        raise typer.Exit(code=2)
    _merge_generic(
        kind="post", dup_id=dup_id, canonical_id=canonical_id, data_dir=data, apply=apply
    )


@app.command("person")
def merge_person(
    dup_id: Annotated[str, typer.Argument(help="Duplicate person-id.")],
    canonical_id: Annotated[str, typer.Argument(help="Canonical person-id.")],
    apply: Annotated[
        bool, typer.Option("--apply", help="Schrijf echt. Default is dry-run.")
    ] = False,
    data: Annotated[Path, typer.Option("--data", help="Polder data root.")] = Path("data"),
) -> None:
    """Merge een duplicate persoon naar canonical.

    Anders dan org/post: het dup-record bevat mandaten en sources die
    waardevol zijn. Die worden samengevoegd in het canonical record
    (op id-uniqueness voor mandaten, op url-uniqueness voor sources).
    Daarna wordt het dup-file verwijderd.
    """
    if not dup_id.startswith("person:") or not canonical_id.startswith("person:"):
        typer.echo("merge person: beide ids moeten beginnen met 'person:'", err=True)
        raise typer.Exit(code=2)
    _merge_generic(
        kind="person", dup_id=dup_id, canonical_id=canonical_id, data_dir=data, apply=apply
    )
