"""Tests voor `scripts/fix_tussenvoegsel_as_initial.py`."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPT = _HERE.parent / "scripts" / "fix_tussenvoegsel_as_initial.py"


def _load() -> object:
    spec = importlib.util.spec_from_file_location("fix_tussenvoegsel", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_variant_1_initialen_kop():
    """`given='J.C.M. Van'` -> tussenvoegsel, given verwijderd, initials
    behouden."""
    mod = _load()
    rec = {
        "name": {
            "full": "J.C.M. Van Aelst",
            "family": "Aelst",
            "given": "J.C.M. Van",
            "initials": "J.C.M.",
        }
    }
    changed, _ = mod.fix_record(rec)
    assert changed is True
    assert rec["name"]["tussenvoegsel"] == "van"
    assert rec["name"]["initials"] == "J.C.M."
    assert "given" not in rec["name"]
    assert rec["name"]["full"] == "J.C.M. Van Aelst"
    assert rec["name"]["family"] == "Aelst"


def test_variant_2_roepnaam_kop_strips_leaked_letter():
    """`given='Aly van'`, `initials='A.V.'` -> given='Aly',
    tussenvoegsel='van', initials='A.' (V was gelekt)."""
    mod = _load()
    rec = {
        "name": {
            "full": "Aly van Berckel",
            "family": "Berckel",
            "given": "Aly van",
            "initials": "A.V.",
        }
    }
    changed, _ = mod.fix_record(rec)
    assert changed is True
    assert rec["name"]["given"] == "Aly"
    assert rec["name"]["tussenvoegsel"] == "van"
    assert rec["name"]["initials"] == "A."


def test_variant_2_multiword_tussenvoegsel():
    """`given='Aernout van der'`, `initials='A.V.D.'` -> initials='A.'."""
    mod = _load()
    rec = {
        "name": {
            "full": "Aernout van der Bend",
            "family": "Bend",
            "given": "Aernout van der",
            "initials": "A.V.D.",
        }
    }
    changed, _ = mod.fix_record(rec)
    assert changed is True
    assert rec["name"]["tussenvoegsel"] == "van der"
    assert rec["name"]["initials"] == "A."
    assert rec["name"]["given"] == "Aernout"


def test_variant_2_preserves_genuine_multiletter_initials():
    """Hardening: echte meer-letter-initialen blijven behouden; alleen de
    gelekte tussenvoegsel-letter wordt van het eind gestript.

    `given='Pieter van'`, `initials='P.J.M.V.'` (V gelekt) -> `P.J.M.`,
    niet platgeslagen naar `P.`."""
    mod = _load()
    rec = {
        "name": {
            "full": "P.J.M. van Houten",
            "family": "Houten",
            "given": "Pieter van",
            "initials": "P.J.M.V.",
        }
    }
    changed, _ = mod.fix_record(rec)
    assert changed is True
    assert rec["name"]["initials"] == "P.J.M."
    assert rec["name"]["given"] == "Pieter"


def test_variant_2_fallback_when_initials_unusable():
    """Geen bruikbare initials -> val terug op roepnaam-letter."""
    mod = _load()
    rec = {
        "name": {
            "full": "Aly van Berckel",
            "family": "Berckel",
            "given": "Aly van",
            "initials": "",
        }
    }
    changed, _ = mod.fix_record(rec)
    assert changed is True
    assert rec["name"]["initials"] == "A."


def test_titlecase_does_not_mangle_ij():
    """`str.title()` zou `IJsbrand` -> `Ijsbrand` maken; al-gekapitaliseerde
    roepnaam moet bron-getrouw blijven."""
    mod = _load()
    rec = {
        "name": {
            "full": "IJsbrand de Vries",
            "family": "Vries",
            "given": "IJsbrand de",
            "initials": "I.D.",
        }
    }
    changed, _ = mod.fix_record(rec)
    assert changed is True
    assert rec["name"]["given"] == "IJsbrand"


def test_skips_when_tussenvoegsel_already_set():
    """CAT B (slug-rest): tussenvoegsel al gezet -> niet aanraken."""
    mod = _load()
    rec = {
        "name": {
            "full": "M.J.G.A. van der Avoort",
            "family": "Avoort",
            "given": "M.J.G.A.",
            "initials": "M.J.G.A.",
            "tussenvoegsel": "van der",
        }
    }
    changed, _ = mod.fix_record(rec)
    assert changed is False


def test_skips_party_name_in_family():
    """Partijnaam tussen haakjes = ander bug-patroon, buiten scope."""
    mod = _load()
    rec = {
        "name": {
            "full": "A.V. van Amerongen (Lokaal Belang)",
            "family": "van Amerongen (Lokaal Belang)",
            "given": "A.V. van",
            "initials": "A.V.",
        }
    }
    changed, _ = mod.fix_record(rec)
    assert changed is False


def test_skips_doubled_tussenvoegsel_in_full():
    """`van van` in full = partijnaam-stem-patroon, buiten scope."""
    mod = _load()
    rec = {
        "name": {
            "full": "A.V. van van Amerongen",
            "family": "Amerongen",
            "given": "A.V. van",
            "initials": "A.V.",
        }
    }
    changed, _ = mod.fix_record(rec)
    assert changed is False


def test_skips_compound_head():
    """Samengestelde kop (`Marieke Twigt-Van der`): niet raden."""
    mod = _load()
    rec = {
        "name": {
            "full": "Marieke Twigt-Van der Kaaden",
            "family": "Kaaden",
            "given": "Marieke Twigt-Van der",
            "initials": "M.T.",
        }
    }
    changed, _ = mod.fix_record(rec)
    assert changed is False


def test_skips_when_family_is_tussenvoegsel():
    """Achternaam lijkt zelf een tussenvoegsel -> niet raden, overslaan."""
    mod = _load()
    rec = {
        "name": {
            "full": "J. van der",
            "family": "der",
            "given": "J. van",
            "initials": "J.V.",
        }
    }
    changed, _ = mod.fix_record(rec)
    assert changed is False


def test_no_name_block_returns_false():
    mod = _load()
    changed, _ = mod.fix_record({})
    assert changed is False


def test_nickname_in_full_becomes_given():
    """Variant 1 met roepnaam tussen haakjes -> die wordt `name.given`."""
    mod = _load()
    rec = {
        "name": {
            "full": "Drs. H.A.M. (Hellen) van Dongen",
            "family": "Dongen",
            "given": "H.A.M. van",
            "initials": "H.A.M.",
        }
    }
    changed, _ = mod.fix_record(rec)
    assert changed is True
    assert rec["name"]["given"] == "Hellen"
    assert rec["name"]["initials"] == "H.A.M."
    assert rec["name"]["tussenvoegsel"] == "van"


def test_party_marker_in_full_is_skipped():
    """`(SGP)` is een partij-marker, geen roepnaam -> overslaan."""
    mod = _load()
    rec = {
        "name": {
            "full": "J. van der van der Tang (SGP)",
            "family": "Tang",
            "given": "J. van der",
            "initials": "J.V.D.",
        }
    }
    changed, _ = mod.fix_record(rec)
    assert changed is False


def test_role_marker_in_full_is_skipped():
    """`(raadslid)` is een rol-marker, geen roepnaam -> overslaan."""
    mod = _load()
    rec = {
        "name": {
            "full": "M. de Haan (raadslid)",
            "family": "Haan",
            "given": "M. de",
            "initials": "M.D.",
        }
    }
    changed, _ = mod.fix_record(rec)
    assert changed is False
