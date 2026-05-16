"""Deterministic resolver voor ROO functie/medewerker-staging-proposals.

Twee auto-merge lanes (additief, geen nieuwe feiten — daarom binnen
de project-governance toegestaan voor code):

1. **Post enrichment** (confidence ≥ 0.95):
   Bestaande post + ROO functie matcht (org+naam) → voeg
   `roo_functie_id`/`roo_naam` toe aan post-yaml. Geen nieuwe records;
   puur administratieve metadata waarvoor ROO de registry-autoriteit is.

2. **Mandaat bevestiging** (confidence ≥ 0.95):
   Bestaande person heeft een open mandaat op die post → voeg een ROO
   `sources[]`-entry toe. Two-source rule blijft intact: het mandaat
   bestond al uit Staatscourant/ABD/ORI (bron 1), ROO is bron 2. Geen
   nieuw benoemingsfeit.

Wat NOOIT auto-gemerged wordt (gaat naar
`data/_staging/<input-stem>.unresolved.json` voor de
human-on-the-loop / `resolve-staging-proposals`-skill):
- **Nieuwe benoeming** (persoon+post matchen maar geen mandaat). Een
  mandaat-creation met ROO als enige bron schendt de two-source rule
  (CLAUDE.md: Staatscourant + ≥1 andere bron, of Stcrt ≥ 0.98). Wordt
  een proposal met confidence 0.7 + confidence_reasoning.
- Nieuwe person creation (parsing van `mw. drs. M. (Mirjam) van Leeuwen`
  is broos).
- Ambiguous person matches (≥ 2 personen delen family+initials).
- Nieuwe post creation (ROO functie zonder bestaande polder-post).

Alle auto-merges schrijven `sources[]`-entry met `id="roo"` en
`fields=[...]` zodat per-veld provenance behouden blijft.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from polder.fetchers.roo import roo_org_url

logger = logging.getLogger("polder.resolve_roo")

SOURCE_ID = "roo"


def _roo_org_url(parent_roo_id: str | None, parent_org_id: str | None) -> str:
    """Hergebruikt `polder.fetchers.roo.roo_org_url` met polder-slug uit
    `parent_org_id`. Individuele medewerker-URLs bestaan NIET in ROO;
    alle ROO-bron-URLs wijzen naar de org-pagina."""
    slug = (
        parent_org_id[len("org:") :] if parent_org_id and parent_org_id.startswith("org:") else ""
    )
    return roo_org_url(parent_roo_id, slug)


def _atomic_write_yaml(path: Path, data: dict) -> None:
    """Schrijf YAML atomically: tempfile in dezelfde directory + os.replace.

    Voorkomt corrupte files bij crash halverwege en voorkomt no-op writes
    als de inhoud byte-identiek is aan de bestaande file (scheelt git-noise
    + filesystem mtime-churn).
    """
    import os
    import tempfile

    new_text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    if path.exists() and path.read_text(encoding="utf-8") == new_text:
        return  # geen wijziging
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        os.replace(tmp, path)
    except BaseException:
        # Cleanup tempfile als os.replace nog niet plaatsgevonden heeft.
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


# ---------------------------------------------------------------------------
# Name parsing
# ---------------------------------------------------------------------------


_TITLE_RX = re.compile(
    r"^(dhr|mw|mevr|mr|drs|prof|dr|ir|ing|jhr|jkvr|baron|gravin|ds|fr)\.?\s+",
    re.IGNORECASE,
)
# Greedy: pak een sequentie van enkele-letter-initialen (`H.J.`, `B.C.M.`).
# Geen \b voor de start; we accepteren dat de match ook na een spatie of
# string-begin komt, en stoppen wanneer de volgende char geen `<UPPER>.`
# meer is.
_INIT_RX = re.compile(r"(?<![A-Za-z])((?:[A-Z]\.){1,6})")
_NICK_RX = re.compile(r"\([^)]+\)")
# Post-nominalen die we van het einde knippen.
_POSTNOM_RX = re.compile(r"^[A-Z]{2,5}$")


def _ascii_lower(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()


def parse_roo_name(naam: str) -> tuple[str, str]:
    """Parse ROO-medewerker-naam naar (family.lower, init-compact).

    Voorbeelden:
    - `dhr. H.J. (Henkjan) Derks MGM` → (`derks`, `hj`)
    - `Dhr. B.C.M. Vostermans`         → (`vostermans`, `bcm`)
    - `Algemeen IGK`                   → (`algemeen igk`, ``)
    """
    s = naam.strip()
    while True:
        m = _TITLE_RX.match(s)
        if not m:
            break
        s = s[m.end() :]
    s = _NICK_RX.sub("", s).strip()
    init_match = _INIT_RX.search(s)
    init_compact = ""
    if init_match:
        init_compact = re.sub(r"[^A-Za-z]", "", init_match.group(1)).lower()
        family_part = s[init_match.end() :].strip()
    else:
        family_part = s
    parts = family_part.split()
    while parts and _POSTNOM_RX.match(parts[-1]):
        parts.pop()
    family = " ".join(parts)
    return _ascii_lower(family).strip(), init_compact


def compact_initials(init: str | None) -> str:
    if not init:
        return ""
    return re.sub(r"[^A-Za-z]", "", init).lower()


# ---------------------------------------------------------------------------
# Indexes over polder
# ---------------------------------------------------------------------------


@dataclass
class PolderIndex:
    posts_by_id: dict[str, tuple[Path, dict]] = field(default_factory=dict)
    persons_by_id: dict[str, tuple[Path, dict]] = field(default_factory=dict)
    persons_by_family_init: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    persons_by_family_first: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    persons_by_family: dict[str, list[str]] = field(default_factory=dict)


def _slugify_label(label: str) -> str:
    if not label:
        return ""
    s = _ascii_lower(label)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9-]+", "", s)
    return re.sub(r"-+", "-", s).strip("-")


def build_index(data_dir: Path) -> PolderIndex:
    """Bouw lookup-indices voor matching."""
    idx = PolderIndex()

    posts_dir = data_dir / "posten"
    if posts_dir.exists():
        for p in posts_dir.rglob("*.yaml"):
            try:
                with p.open("r", encoding="utf-8") as fh:
                    d = yaml.safe_load(fh) or {}
            except yaml.YAMLError as exc:
                logger.warning("Kan post-yaml niet parsen: %s (%s)", p, exc)
                continue
            if not isinstance(d, dict):
                continue
            pid = d.get("id")
            if isinstance(pid, str):
                idx.posts_by_id[pid] = (p, d)

    persons_dir = data_dir / "personen"
    family_init: dict[tuple[str, str], list[str]] = defaultdict(list)
    family_first: dict[tuple[str, str], list[str]] = defaultdict(list)
    family_only: dict[str, list[str]] = defaultdict(list)
    if persons_dir.exists():
        for p in persons_dir.glob("*.yaml"):
            try:
                with p.open("r", encoding="utf-8") as fh:
                    d = yaml.safe_load(fh) or {}
            except yaml.YAMLError as exc:
                logger.warning("Kan person-yaml niet parsen: %s (%s)", p, exc)
                continue
            if not isinstance(d, dict):
                continue
            pid = d.get("id")
            if not isinstance(pid, str):
                continue
            idx.persons_by_id[pid] = (p, d)
            name = d.get("name") or {}
            family = _ascii_lower(name.get("family") or "").strip()
            if not family:
                continue
            init_full = compact_initials(name.get("initials"))
            given = name.get("given") or ""
            given_init = compact_initials(given) if re.match(r"^[A-Z]\.", given) else ""
            full_init = init_full if len(init_full) >= len(given_init) else given_init

            family_only[family].append(pid)
            if full_init:
                family_first[(family, full_init[:1])].append(pid)
                if len(full_init) >= 2:
                    family_init[(family, full_init)].append(pid)

    idx.persons_by_family_init = dict(family_init)
    idx.persons_by_family_first = dict(family_first)
    idx.persons_by_family = dict(family_only)
    return idx


def find_person(idx: PolderIndex, family: str, init_compact: str) -> tuple[str | None, str]:
    """Geef (person_id, match_kind) terug.

    match_kind ∈ {"family+full-init", "family+first-init", "family-only", "none",
    "ambiguous-init", "ambiguous-first", "ambiguous-family"}.
    """
    if not family:
        return None, "none"
    if init_compact and len(init_compact) >= 2:
        cands = idx.persons_by_family_init.get((family, init_compact))
        if cands:
            if len(cands) == 1:
                return cands[0], "family+full-init"
            return None, "ambiguous-init"
    if init_compact:
        cands = idx.persons_by_family_first.get((family, init_compact[:1]))
        if cands:
            if len(cands) == 1:
                return cands[0], "family+first-init"
            return None, "ambiguous-first"
    cands = idx.persons_by_family.get(family)
    if cands:
        if len(cands) == 1:
            return cands[0], "family-only"
        return None, "ambiguous-family"
    return None, "none"


# ---------------------------------------------------------------------------
# Lane 1: Post enrichment
# ---------------------------------------------------------------------------


def find_post_for_functie(
    idx: PolderIndex, parent_org_id: str | None, functie_naam: str, suggested_post_id: str | None
) -> str | None:
    """Probeer functie naar bestaande polder-post te resolven.

    Regels:
    1. `suggested_post_id` (door fetcher als `post:<role>-<org>` gebouwd) bestaat.
    2. Anders: scan posts met dezelfde org en check of label-slug matcht.
    """
    if suggested_post_id and suggested_post_id in idx.posts_by_id:
        return suggested_post_id
    if not parent_org_id:
        return None
    target = _slugify_label(functie_naam)
    for pid, (_p, d) in idx.posts_by_id.items():
        if d.get("organization_id") != parent_org_id:
            continue
        if _slugify_label(d.get("label", "")) == target:
            return pid
    return None


def enrich_post(
    post_path: Path,
    post_data: dict,
    proposal: dict,
    *,
    today: str,
) -> bool:
    """Voeg ROO-metadata toe aan een post-yaml. Geef True als er iets veranderd is."""
    changed = False
    roo_id = proposal.get("roo_functie_id")
    if roo_id and post_data.get("roo_functie_id") != str(roo_id):
        post_data["roo_functie_id"] = str(roo_id)
        changed = True
    if proposal.get("roo_functie_naam") and not post_data.get("roo_naam"):
        post_data["roo_naam"] = proposal["roo_functie_naam"]
        changed = True
    if not changed:
        return False
    sources = post_data.get("sources") or []
    has_roo_source = any(isinstance(s, dict) and s.get("id") == SOURCE_ID for s in sources)
    if not has_roo_source:
        url = _roo_org_url(proposal.get("parent_roo_id"), post_data.get("organization_id"))
        sources.append(
            {
                "id": SOURCE_ID,
                "url": url,
                "retrieved": today,
                "fields": ["roo_functie_id", "roo_naam"],
            }
        )
        post_data["sources"] = sources
    return True


# ---------------------------------------------------------------------------
# Lane 2: Mandaat bevestiging
# ---------------------------------------------------------------------------


def find_open_mandate(person_data: dict, post_id: str) -> dict | None:
    """Geef het open mandaat op `post_id` voor deze persoon, anders None."""
    for m in person_data.get("mandaten") or []:
        if not isinstance(m, dict):
            continue
        if m.get("post_id") == post_id and m.get("end_date") in (None, ""):
            return m
    return None


def confirm_mandaat(mandaat: dict, proposal: dict, medewerker: dict, *, today: str) -> bool:
    """Voeg ROO-source toe aan mandaat.sources[] met `roo_medewerker_id` als
    fingerprint-veld. Geef True als toegevoegd of geüpgraded.

    URL wijst naar de organisatie-pagina op organisaties.overheid.nl —
    individuele medewerker-URLs bestaan niet als publiek pad. De link tussen
    bron en specifieke medewerker zit in `roo_medewerker_id`.

    Als er al een ROO-source bestaat zonder de medewerker-fingerprint
    (vroegere resolver-run), upgraden we die in-place.
    """
    sysid = medewerker.get("roo_medewerker_id")
    if not sysid:
        return False
    sources = mandaat.get("sources") or []
    fingerprint_field = f"roo_medewerker_id={sysid}"
    url = _roo_org_url(proposal.get("parent_roo_id"), proposal.get("parent_org_id"))

    # Pad 1: bestaande ROO-source met deze fingerprint → niets te doen.
    for s in sources:
        if (
            isinstance(s, dict)
            and s.get("id") == SOURCE_ID
            and fingerprint_field in (s.get("fields") or [])
        ):
            return False

    # Pad 2: bestaande ROO-source ZONDER fingerprint → upgrade in-place.
    for s in sources:
        if isinstance(s, dict) and s.get("id") == SOURCE_ID:
            fields = s.get("fields") or []
            if fingerprint_field not in fields:
                fields.append(fingerprint_field)
            s["fields"] = fields
            # Update URL ook, voor het geval die nog het oude (404) format had.
            s["url"] = url
            s["retrieved"] = today
            return True

    # Pad 3: geen ROO-source → toevoegen.
    sources.append(
        {
            "id": SOURCE_ID,
            "url": url,
            "retrieved": today,
            "fields": ["confirmed_current", fingerprint_field],
        }
    )
    mandaat["sources"] = sources
    return True


# Lane 3 (auto-create mandaat uit ROO-only) is bewust verwijderd: dat
# schond de two-source rule (project-CLAUDE.md — een benoeming vereist
# Staatscourant + ≥1 andere bron, niet ROO alleen). Nieuwe benoemingen
# die ROO als enige bron heeft gaan nu als staging-proposal naar
# `data/_staging/<input>.unresolved.json` (zie de resolve()-loop), waar
# de human-on-the-loop ze met een tweede bron kan bevestigen.


# ---------------------------------------------------------------------------
# Main resolution loop
# ---------------------------------------------------------------------------


@dataclass
class ResolutionStats:
    posts_enriched: int = 0
    mandaten_confirmed: int = 0
    person_not_found: int = 0
    person_ambiguous: int = 0
    post_not_found: int = 0
    skipped_no_org: int = 0
    proposals_to_staging: int = 0


def resolve(
    proposals_file: Path,
    data_dir: Path,
    *,
    dry_run: bool = False,
    today: str | None = None,
) -> tuple[ResolutionStats, list[dict]]:
    today = today or date.today().isoformat()
    with proposals_file.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    proposals = payload.get("proposals") if isinstance(payload, dict) else payload

    idx = build_index(data_dir)
    stats = ResolutionStats()
    staging_proposals: list[dict] = []
    # Track which paths we mutated; data zelf zit al in `idx.{posts,persons}_by_id`.
    dirty_posts: set[Path] = set()
    dirty_persons: set[Path] = set()

    for prop in proposals:
        org_id = prop.get("parent_org_id")
        functie_naam = prop.get("roo_functie_naam") or ""
        suggested = prop.get("suggested_post_id")
        if not org_id:
            stats.skipped_no_org += 1
            continue

        post_id = find_post_for_functie(idx, org_id, functie_naam, suggested)

        if post_id is None:
            stats.post_not_found += 1
            # Push to staging for human/skill review.
            staging_proposals.append({**prop, "_resolution": "no_post_match"})
            stats.proposals_to_staging += 1
            continue

        post_path, post_data = idx.posts_by_id[post_id]
        # Lane 1: enrich post.
        if enrich_post(post_path, post_data, prop, today=today):
            stats.posts_enriched += 1
            dirty_posts.add(post_path)

        # Per-medewerker resolution (lanes 2 + 3).
        for med in prop.get("medewerkers") or []:
            family, init = parse_roo_name(med.get("naam", ""))
            person_id, match_kind = find_person(idx, family, init)
            if person_id is None:
                if match_kind.startswith("ambiguous"):
                    stats.person_ambiguous += 1
                else:
                    stats.person_not_found += 1
                staging_proposals.append(
                    {
                        "roo_functie_id": prop.get("roo_functie_id"),
                        "roo_functie_naam": functie_naam,
                        "post_id": post_id,
                        "parent_org_id": org_id,
                        "medewerker": med,
                        "_resolution": match_kind,
                        "_parsed_family": family,
                        "_parsed_init": init,
                    }
                )
                stats.proposals_to_staging += 1
                continue

            person_path, person_data = idx.persons_by_id[person_id]

            open_m = find_open_mandate(person_data, post_id)
            if open_m is not None:
                # Lane 2: bevestig huidigheid. ROO wordt 2e bron op een
                # mandaat dat al uit Staatscourant/ABD/ORI komt — two-source
                # rule blijft intact (additieve provenance, geen nieuw feit).
                if confirm_mandaat(open_m, prop, med, today=today):
                    stats.mandaten_confirmed += 1
                    dirty_persons.add(person_path)
            else:
                # Geen bestaand mandaat: een NIEUWE benoeming op alleen ROO
                # als bron. De two-source rule (project-CLAUDE.md) verbiedt
                # auto-merge hiervan — Staatscourant + ≥1 andere bron, of
                # Stcrt confidence ≥ 0.98. ROO alleen voldoet niet. Daarom
                # geen auto-create, maar een staging-proposal voor de
                # human-on-the-loop / resolve-staging-proposals-skill.
                stats.proposals_to_staging += 1
                staging_proposals.append(
                    {
                        "roo_functie_id": prop.get("roo_functie_id"),
                        "roo_functie_naam": functie_naam,
                        "post_id": post_id,
                        "parent_org_id": org_id,
                        "resolved_person_id": person_id,
                        "medewerker": med,
                        "_resolution": "new_mandaat_needs_second_source",
                        "confidence": 0.7,
                        "confidence_reasoning": (
                            "ROO is administratieve current-state, geen "
                            "benoemingsbron. Een nieuw mandaat vereist "
                            "Staatscourant + minstens één andere bron "
                            "(two-source rule). ROO-only blijft een proposal."
                        ),
                    }
                )

    if not dry_run:
        # Bouw één keer een path → data lookup uit de bestaande indices.
        post_data_by_path = {p: d for p, d in idx.posts_by_id.values()}
        person_data_by_path = {p: d for p, d in idx.persons_by_id.values()}
        for path in dirty_posts:
            _atomic_write_yaml(path, post_data_by_path[path])
        for path in dirty_persons:
            _atomic_write_yaml(path, person_data_by_path[path])

        if staging_proposals:
            staging_dir = data_dir / "_staging"
            staging_dir.mkdir(parents=True, exist_ok=True)
            target = staging_dir / f"roo-functies-{today}.unresolved.json"
            with target.open("w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "source_id": SOURCE_ID,
                        "retrieved": today,
                        "n_unresolved": len(staging_proposals),
                        "proposals": staging_proposals,
                    },
                    fh,
                    indent=2,
                    ensure_ascii=False,
                )

    return stats, staging_proposals


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polder roo resolve",
        description="Resolve ROO functie/medewerker-proposals naar bestaande posts/personen.",
    )
    parser.add_argument(
        "proposals",
        type=Path,
        help="Pad naar `roo-functies-YYYY-MM-DD.json` (output van `polder roo functies`).",
    )
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    if not args.proposals.exists():
        print(f"Proposals-file bestaat niet: {args.proposals}", file=sys.stderr)
        return 2
    stats, _staging = resolve(args.proposals, args.data, dry_run=args.dry_run)
    print(
        f"=== ROO resolve-stats ({'dry-run' if args.dry_run else 'wrote changes'}) ===",
        file=sys.stderr,
    )
    print(f"  posts enriched:       {stats.posts_enriched}", file=sys.stderr)
    print(f"  mandaten confirmed:   {stats.mandaten_confirmed}", file=sys.stderr)
    print(f"  person not found:     {stats.person_not_found}", file=sys.stderr)
    print(f"  person ambiguous:     {stats.person_ambiguous}", file=sys.stderr)
    print(f"  post not found:       {stats.post_not_found}", file=sys.stderr)
    print(f"  skipped (no org):     {stats.skipped_no_org}", file=sys.stderr)
    print(f"  → staging:            {stats.proposals_to_staging}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
