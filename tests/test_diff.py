"""Tests voor src/polder/diff.py."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from polder.diff import (
    canonical,
    compute_diff,
    diff_records,
    is_high_stakes_change,
    main,
)


def test_canonical_dict_key_order_independent() -> None:
    a = {"a": 1, "b": {"x": 1, "y": 2}, "c": [1, 2]}
    b = {"c": [1, 2], "b": {"y": 2, "x": 1}, "a": 1}
    assert canonical(a) == canonical(b)


def test_canonical_list_order_sensitive() -> None:
    assert canonical([1, 2, 3]) != canonical([3, 2, 1])


def test_diff_records_detects_nested_change() -> None:
    before = {"id": "org:x", "names": [{"value": "Oude Naam", "abbr": "X"}]}
    after = {"id": "org:x", "names": [{"value": "Nieuwe Naam", "abbr": "X"}]}
    changed = diff_records(before, after)
    assert "names[0].value" in changed
    assert all("abbr" not in f for f in changed)


def test_diff_records_no_change_on_reordered_keys() -> None:
    before = {"a": 1, "b": 2}
    after = {"b": 2, "a": 1}
    assert diff_records(before, after) == []


def test_high_stakes_valid_until_null_to_date() -> None:
    before = {"valid_until": None}
    after = {"valid_until": "2024-01-01"}
    changed = diff_records(before, after)
    assert is_high_stakes_change(before, after, changed) is True


def test_high_stakes_valid_until_date_to_other_date_not_flagged() -> None:
    before = {"valid_until": "2020-01-01"}
    after = {"valid_until": "2024-01-01"}
    changed = diff_records(before, after)
    assert is_high_stakes_change(before, after, changed) is False


def test_high_stakes_mandaten_change_flagged() -> None:
    before = {"mandaten": [{"role": "SG", "end_date": None}]}
    after = {"mandaten": [{"role": "SG", "end_date": "2026-01-01"}]}
    changed = diff_records(before, after)
    assert is_high_stakes_change(before, after, changed) is True


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def test_compute_diff_new_record_proposed(tmp_path: Path) -> None:
    cache = tmp_path / "_cache"
    data = tmp_path / "data"
    record = {"id": "org:nieuw", "names": [{"value": "Nieuw"}]}
    _write_yaml(cache / "organisaties" / "ministeries" / "nieuw.yaml", record)

    diffs, proposals = compute_diff(cache, data)
    assert diffs == []
    assert len(proposals) == 1
    assert proposals[0]["type"] == "new"
    assert proposals[0]["record"] == record
    assert proposals[0]["path"].endswith("organisaties/ministeries/nieuw.yaml")


def test_compute_diff_equal_records_empty(tmp_path: Path) -> None:
    cache = tmp_path / "_cache"
    data = tmp_path / "data"
    record_a = {"id": "org:x", "a": 1, "b": 2}
    record_b = {"b": 2, "id": "org:x", "a": 1}
    _write_yaml(cache / "x.yaml", record_a)
    _write_yaml(data / "x.yaml", record_b)

    diffs, proposals = compute_diff(cache, data)
    assert diffs == []
    assert proposals == []


def test_compute_diff_modified_record(tmp_path: Path) -> None:
    cache = tmp_path / "_cache"
    data = tmp_path / "data"
    before = {"id": "org:x", "valid_until": None}
    after = {"id": "org:x", "valid_until": "2024-01-01"}
    _write_yaml(data / "x.yaml", before)
    _write_yaml(cache / "x.yaml", after)

    diffs, proposals = compute_diff(cache, data)
    assert proposals == []
    assert len(diffs) == 1
    entry = diffs[0]
    assert entry["type"] == "modified"
    assert entry["high_stakes"] is True
    assert "valid_until" in entry["changed_fields"]
    assert entry["before"] == before
    assert entry["after"] == after


def test_main_writes_outputs(tmp_path: Path) -> None:
    cache = tmp_path / "_cache"
    data = tmp_path / "data"
    _write_yaml(cache / "n.yaml", {"id": "org:n"})
    out = tmp_path / "diff.json"
    proposals = tmp_path / "proposals.json"

    rc = main(
        [
            "--cache",
            str(cache),
            "--data",
            str(data),
            "--out",
            str(out),
            "--proposals",
            str(proposals),
        ]
    )
    assert rc == 0
    assert json.loads(out.read_text()) == []
    proposed = json.loads(proposals.read_text())
    assert len(proposed) == 1
    assert proposed[0]["type"] == "new"
