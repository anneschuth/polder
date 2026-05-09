"""YAML-aware diff-engine: vergelijk records in `_cache/` met `data/`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

JSONScalar = str | int | float | bool | None
JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
Canonical = JSONScalar | tuple[tuple[str, "Canonical"], ...] | tuple["Canonical", ...]


def canonical(obj: JSONValue) -> Canonical:
    """Return a hashable canonical form: dicts become tuple-of-sorted-items, lists stay ordered."""
    if isinstance(obj, dict):
        return tuple(sorted((k, canonical(v)) for k, v in obj.items()))
    if isinstance(obj, list):
        return tuple(canonical(v) for v in obj)
    return obj


def _walk(before: JSONValue, after: JSONValue, prefix: str, out: list[str]) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        keys = set(before.keys()) | set(after.keys())
        for k in sorted(keys):
            child = f"{prefix}.{k}" if prefix else k
            if k not in before or k not in after:
                out.append(child)
                continue
            _walk(before[k], after[k], child, out)
        return
    if isinstance(before, list) and isinstance(after, list):
        max_len = max(len(before), len(after))
        for i in range(max_len):
            child = f"{prefix}[{i}]"
            if i >= len(before) or i >= len(after):
                out.append(child)
                continue
            _walk(before[i], after[i], child, out)
        return
    if canonical(before) != canonical(after):
        out.append(prefix or "$")


def diff_records(before: JSONValue, after: JSONValue) -> list[str]:
    """Return JSONPath-style strings for fields that differ structurally."""
    changed: list[str] = []
    _walk(before, after, "", changed)
    return changed


def _top_level_field(field: str) -> str:
    indices = [field.find(sep) for sep in (".", "[")]
    indices = [i for i in indices if i != -1]
    if not indices:
        return field
    return field[: min(indices)]


def is_high_stakes_change(
    before: JSONValue,
    after: JSONValue,
    changed_fields: list[str],
) -> bool:
    """High-stakes: valid_until null -> date, or any change inside mandaten."""
    for field in changed_fields:
        top = _top_level_field(field)
        if top == "mandaten":
            return True
        if top == "valid_until":
            before_val = before.get("valid_until") if isinstance(before, dict) else None
            after_val = after.get("valid_until") if isinstance(after, dict) else None
            if before_val is None and after_val is not None:
                return True
    return False


def _load_yaml(path: Path) -> JSONValue:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def compute_diff(
    cache_dir: Path,
    data_dir: Path,
) -> tuple[list[dict[str, JSONValue]], list[dict[str, JSONValue]]]:
    """Walk cache_dir, compare against data_dir. Return (diffs, proposals)."""
    diffs: list[dict[str, JSONValue]] = []
    proposals: list[dict[str, JSONValue]] = []

    if not cache_dir.exists():
        return diffs, proposals

    for cache_path in sorted(cache_dir.rglob("*.yaml")):
        rel = cache_path.relative_to(cache_dir)
        data_path = data_dir / rel
        cache_record = _load_yaml(cache_path)

        if not data_path.exists():
            proposals.append(
                {
                    "path": str(data_path),
                    "type": "new",
                    "record": cache_record,
                }
            )
            continue

        data_record = _load_yaml(data_path)
        if canonical(data_record) == canonical(cache_record):
            continue

        changed_fields = diff_records(data_record, cache_record)
        diffs.append(
            {
                "path": str(data_path),
                "type": "modified",
                "changed_fields": changed_fields,
                "high_stakes": is_high_stakes_change(data_record, cache_record, changed_fields),
                "before": data_record,
                "after": cache_record,
            }
        )

    return diffs, proposals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="polder-diff", description=__doc__)
    parser.add_argument("--cache", default="_cache", type=Path)
    parser.add_argument("--data", default="data", type=Path)
    parser.add_argument("--out", default="diff.json", type=Path)
    parser.add_argument("--proposals", default="proposals.json", type=Path)
    args = parser.parse_args(argv)

    diffs, proposals = compute_diff(args.cache, args.data)

    args.out.write_text(json.dumps(diffs, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    args.proposals.write_text(
        json.dumps(proposals, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )

    print(
        f"{len(diffs)} records gewijzigd, {len(proposals)} toegevoegd",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
