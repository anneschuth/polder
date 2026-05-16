"""Merge persoonsrecords die een open ORI-mandaat met dezelfde id delen.

Zusterscript van ``dedup_persons_by_ori_id.py``. Dat script matcht op de
ORI-id in de slug; deze matcht op de deterministische mandaat-id die de
ORI-fetcher sinds issue #64 uit de stabiele Membership-`@id` afleidt.

Twee person-records die op een open mandaat dezelfde
``mandate-ori-<hash>`` dragen, verwijzen per definitie naar dezelfde ORI
Membership: dezelfde fysieke persoon op dezelfde zetel. Dat is het harde,
niet-naam-gebaseerde signaal dat issue #55 als merge-voorwaarde stelt en
dat de UUID-fallback-slugs (``person:opstraat-59bc7f2a`` vs
``person:opstraat-a-0d9235a7``) zelf niet leveren.

De familienaam-gate uit het zusterscript blijft gelden: clusters met een
afwijkende voor- of achternaam zijn echte ambiguiteit (twee mensen op
dezelfde zetel via een ORI-bronfout) en blijven met rust. De merge zelf
gaat via ``polder merge person`` zodat mandaten en sources correct
samenvloeien. Canonical = het record met de meest complete naam.
``--apply`` voert de merges echt uit.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

SOURCE_ID = "open_raadsinformatie"


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z]", "", (value or "").lower())


def _name(doc: dict[str, Any]) -> dict[str, Any]:
    return doc.get("name") or {}


def _completeness(pid: str, doc: dict[str, Any]) -> tuple[int, int, int, str]:
    """Hoger = completer record; canonical-keuze."""
    name = _name(doc)
    return (
        len(name.get("given") or ""),
        len(str(name.get("initials") or "")),
        len(doc.get("mandaten") or []),
        pid,  # deterministische tie-break
    )


def _is_safe_cluster(records: list[tuple[str, dict[str, Any]]]) -> bool:
    """Veilig te mergen als de familienaam gelijk is en óf de given-namen
    prefix-compatibel zijn (initialen-drift), óf alle leden exact dezelfde
    ORI bron-URL dragen.

    Dat laatste is een onafhankelijk hard signaal: een identieke
    ``id.openraadsinformatie.nl/<membership_id>`` betekent één en hetzelfde
    ORI Membership-record. ORI's parser splitst de naam van diezelfde
    persoon soms verschillend tussen fetches (``Han Boer`` vs ``J.B.
    Boer``, een tussenvoegsel of fractietag die in het given-veld lekt),
    waardoor de given-vergelijking faalt op ruis terwijl het bewijsbaar
    dezelfde persoon is. Twee écht verschillende personen die door een
    ORI-bronfout dezelfde id kregen hebben verschillende membership-URLs
    en blijven zo terecht buiten de merge."""
    families = {_norm(_name(d).get("family")) for _, d in records}
    if len(families) != 1:
        return False
    # URL-signaal: alle leden delen minstens één ORI bron-URL én geen
    # enkel lid heeft een URL die een ander lid tegenspreekt. Concreet:
    # de doorsnede is niet-leeg en elk lid is een superset daarvan. Een
    # asymmetrisch geval ({X} vs {X,Y}) is legitiem — dezelfde persoon
    # waarvan één duplicaat-record een extra zetel Y kent. Twee echt
    # verschillende personen die door een ORI-bronfout een id delen
    # hebben géén gedeelde membership-URL (lege doorsnede) en vallen zo
    # buiten de merge.
    url_sets = [_ori_source_urls(d) for _, d in records]
    if url_sets and set.intersection(*url_sets):
        return True
    givens = {_norm(_name(d).get("given")) for _, d in records}
    givens.discard("")
    if len(givens) <= 1:
        return True
    return all(all(a == b or a.startswith(b) or b.startswith(a) for b in givens) for a in givens)


def _ori_source_urls(doc: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for m in doc.get("mandaten") or []:
        for s in m.get("sources") or []:
            if (s or {}).get("id") == SOURCE_ID and s.get("url"):
                out.add(str(s["url"]))
    return out


def _open_ori_mandate_ids(doc: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for m in doc.get("mandaten") or []:
        if not isinstance(m, dict) or m.get("end_date") is not None:
            continue
        if any((s or {}).get("id") == SOURCE_ID for s in (m.get("sources") or [])):
            mid = m.get("id")
            if mid:
                out.add(str(mid))
    return out


def build_plan(person_dir: Path) -> tuple[list[tuple[str, str]], list[str]]:
    docs: dict[str, dict[str, Any]] = {}
    by_mandate: dict[str, set[str]] = defaultdict(set)
    for path in sorted(person_dir.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        pid = doc.get("id", "")
        if not pid:
            continue
        docs[pid] = doc
        for mid in _open_ori_mandate_ids(doc):
            by_mandate[mid].add(pid)

    # Connected components: person-ids verbonden via een gedeelde
    # mandaat-id (een persoon kan via meerdere zetels aan een cluster
    # vastzitten).
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for pids in by_mandate.values():
        pids_list = sorted(pids)
        for other in pids_list[1:]:
            union(pids_list[0], other)

    clusters: dict[str, list[str]] = defaultdict(list)
    for pid in parent:
        clusters[find(pid)].append(pid)

    plan: list[tuple[str, str]] = []
    skipped: list[str] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        # Een connected component kan via een ORI-bronfout-id (één id,
        # twee echte personen) twee families overbruggen. Beoordeel
        # daarom per familienaam: de veilige subfamilie merget alsnog,
        # alleen het echt-ambigue deel blijft over i.p.v. de hele
        # component weg te gooien.
        by_family: dict[str, list[str]] = defaultdict(list)
        for pid in members:
            by_family[_norm(_name(docs[pid]).get("family"))].append(pid)
        for fam_members in by_family.values():
            if len(fam_members) < 2:
                continue
            records = [(pid, docs[pid]) for pid in fam_members]
            if not _is_safe_cluster(records):
                skipped.append(" + ".join(sorted(fam_members)))
                continue
            ranked = sorted(records, key=lambda t: _completeness(t[0], t[1]), reverse=True)
            canonical = ranked[0][0]
            for pid, _ in ranked[1:]:
                plan.append((pid, canonical))
    return plan, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    plan, skipped = build_plan(args.data_root / "personen")
    print(f"veilige merges: {len(plan)}  overgeslagen clusters (ambigu): {len(skipped)}")

    if not args.apply:
        for dup, canon in plan[:20]:
            print(f"  {dup} -> {canon}")
        for s in skipped[:10]:
            print(f"  SKIP (ambigu): {s}")
        print("(dry run — gebruik --apply om de merges uit te voeren)")
        return 0

    done = failed = 0
    for dup, canon in plan:
        result = subprocess.run(
            [
                "uv",
                "run",
                "polder",
                "merge",
                "person",
                dup,
                canon,
                "--data",
                str(args.data_root),
                "--apply",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            done += 1
        else:
            failed += 1
            print(f"FAILED {dup} -> {canon}: {result.stderr.strip()[:200]}")
    print(f"gemerged: {done}, mislukt: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
