"""Diepe data-audit op `data/`.

Detecteert inconsistenties die de JSON-Schema-validator niet vangt:
chronologische impossibilities, orphan-refs, implausible birth-years,
placeholder-strings, quasi-duplicate personen, sentinel-dates uit Wikidata,
en meer.

Eén entrypoint: `run_audit(repo_root)` retourneert een dict van categorie naar
lijst van findings. Geen IO buiten YAML-lezen; printing doet de CLI.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import yaml

# Categorieën met een korte uitleg, voor `polder audit --explain`.
CATEGORY_HELP: dict[str, str] = {
    "yaml_parse_error": "Bestand is geen geldig YAML.",
    "dup_id_orgs": "Twee organisatie-records met dezelfde ID.",
    "dup_id_posts": "Twee post-records met dezelfde ID.",
    "dup_id_persons": "Twee person-records met dezelfde ID.",
    "start_after_end": "Mandaat met start_date later dan end_date (chronologisch onmogelijk).",
    "mandaat_before_age_18": "Mandaat begint voordat de persoon 18 was. Vaak Wikidata-sentinel 1945-01-01.",
    "mandaat_after_age_100": "Mandaat begint na de 100e verjaardag van de persoon.",
    "start_in_future": "Mandaat-start_date ligt in de toekomst. Kan valide zijn (aangekondigde benoeming).",
    "orphan_org_ref": "Mandaat verwijst naar een niet-bestaande organization_id.",
    "orphan_post_ref": "Mandaat verwijst naar een niet-bestaande post_id.",
    "post_orphan_org": "Post verwijst naar een niet-bestaande organization_id.",
    "implausible_birth_year": "Geboortejaar buiten plausibele range (< 1700 of > 2010).",
    "birth_year_not_int": "Geboortejaar is geen integer.",
    "no_sources_orgs": "Organisatie zonder sources[] entry.",
    "no_sources_posts": "Post zonder sources[] entry.",
    "no_sources_persons": "Persoon zonder sources[] entry.",
    "source_no_url_orgs": "Source-entry in organisatie zonder url.",
    "source_no_url_posts": "Source-entry in post zonder url.",
    "source_no_url_persons": "Source-entry in persoon zonder url.",
    "source_no_retrieved_orgs": "Source-entry in organisatie zonder retrieved-datum.",
    "source_no_retrieved_posts": "Source-entry in post zonder retrieved-datum.",
    "source_no_retrieved_persons": "Source-entry in persoon zonder retrieved-datum.",
    "person_no_family_name": "Persoon zonder name.family.",
    "placeholder_in_orgs": "Placeholder-string (onbekend/unknown/null/todo) in organisatie-veld.",
    "placeholder_in_posts": "Placeholder-string in post-veld.",
    "placeholder_in_persons": "Placeholder-string in persoon-veld.",
    "quasi_dup_family_birth": "Twee personen met dezelfde family-name en birth-year (kan naamgenoten zijn).",
    "confidence_out_of_range": "Mandaat-confidence buiten [0, 1] range.",
    "mandaat_org_post_mismatch": "Mandaat-organization_id strookt niet met post.organization_id.",
}


PLACEHOLDER_RX = re.compile(
    r"^(onbekend|unknown|null|none|todo|tbd|n\.?\s*v\.?\s*t\.?|nvt)$",
    re.IGNORECASE,
)


def _safe_load_yaml(path: Path) -> tuple[dict | None, str | None]:
    """Lees YAML, geef (data, error_message) terug."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data, None
    except yaml.YAMLError as e:
        return None, str(e)
    except OSError as e:
        return None, str(e)


def _load_all(
    data_dir: Path,
) -> tuple[
    list[tuple[Path, dict]],
    list[tuple[Path, dict]],
    list[tuple[Path, dict]],
    list[tuple[Path, str]],
]:
    """Lees alle org/post/person yaml-bestanden. Retourneer ook parse-errors."""
    orgs, posts, persons = [], [], []
    parse_errors: list[tuple[Path, str]] = []

    for glob, target in (
        ("organisaties/**/*.yaml", orgs),
        ("posten/**/*.yaml", posts),
        ("personen/**/*.yaml", persons),
    ):
        for p in data_dir.glob(glob):
            data, err = _safe_load_yaml(p)
            if err:
                parse_errors.append((p, err))
                continue
            if not isinstance(data, dict):
                continue
            target.append((p, data))

    return orgs, posts, persons, parse_errors


def run_audit(data_dir: Path, *, today: str | None = None) -> dict[str, list[str]]:
    """Voer de hele audit uit en retourneer findings per categorie.

    `today` defaultt op vandaag in ISO-formaat; injecteerbaar voor tests.
    """
    today = today or date.today().isoformat()
    orgs, posts, persons, parse_errors = _load_all(data_dir)

    issues: dict[str, list[str]] = defaultdict(list)

    for path, err in parse_errors:
        issues["yaml_parse_error"].append(f"{path.name}: {err}")

    # Bouw ID-indexes
    all_org_ids: set[str] = set()
    all_post_ids: set[str] = set()
    all_person_ids: set[str] = set()
    post_to_org: dict[str, str] = {}
    for _, d in orgs:
        if d.get("id"):
            all_org_ids.add(d["id"])
    for _, d in posts:
        if d.get("id"):
            all_post_ids.add(d["id"])
            if d.get("organization_id"):
                post_to_org[d["id"]] = d["organization_id"]
    for _, d in persons:
        if d.get("id"):
            all_person_ids.add(d["id"])

    # 1. Dup IDs
    for label, items in [("orgs", orgs), ("posts", posts), ("persons", persons)]:
        c: Counter[str] = Counter()
        for _, d in items:
            if d.get("id"):
                c[d["id"]] += 1
        for dup_id, n in c.items():
            if n > 1:
                issues[f"dup_id_{label}"].append(f"{dup_id} ({n}x)")

    # 2. Mandaat-checks per persoon
    for p, d in persons:
        for m in d.get("mandaten") or []:
            if not isinstance(m, dict):
                continue
            _check_mandaat(p, d, m, today, all_org_ids, all_post_ids, post_to_org, issues)

    # 3. Posts met orphan org-refs
    for p, d in posts:
        org = d.get("organization_id")
        if org and org not in all_org_ids:
            issues["post_orphan_org"].append(f"{p.name}: org_id={org}")

    # 4. Birth-year sanity
    for p, d in persons:
        birth = d.get("birth")
        if not isinstance(birth, dict) or birth.get("year") is None:
            continue
        y = birth["year"]
        if not isinstance(y, int):
            issues["birth_year_not_int"].append(f"{p.name}: birth.year={y!r}")
            continue
        if y < 1700 or y > 2010:
            issues["implausible_birth_year"].append(f"{p.name}: birth.year={y}")

    # 5. Sources presence + integrity
    for label, items in [("orgs", orgs), ("posts", posts), ("persons", persons)]:
        for p, d in items:
            srcs = d.get("sources") or []
            if not srcs:
                issues[f"no_sources_{label}"].append(p.name)
            for src in srcs:
                if not isinstance(src, dict):
                    continue
                if not src.get("url"):
                    issues[f"source_no_url_{label}"].append(f"{p.name}: {src.get('id', '?')}")
                if not src.get("retrieved"):
                    issues[f"source_no_retrieved_{label}"].append(
                        f"{p.name}: {src.get('id', '?')}"
                    )

    # 6. Persoon-naam
    for p, d in persons:
        name = d.get("name")
        if not isinstance(name, dict) or not name.get("family"):
            issues["person_no_family_name"].append(p.name)

    # 7. Placeholders
    for label, items in [("orgs", orgs), ("posts", posts), ("persons", persons)]:
        for p, d in items:
            for key in ("id", "name", "label", "organization_id", "post_id"):
                val = d.get(key)
                if isinstance(val, str) and PLACEHOLDER_RX.match(val.strip()):
                    issues[f"placeholder_in_{label}"].append(f"{p.name}: {key}={val!r}")
            if isinstance(d.get("name"), dict):
                for nk, nv in d["name"].items():
                    if isinstance(nv, str) and PLACEHOLDER_RX.match(nv.strip()):
                        issues[f"placeholder_in_{label}"].append(f"{p.name}: name.{nk}={nv!r}")

    # 8. Quasi-duplicate personen
    fam_year: dict[tuple[str, int], list[str]] = defaultdict(list)
    for p, d in persons:
        name = d.get("name") or {}
        birth = d.get("birth") or {}
        family = name.get("family")
        initials = name.get("initials")
        y = birth.get("year")
        if family and isinstance(y, int):
            key = (str(family).lower(), y)
            fam_year[key].append(f"{p.name} (initials={initials!r})")
    for key, files in fam_year.items():
        if len(files) > 1:
            issues["quasi_dup_family_birth"].append(f"{key}: {len(files)} records -> {files[:3]}")

    return dict(issues)


def _check_mandaat(
    p: Path,
    d: dict,
    m: dict,
    today: str,
    all_org_ids: set[str],
    all_post_ids: set[str],
    post_to_org: dict[str, str],
    issues: dict[str, list[str]],
) -> None:
    """Run alle per-mandaat checks. Schrijft naar issues."""
    s = m.get("start_date")
    e = m.get("end_date")

    if isinstance(s, str) and isinstance(e, str) and s > e:
        issues["start_after_end"].append(f"{p.name}: {m.get('id', '?')} start={s} end={e}")

    if isinstance(s, str) and s > today:
        issues["start_in_future"].append(f"{p.name}: start={s}")

    birth = d.get("birth")
    if isinstance(birth, dict) and isinstance(birth.get("year"), int) and isinstance(s, str):
        try:
            start_year = int(s[:4])
            age = start_year - birth["year"]
            if age < 18:
                issues["mandaat_before_age_18"].append(
                    f"{p.name}: birth={birth['year']} start={s} ({age}y old)"
                )
            elif age > 100:
                issues["mandaat_after_age_100"].append(
                    f"{p.name}: birth={birth['year']} start={s} ({age}y old)"
                )
        except (ValueError, TypeError):
            pass

    org = m.get("organization_id")
    post = m.get("post_id")
    if org and org not in all_org_ids:
        issues["orphan_org_ref"].append(f"{p.name}: org_id={org}")
    if post and post not in all_post_ids:
        issues["orphan_post_ref"].append(f"{p.name}: post_id={post}")

    if org and post and post in post_to_org and post_to_org[post] != org:
        issues["mandaat_org_post_mismatch"].append(
            f"{p.name}: m.org={org} post.org={post_to_org[post]} post={post}"
        )

    c = m.get("confidence")
    if isinstance(c, (int, float)) and (c < 0 or c > 1):
        issues["confidence_out_of_range"].append(f"{p.name}: confidence={c}")


def summary(issues: dict[str, list[str]]) -> tuple[int, int]:
    """Geef (totaal_categorieën, totaal_findings) terug."""
    return len(issues), sum(len(v) for v in issues.values())
