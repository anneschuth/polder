"""Tests voor `scripts/upgrade_initials_from_given.py`."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPT = _HERE.parent / "scripts" / "upgrade_initials_from_given.py"


def _load() -> object:
    spec = importlib.util.spec_from_file_location("upgrade_initials", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_upgrade_record_uses_given_when_initials_short():
    mod = _load()
    rec = {"name": {"family": "Derks", "initials": "H.", "given": "H.J."}}
    changed, new = mod.upgrade_record(rec)
    assert changed is True
    assert new == "H.J."
    assert rec["name"]["initials"] == "H.J."


def test_upgrade_record_skips_when_initials_already_full():
    mod = _load()
    rec = {"name": {"family": "Derks", "initials": "H.J.", "given": "Henkjan"}}
    changed, _ = mod.upgrade_record(rec)
    assert changed is False


def test_upgrade_record_skips_when_given_is_roepnaam():
    """`given='Henkjan'` is geen initial-sequence; mag niet leiden tot
    upgrade. Anders zouden roepnamen verkeerd geinterpreteerd worden."""
    mod = _load()
    rec = {"name": {"family": "Derks", "initials": "H.", "given": "Henkjan"}}
    changed, _ = mod.upgrade_record(rec)
    assert changed is False


def test_upgrade_record_uses_full_field_as_fallback():
    """`name.full` met initials-prefix kan ook als bron dienen."""
    mod = _load()
    rec = {
        "name": {
            "family": "Derks",
            "initials": "H.",
            "given": "",
            "full": "H.J. Derks",
        }
    }
    changed, new = mod.upgrade_record(rec)
    assert changed is True
    assert new == "H.J."


def test_upgrade_record_no_name_block_returns_false():
    mod = _load()
    changed, _ = mod.upgrade_record({})
    assert changed is False
