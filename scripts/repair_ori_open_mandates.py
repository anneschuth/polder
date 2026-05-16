"""One-off repair for duplicate / misassigned ORI open mandates.

Two bugs in the Open Raadsinformatie fetcher produced bad data before the
fixes in this branch:

1. ``person_to_polder_record`` stamped ``today`` into ``start_date``. The
   merge key was ``(post_id, start_date)``, so every nightly rerun appended
   a fresh open mandate for the same still-current seat.
2. The fetcher iterated over *all* gemeente files, including abolished ones
   (``valid_until`` / ``successor_id`` set), assigning current griffiers and
   secretarissen to gemeenten that no longer exist (Geldrop vs
   Geldrop-Mierlo).

3. ``build_mandaat`` stamped a fresh ``uuid.uuid4()`` per run, so the
   same person on the same seat got a new mandate-id every night even
   when the merge-snap path was never reached (issue #64).

This script rewrites mandates that point at an abolished gemeente to the
successor, rewrites every ORI mandate-id to the deterministic
membership-derived id (so the on-disk data matches what the fixed
fetcher now produces and re-runs are idempotent), then collapses
duplicate open ORI mandates on the same post into one (earliest
start_date wins, sources merged). It never deletes a person record. Run
with ``--apply`` to write; default is a dry run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from polder.fetchers.open_raadsinformatie import _ori_mandaat_id

SOURCE_ID = "open_raadsinformatie"


def _membership_id_from_url(url: str) -> str:
    """`https://id.openraadsinformatie.nl/3067673` -> `3067673`.

    The fetcher builds the ORI source-url as
    ``_ori_url(membership_id)``; the last path segment is the stable
    Membership-`@id`. Backfilling the deterministic mandate-id from this
    means we don't have to re-fetch ORI.
    """
    if "openraadsinformatie.nl/" not in url:
        return ""
    return url.rstrip("/").rsplit("/", 1)[-1]


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def build_successor_map(gem_dir: Path) -> dict[str, str]:
    """Map abolished org-id -> successor org-id (transitively resolved)."""
    direct: dict[str, str] = {}
    for path in gem_dir.glob("*.yaml"):
        doc = _load(path)
        succ = doc.get("successor_id")
        if succ:
            direct[doc["id"]] = succ

    def resolve(org_id: str, _seen: set[str] | None = None) -> str:
        seen = _seen or set()
        while org_id in direct and org_id not in seen:
            seen.add(org_id)
            org_id = direct[org_id]
        return org_id

    return {old: resolve(old) for old in direct}


def _is_ori(mandaat: dict[str, Any]) -> bool:
    return any((s or {}).get("id") == SOURCE_ID for s in (mandaat.get("sources") or []))


def _ori_ref(mandaat: dict[str, Any]) -> str:
    """ORI person-URL: stable identity of the seat behind a mandate."""
    for src in mandaat.get("sources") or []:
        if (src or {}).get("id") == SOURCE_ID and src.get("url"):
            return str(src["url"])
    return ""


def _retarget_post_id(post_id: str, old_org: str, new_org: str) -> str:
    """post:griffier-gemeente-geldrop -> post:griffier-gemeente-geldrop-mierlo.

    Post-slugs encode the gemeente slug as the org-id minus the ``org:``
    prefix. Swap that tail when the org moves to a successor.
    """
    old_tail = old_org.removeprefix("org:")
    new_tail = new_org.removeprefix("org:")
    if post_id.endswith(old_tail):
        return post_id[: -len(old_tail)] + new_tail
    return post_id


def _earliest(*dates: str | None) -> str:
    present = [d for d in dates if d]
    return min(present) if present else ""


def _merge_sources(
    a: list[dict[str, Any]] | None, b: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    by_url: dict[tuple[str, str], dict[str, Any]] = {}
    for src in (a or []) + (b or []):
        key = (src.get("id", ""), src.get("url", ""))
        prev = by_url.get(key)
        if prev is None or (src.get("retrieved", "") > prev.get("retrieved", "")):
            by_url[key] = src
    return list(by_url.values())


def repair_person(doc: dict[str, Any], successor: dict[str, str]) -> bool:
    """Mutate doc in place. Return True if anything changed."""
    mandaten = doc.get("mandaten")
    if not isinstance(mandaten, list):
        return False

    changed = False

    # 1. Retarget abolished-gemeente mandates to the successor.
    for m in mandaten:
        if not isinstance(m, dict):
            continue
        org = m.get("organization_id", "")
        if org in successor:
            new_org = successor[org]
            m["post_id"] = _retarget_post_id(m.get("post_id", ""), org, new_org)
            m["organization_id"] = new_org
            changed = True

    # 1b. Rewrite ORI mandate ids to the deterministic, membership-derived
    #     id (issue #64). Existing records still carry the per-run uuid4;
    #     without this, the next ORI run computes a different id and the
    #     fragile snap-in-merge path has to catch the duplicate again.
    #     Same membership -> same id, so re-runs are now idempotent on disk.
    for m in mandaten:
        if not isinstance(m, dict) or not _is_ori(m):
            continue
        membership_id = _membership_id_from_url(_ori_ref(m))
        new_id = _ori_mandaat_id(membership_id, m.get("organization_id", ""), m.get("post_id", ""))
        if m.get("id") != new_id:
            m["id"] = new_id
            changed = True

    # 2. Collapse duplicate open ORI mandates that are the *same seat*
    #    re-fetched on different days: same post AND same ORI person-URL.
    #    Two genuine council terms on the same post carry different ORI
    #    record-URLs and must stay separate (Bos-Coenraad). Closed and
    #    non-ORI mandates are left untouched.
    open_ori: dict[tuple[str, str], dict[str, Any]] = {}
    keep: list[dict[str, Any]] = []
    for m in mandaten:
        if isinstance(m, dict) and m.get("end_date") is None and _is_ori(m) and m.get("post_id"):
            key = (m["post_id"], _ori_ref(m))
            prev = open_ori.get(key)
            if prev is None:
                open_ori[key] = m
                keep.append(m)
            else:
                prev["start_date"] = _earliest(prev.get("start_date"), m.get("start_date"))
                prev["sources"] = _merge_sources(prev.get("sources"), m.get("sources"))
                changed = True
        else:
            keep.append(m)

    if changed:
        keep.sort(key=lambda m: m.get("start_date", "") if isinstance(m, dict) else "")
        doc["mandaten"] = keep
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--apply", action="store_true", help="write changes to disk")
    args = parser.parse_args(argv)

    gem_dir = args.data_root / "organisaties" / "gemeenten"
    successor = build_successor_map(gem_dir)
    print(f"abolished->successor mappings: {len(successor)}")

    person_dir = args.data_root / "personen"
    touched = 0
    for path in sorted(person_dir.glob("*.yaml")):
        doc = _load(path)
        if repair_person(doc, successor):
            touched += 1
            if args.apply:
                path.write_text(
                    yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                    encoding="utf-8",
                )

    verb = "rewrote" if args.apply else "would rewrite"
    print(f"{verb} {touched} person records")
    if not args.apply:
        print("(dry run — pass --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
