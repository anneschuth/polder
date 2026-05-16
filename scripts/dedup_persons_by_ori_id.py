"""Merge persoonsrecords die dezelfde ORI-id maar een andere slug hebben.

Opeenvolgende Open Raadsinformatie-fetches parseten dezelfde persoon soms
net anders (initialen, tussenvoegsel), waardoor een tweede record met een
afwijkende slug ontstond i.p.v. een update. De ORI-id (laatste numerieke
segment van de slug) is stabiel, dus gelijke ORI-id = dezelfde bron-
persoon.

Alleen de bewijsbaar veilige subset wordt hier gemerged: zelfde
familienaam en een given-naam die gelijk, een prefix, of leeg is (pure
initialen-drift). Clusters met een afwijkende voor- of achternaam zijn
echte ambiguiteit en blijven met rust (apart issue).

De merge zelf gaat via ``polder merge person`` zodat mandaten en sources
correct samengevoegd worden. Canonical = de slug met de meest complete
naam. ``--apply`` voert de merges echt uit.
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

ORI_ID = re.compile(r"-(\d{6,8})$")
# Bekende ambigue ORI-id's: twee echt verschillende personen op dezelfde
# id (ORI-bronfout), niet mechanisch te mergen.
SKIP_ORI_IDS = {"6699628"}


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
    families = {_norm(_name(d).get("family")) for _, d in records}
    if len(families) != 1:
        return False
    givens = {_norm(_name(d).get("given")) for _, d in records}
    givens.discard("")
    if len(givens) <= 1:
        return True
    # elke given moet prefix-compatibel zijn met de andere (J vs Jan)
    return all(all(a == b or a.startswith(b) or b.startswith(a) for b in givens) for a in givens)


def build_plan(person_dir: Path) -> tuple[list[tuple[str, str]], list[str]]:
    clusters: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for path in sorted(person_dir.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        pid = doc.get("id", "")
        m = ORI_ID.search(pid)
        if m:
            clusters[m.group(1)].append((pid, doc))

    plan: list[tuple[str, str]] = []
    skipped: list[str] = []
    for ori_id, records in clusters.items():
        if len(records) < 2:
            continue
        if ori_id in SKIP_ORI_IDS or not _is_safe_cluster(records):
            skipped.append(ori_id)
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
        for dup, canon in plan[:15]:
            print(f"  {dup} -> {canon}")
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
